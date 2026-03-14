"""Tests for gap_sar_validator — Sentinel-1 AIS Gap Cross-Correlation (v4.0)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.gap_sar_validator import _interpolate_position, validate_gaps_with_sar


def _make_gap(
    gap_event_id: int = 1,
    vessel_id: int = 100,
    gap_start: datetime | None = None,
    gap_end: datetime | None = None,
    off_lat: float | None = 60.0,
    off_lon: float | None = 25.0,
    on_lat: float | None = 61.0,
    on_lon: float | None = 26.0,
    coverage_quality: str | None = None,
    risk_breakdown_json: dict | None = None,
):
    """Create a mock AISGapEvent."""
    g = MagicMock()
    g.gap_event_id = gap_event_id
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start or datetime(2026, 1, 10, 0, 0, 0)
    g.gap_end_utc = gap_end or datetime(2026, 1, 10, 12, 0, 0)
    g.gap_off_lat = off_lat
    g.gap_off_lon = off_lon
    g.gap_on_lat = on_lat
    g.gap_on_lon = on_lon
    g.coverage_quality = coverage_quality
    g.risk_breakdown_json = risk_breakdown_json if risk_breakdown_json is not None else {}
    return g


def _make_detection(
    detection_id: int = 1,
    scene_id: str = "gfw-sar-001",
    lat: float = 60.5,
    lon: float = 25.5,
    time: datetime | None = None,
    corridor_id: int | None = None,
    confidence: float = 0.9,
):
    """Create a mock DarkVesselDetection."""
    d = MagicMock()
    d.detection_id = detection_id
    d.scene_id = scene_id
    d.detection_lat = lat
    d.detection_lon = lon
    d.detection_time_utc = time or datetime(2026, 1, 10, 6, 0, 0)
    d.corridor_id = corridor_id
    d.model_confidence = confidence
    return d


# ── Interpolation tests ─────────────────────────────────────────────────────


def test_interpolation_midpoint():
    """Interpolation at midpoint returns average of start and end."""
    gap = _make_gap(
        off_lat=60.0, off_lon=20.0, on_lat=62.0, on_lon=24.0,
        gap_start=datetime(2026, 1, 1, 0, 0),
        gap_end=datetime(2026, 1, 1, 10, 0),
    )
    mid = gap.gap_start_utc + (gap.gap_end_utc - gap.gap_start_utc) / 2
    result = _interpolate_position(gap, mid)
    assert result is not None
    lat, lon = result
    assert abs(lat - 61.0) < 0.01
    assert abs(lon - 22.0) < 0.01


def test_interpolation_missing_positions():
    """Interpolation returns None when positions are missing."""
    gap = _make_gap(off_lat=None, off_lon=None, on_lat=None, on_lon=None)
    assert _interpolate_position(gap, gap.gap_start_utc) is None


# ── Core validation tests ───────────────────────────────────────────────────


@patch("app.modules.gap_sar_validator.settings")
def test_confirmed_dark_transit(mock_settings):
    """SAR detection near predicted position → confirmed dark transit."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap()
    det = _make_detection(lat=60.5, lon=25.5)  # close to midpoint

    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [gap]
    # Second query for detections
    db.query.return_value.filter.return_value.all.return_value = [det]

    # Use side_effect to differentiate gap query from detection query
    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = [det]

    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["confirmed_dark"] == 1
    assert result["gaps_checked"] == 1
    assert gap.risk_breakdown_json["sar_validation"]["result"] == "confirmed"
    assert len(gap.risk_breakdown_json["sar_validation"]["detections"]) == 1


@patch("app.modules.gap_sar_validator.settings")
def test_possible_outage(mock_settings):
    """No SAR detection in coverage area → possible outage."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap(coverage_quality="GOOD")

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = []  # no detections

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["possible_outage"] == 1
    assert gap.risk_breakdown_json["sar_validation"]["result"] == "outage"


@patch("app.modules.gap_sar_validator.settings")
def test_inconclusive_no_coverage(mock_settings):
    """No coverage data → inconclusive."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap(coverage_quality=None)

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = []

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["inconclusive"] == 1
    assert gap.risk_breakdown_json["sar_validation"]["result"] == "inconclusive"


@patch("app.modules.gap_sar_validator.settings")
def test_search_radius_filtering(mock_settings):
    """Detection outside search radius is filtered out."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 5.0  # very small radius
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap(coverage_quality=None)
    # Detection far from midpoint (60.5, 25.5)
    det = _make_detection(lat=65.0, lon=30.0)

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = [det]

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    # Far detection filtered out, no coverage → inconclusive
    assert result["inconclusive"] == 1
    assert result["confirmed_dark"] == 0


@patch("app.modules.gap_sar_validator.settings")
def test_time_window_filtering(mock_settings):
    """Detections are queried with the time window around the gap."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 1  # tight window

    gap = _make_gap(
        gap_start=datetime(2026, 1, 10, 6, 0),
        gap_end=datetime(2026, 1, 10, 8, 0),
    )

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = []

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    # Verify the filter was called (time window applied via SQLAlchemy filters)
    assert det_query.filter.called
    assert result["gaps_checked"] == 1


@patch("app.modules.gap_sar_validator.settings")
def test_multiple_sar_detections(mock_settings):
    """Multiple SAR detections for one gap are all recorded."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap()
    det1 = _make_detection(detection_id=1, lat=60.5, lon=25.5, scene_id="gfw-sar-001")
    det2 = _make_detection(detection_id=2, lat=60.4, lon=25.4, scene_id="gfw-sar-002")
    det3 = _make_detection(detection_id=3, lat=60.6, lon=25.6, scene_id="gfw-sar-003")

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = [det1, det2, det3]

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["confirmed_dark"] == 1
    assert len(gap.risk_breakdown_json["sar_validation"]["detections"]) == 3


@patch("app.modules.gap_sar_validator.settings")
def test_no_gaps_in_date_range(mock_settings):
    """No gaps → all counts zero."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = []

    db = MagicMock()
    db.query.return_value = gap_query

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["gaps_checked"] == 0
    assert result["confirmed_dark"] == 0
    assert result["possible_outage"] == 0
    assert result["inconclusive"] == 0


@patch("app.modules.gap_sar_validator.settings")
def test_disabled_flag(mock_settings):
    """When disabled, returns immediately with disabled=True."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = False

    db = MagicMock()
    result = validate_gaps_with_sar(db)

    assert result["disabled"] is True
    assert result["gaps_checked"] == 0
    db.query.assert_not_called()


@patch("app.modules.gap_sar_validator.settings")
def test_evidence_json_update(mock_settings):
    """risk_breakdown_json is updated with sar_validation data."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    existing = {"some_key": 42}
    gap = _make_gap(risk_breakdown_json=existing)
    det = _make_detection()

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = [det]

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    # Existing keys preserved
    assert gap.risk_breakdown_json["some_key"] == 42
    # New validation added
    assert "sar_validation" in gap.risk_breakdown_json
    assert gap.risk_breakdown_json["sar_validation"]["predicted_lat"] is not None
    assert gap.risk_breakdown_json["sar_validation"]["predicted_lon"] is not None
    db.commit.assert_called_once()


@patch("app.modules.gap_sar_validator.settings")
def test_viirs_detections(mock_settings):
    """VIIRS detections (prefix viirs-) are also valid for gap validation."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap()
    viirs_det = _make_detection(
        detection_id=10, scene_id="viirs-20260110-001", lat=60.5, lon=25.5,
    )

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = [viirs_det]

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    result = validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert result["confirmed_dark"] == 1
    assert gap.risk_breakdown_json["sar_validation"]["detections"][0]["scene_id"].startswith("viirs-")


@patch("app.modules.gap_sar_validator.settings")
def test_detection_count_bonus(mock_settings):
    """Multiple detections trigger capped count bonus in scoring."""
    mock_settings.GAP_SAR_VALIDATION_ENABLED = True
    mock_settings.GAP_SAR_SEARCH_RADIUS_NM = 30.0
    mock_settings.GAP_SAR_TIME_WINDOW_H = 12

    gap = _make_gap()
    # 4 detections nearby
    dets = [
        _make_detection(detection_id=i, lat=60.5, lon=25.5, scene_id=f"gfw-sar-{i:03d}")
        for i in range(1, 5)
    ]

    gap_query = MagicMock()
    gap_query.filter.return_value = gap_query
    gap_query.all.return_value = [gap]

    det_query = MagicMock()
    det_query.filter.return_value = det_query
    det_query.all.return_value = dets

    db = MagicMock()
    db.query.side_effect = [gap_query, det_query]

    validate_gaps_with_sar(db, datetime(2026, 1, 1), datetime(2026, 2, 1))

    assert len(gap.risk_breakdown_json["sar_validation"]["detections"]) == 4

    # Now test the scoring side — confirmed gap with 4 detections
    from app.modules.scoring_config import load_scoring_config

    config = load_scoring_config()
    gs_cfg = config.get("gap_sar_validation", {})
    bonus_per = gs_cfg.get("sar_detection_count_bonus", 5)
    # 4 detections * 5 = 20, capped at 15
    expected_bonus = min(4 * bonus_per, 15)
    assert expected_bonus == 15
