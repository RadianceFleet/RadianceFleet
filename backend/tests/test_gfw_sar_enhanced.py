"""Tests for GFW SAR incremental corridor sweeping enhancements."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.corridor import Corridor
from app.models.stubs import DarkVesselDetection


@pytest.fixture()
def db():
    """In-memory SQLite session with required tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_corridor(db: Session, corridor_id: int = 1, name: str = "Test Corridor") -> Corridor:
    """Insert a corridor with a simple WKT polygon."""
    c = Corridor(
        corridor_id=corridor_id,
        name=name,
        corridor_type="export_route",
        geometry="POLYGON((25 55, 30 55, 30 60, 25 60, 25 55))",
    )
    db.add(c)
    db.commit()
    return c


def _make_detection(
    db: Session,
    corridor_id: int,
    scene_id: str = "gfw-sar-2026-01-01-57.0000-27.0000",
    lat: float = 57.0,
    lon: float = 27.0,
    detection_time: datetime | None = None,
    confidence: float | None = None,
) -> DarkVesselDetection:
    """Insert a DarkVesselDetection row."""
    det = DarkVesselDetection(
        scene_id=scene_id,
        detection_lat=lat,
        detection_lon=lon,
        detection_time_utc=detection_time or datetime.utcnow(),
        corridor_id=corridor_id,
        model_confidence=confidence,
    )
    db.add(det)
    db.commit()
    return det


# ── Test: incremental sweep skips recently-swept corridors ────────────


@patch("app.modules.gfw_client.get_sar_detections")
def test_incremental_sweep_skips_recently_swept(mock_get_sar, db):
    """Corridors swept within GFW_SAR_SWEEP_INTERVAL_HOURS should be skipped."""
    corridor = _make_corridor(db)
    # Recent detection — within default 24h window
    _make_detection(db, corridor.corridor_id, detection_time=datetime.utcnow() - timedelta(hours=2))

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["corridors_skipped"] == 1
    assert result["corridors_swept"] == 0
    mock_get_sar.assert_not_called()


# ── Test: confidence filtering at threshold ───────────────────────────


@patch("app.modules.gfw_client.import_sar_detections_to_db")
@patch("app.modules.gfw_client.get_sar_detections")
def test_confidence_filtering_at_threshold(mock_get_sar, mock_import, db):
    """Detections with confidence below threshold should be filtered out."""
    corridor = _make_corridor(db)

    mock_get_sar.return_value = [
        {
            "scene_id": "gfw-sar-2026-01-15-57.0000-27.0000",
            "detection_lat": 57.0,
            "detection_lon": 27.0,
            "detection_time_utc": "2026-01-15",
            "model_confidence": 0.8,
        },
        {
            "scene_id": "gfw-sar-2026-01-15-57.1000-27.1000",
            "detection_lat": 57.1,
            "detection_lon": 27.1,
            "detection_time_utc": "2026-01-15",
            "model_confidence": 0.3,  # Below threshold
        },
    ]
    mock_import.return_value = {"dark": 1, "matched": 0, "total": 1, "rejected": 0}

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["detections_filtered"] == 1
    assert result["corridors_swept"] == 1
    # Only 1 detection should have been imported (the 0.8 confidence one)
    assert mock_import.call_count == 1


# ── Test: compound dedup key (scene_id + lat + lon) ──────────────────


@patch("app.modules.gfw_client.import_sar_detections_to_db")
@patch("app.modules.gfw_client.get_sar_detections")
def test_compound_dedup_key(mock_get_sar, mock_import, db):
    """Detections with same scene_id+lat+lon should be deduped."""
    corridor = _make_corridor(db)
    # Pre-existing detection with same compound key
    _make_detection(
        db,
        corridor.corridor_id,
        scene_id="gfw-sar-2026-01-15-57.0000-27.0000",
        lat=57.0,
        lon=27.0,
    )

    mock_get_sar.return_value = [
        {
            "scene_id": "gfw-sar-2026-01-15-57.0000-27.0000",
            "detection_lat": 57.0,
            "detection_lon": 27.0,
            "detection_time_utc": "2026-01-15",
            "model_confidence": 0.9,
        },
    ]
    mock_import.return_value = {"dark": 0, "matched": 0, "total": 0, "rejected": 0}

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    # The duplicate should not be imported
    mock_import.assert_not_called()


# ── Test: sweep interval config ───────────────────────────────────────


@patch("app.modules.gfw_client.import_sar_detections_to_db")
@patch("app.modules.gfw_client.get_sar_detections")
def test_sweep_interval_config(mock_get_sar, mock_import, db):
    """Custom sweep interval should control when corridors are re-swept."""
    corridor = _make_corridor(db)
    # Detection from 5 hours ago
    _make_detection(db, corridor.corridor_id, detection_time=datetime.utcnow() - timedelta(hours=5))

    mock_get_sar.return_value = []

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 4  # Short interval — 5h ago is stale
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    # Corridor should be swept because 5h > 4h interval
    assert result["corridors_swept"] == 1
    assert result["corridors_skipped"] == 0


# ── Test: all corridors swept (nothing to do) ─────────────────────────


@patch("app.modules.gfw_client.get_sar_detections")
def test_all_corridors_recently_swept(mock_get_sar, db):
    """When all corridors were recently swept, nothing should be done."""
    c1 = _make_corridor(db, corridor_id=1, name="C1")
    c2 = _make_corridor(db, corridor_id=2, name="C2")
    _make_detection(db, c1.corridor_id, detection_time=datetime.utcnow() - timedelta(hours=1))
    _make_detection(
        db,
        c2.corridor_id,
        scene_id="gfw-sar-2026-01-02-58.0000-28.0000",
        lat=58.0,
        lon=28.0,
        detection_time=datetime.utcnow() - timedelta(hours=1),
    )

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["corridors_swept"] == 0
    assert result["corridors_skipped"] == 2
    mock_get_sar.assert_not_called()


# ── Test: confidence below threshold filtered out ─────────────────────


@patch("app.modules.gfw_client.import_sar_detections_to_db")
@patch("app.modules.gfw_client.get_sar_detections")
def test_confidence_below_threshold_filtered(mock_get_sar, mock_import, db):
    """All detections below confidence threshold should be filtered."""
    corridor = _make_corridor(db)

    mock_get_sar.return_value = [
        {
            "scene_id": "gfw-sar-2026-01-15-57.0000-27.0000",
            "detection_lat": 57.0,
            "detection_lon": 27.0,
            "detection_time_utc": "2026-01-15",
            "model_confidence": 0.2,
        },
        {
            "scene_id": "gfw-sar-2026-01-15-57.1000-27.1000",
            "detection_lat": 57.1,
            "detection_lon": 27.1,
            "detection_time_utc": "2026-01-15",
            "model_confidence": 0.4,
        },
    ]

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["detections_filtered"] == 2
    assert result["detections_imported"] == 0
    mock_import.assert_not_called()


# ── Test: empty corridor list ─────────────────────────────────────────


def test_empty_corridor_list(db):
    """When no corridors exist, should return empty summary."""
    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["corridors_swept"] == 0
    assert result["corridors_skipped"] == 0
    assert result["detections_imported"] == 0
    assert result["detections_filtered"] == 0


# ── Test: GFW API error handling ──────────────────────────────────────


@patch("app.modules.gfw_client.get_sar_detections")
def test_gfw_api_error_handling(mock_get_sar, db):
    """API errors should be caught and recorded, not crash the sweep."""
    _make_corridor(db, corridor_id=1, name="C1")
    _make_corridor(db, corridor_id=2, name="C2")

    mock_get_sar.side_effect = Exception("GFW API timeout")

    with patch("app.modules.gfw_client.settings") as mock_settings:
        mock_settings.GFW_API_TOKEN = "fake-token"
        mock_settings.GFW_SAR_SWEEP_INTERVAL_HOURS = 24
        mock_settings.GFW_SAR_MIN_CONFIDENCE = 0.5

        from app.modules.gfw_client import sweep_corridors_sar_incremental

        result = sweep_corridors_sar_incremental(db, "2026-01-01", "2026-01-31", token="fake")

    assert result["corridors_swept"] == 0
    assert len(result["errors"]) == 2
    assert result["detections_imported"] == 0
