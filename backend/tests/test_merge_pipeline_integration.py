"""Integration tests for merge pipeline diagnostics and identity resolution."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import app
from app.models import Base
from app.models.ais_point import AISPoint
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel

runner = CliRunner()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Helper to create vessels + AIS points
# ---------------------------------------------------------------------------


def _make_vessel(
    db,
    mmsi,
    imo=None,
    name=None,
    flag=None,
    vessel_type=None,
    deadweight=None,
    callsign=None,
    first_seen=None,
):
    v = Vessel(
        mmsi=mmsi,
        imo=imo,
        name=name,
        flag=flag,
        vessel_type=vessel_type,
        deadweight=deadweight,
        callsign=callsign,
        mmsi_first_seen_utc=first_seen,
    )
    db.add(v)
    db.flush()
    return v


def _make_ais_point(db, vessel_id, ts, lat=55.0, lon=20.0, sog=10.0):
    pt = AISPoint(
        vessel_id=vessel_id,
        timestamp_utc=ts,
        lat=lat,
        lon=lon,
        sog=sog,
    )
    db.add(pt)
    db.flush()
    return pt


def _make_gap_event(db, vessel_id, start, end):
    gap = AISGapEvent(
        vessel_id=vessel_id,
        gap_start_utc=start,
        gap_end_utc=end,
        duration_minutes=int((end - start).total_seconds() / 60),
    )
    db.add(gap)
    db.flush()
    return gap


# ---------------------------------------------------------------------------
# Test 1: diagnose identifies no gap events
# ---------------------------------------------------------------------------


def test_diagnose_identifies_no_gap_events(db):
    """Vessels with no gap events trigger an issue."""
    from app.modules.identity_resolver import diagnose_merge_readiness

    now = datetime.utcnow()
    v = _make_vessel(db, "123456789")
    _make_ais_point(db, v.vessel_id, now - timedelta(hours=1))
    db.flush()

    result = diagnose_merge_readiness(db)
    assert result["total_vessels"] == 1
    assert result["vessels_with_gaps"] == 0
    assert any("No vessels have gap events" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Test 2: diagnose identifies no dark candidates
# ---------------------------------------------------------------------------


def test_diagnose_identifies_no_dark_candidates(db):
    """Vessels with gaps but recent AIS -> no dark candidates issue."""
    from app.modules.identity_resolver import diagnose_merge_readiness

    now = datetime.utcnow()
    v = _make_vessel(db, "111111111")
    # Recent AIS (within 2h) means vessel won't be "dark"
    _make_ais_point(db, v.vessel_id, now - timedelta(minutes=30))
    _make_gap_event(db, v.vessel_id, now - timedelta(hours=10), now - timedelta(hours=5))
    db.flush()

    result = diagnose_merge_readiness(db)
    assert result["vessels_with_gaps"] >= 1
    assert result["dark_candidates"] == 0
    assert any("No dark candidates found" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Test 3: diagnose identifies sparse data
# ---------------------------------------------------------------------------


def test_diagnose_identifies_sparse_data(db):
    """Few AIS points per vessel trigger sparse data warning."""
    from app.modules.identity_resolver import diagnose_merge_readiness

    now = datetime.utcnow()
    # Create 3 vessels with 1-2 points each (avg < 5)
    for i in range(3):
        v = _make_vessel(db, f"20000000{i}")
        _make_ais_point(db, v.vessel_id, now - timedelta(hours=i + 1))
    db.flush()

    result = diagnose_merge_readiness(db)
    assert result["avg_points_per_vessel"] < 5
    assert any("Sparse AIS data" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Test 4: diagnose returns merge config
# ---------------------------------------------------------------------------


def test_diagnose_returns_config(db):
    """Result includes merge_config with all 4 threshold keys."""
    from app.modules.identity_resolver import diagnose_merge_readiness

    result = diagnose_merge_readiness(db)
    cfg = result["merge_config"]
    assert "max_gap_days" in cfg
    assert "max_speed_kn" in cfg
    assert "auto_threshold" in cfg
    assert "min_threshold" in cfg


# ---------------------------------------------------------------------------
# Test 5: detect creates candidate for valid pair
# ---------------------------------------------------------------------------


def test_detect_creates_candidate_for_valid_pair(db):
    """Dark vessel + new vessel with same IMO and nearby -> candidate created."""
    from app.modules.identity_resolver import detect_merge_candidates

    now = datetime.utcnow()

    # Dark vessel: has gap event, last AIS >2h ago
    dark_v = _make_vessel(
        db, "300000001", imo="9876543", vessel_type="Crude Oil Tanker", deadweight=150000.0
    )
    _make_ais_point(db, dark_v.vessel_id, now - timedelta(hours=5), lat=55.0, lon=20.0)
    _make_gap_event(db, dark_v.vessel_id, now - timedelta(hours=6), now - timedelta(hours=5))

    # New vessel: appeared recently, same IMO, nearby position
    new_v = _make_vessel(
        db,
        "300000002",
        imo="9876543",
        vessel_type="Crude Oil Tanker",
        deadweight=150000.0,
        first_seen=now - timedelta(hours=3),
    )
    _make_ais_point(db, new_v.vessel_id, now - timedelta(hours=3), lat=55.1, lon=20.1)
    db.flush()

    result = detect_merge_candidates(db)
    assert result["candidates_created"] >= 1 or result["auto_merged"] >= 1


# ---------------------------------------------------------------------------
# Test 6: auto merge at high confidence
# ---------------------------------------------------------------------------


def test_auto_merge_at_high_confidence(db):
    """Pair with IMO + type + DWT + name + callsign match -> auto-merged."""
    from app.modules.identity_resolver import detect_merge_candidates

    now = datetime.utcnow()

    dark_v = _make_vessel(
        db,
        "400000001",
        imo="1234567",
        name="SHADOW TANKER",
        vessel_type="Crude Oil Tanker",
        deadweight=150000.0,
        callsign="ABCD",
        flag="PA",
    )
    _make_ais_point(db, dark_v.vessel_id, now - timedelta(hours=5), lat=55.0, lon=20.0)
    _make_gap_event(db, dark_v.vessel_id, now - timedelta(hours=6), now - timedelta(hours=5))

    new_v = _make_vessel(
        db,
        "400000002",
        imo="1234567",
        name="SHADOW TANKER",
        vessel_type="Crude Oil Tanker",
        deadweight=150000.0,
        callsign="ABCD",
        flag="PA",
        first_seen=now - timedelta(hours=3),
    )
    _make_ais_point(db, new_v.vessel_id, now - timedelta(hours=3), lat=55.05, lon=20.05)
    db.flush()

    result = detect_merge_candidates(db)
    assert result["auto_merged"] >= 1


# ---------------------------------------------------------------------------
# Test 7: detect logs when no candidates
# ---------------------------------------------------------------------------


def test_detect_logs_when_no_candidates(db, caplog):
    """When no dark or new vessels exist, a log message with counts is emitted."""
    from app.modules.identity_resolver import detect_merge_candidates

    # No vessels at all — should log "No merge candidates"
    with caplog.at_level(logging.INFO, logger="app.modules.identity_resolver"):
        result = detect_merge_candidates(db)

    assert result["candidates_created"] == 0
    assert any("No merge candidates" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Test 8: check-vessels --diagnose prints and exits
# ---------------------------------------------------------------------------


def test_check_vessels_diagnose_prints_and_exits():
    """--diagnose prints diagnostic and does NOT scan for vessel identity."""
    mock_db = MagicMock()

    diag_result = {
        "total_vessels": 42,
        "dark_candidates": 0,
        "new_candidates": 0,
        "vessels_with_gaps": 5,
        "avg_points_per_vessel": 3.2,
        "issues": ["No dark candidates found (need vessels with gap events + last AIS >2h ago)"],
        "merge_config": {
            "max_gap_days": 30,
            "max_speed_kn": 16.0,
            "auto_threshold": 85,
            "min_threshold": 50,
        },
    }

    with (
        patch("app.database.SessionLocal", return_value=mock_db),
        patch(
            "app.modules.identity_resolver.diagnose_merge_readiness", return_value=diag_result
        ) as mock_diag,
    ):
        result = runner.invoke(app, ["check-vessels", "--diagnose"])

    assert result.exit_code == 0
    assert "Merge Readiness" in result.output
    assert "Scanning for vessel identity" not in result.output
    mock_diag.assert_called_once()


# ---------------------------------------------------------------------------
# Test 9: check-vessels --diagnose overrides --auto
# ---------------------------------------------------------------------------


def test_check_vessels_diagnose_overrides_auto():
    """--diagnose takes precedence over --auto."""
    mock_db = MagicMock()

    diag_result = {
        "total_vessels": 10,
        "dark_candidates": 0,
        "new_candidates": 0,
        "vessels_with_gaps": 0,
        "avg_points_per_vessel": 0.0,
        "issues": ["No vessels have gap events"],
        "merge_config": {
            "max_gap_days": 30,
            "max_speed_kn": 16.0,
            "auto_threshold": 85,
            "min_threshold": 50,
        },
    }

    with (
        patch("app.database.SessionLocal", return_value=mock_db),
        patch("app.modules.identity_resolver.diagnose_merge_readiness", return_value=diag_result),
        patch("app.modules.identity_resolver.detect_merge_candidates") as mock_detect,
    ):
        result = runner.invoke(app, ["check-vessels", "--diagnose", "--auto"])

    assert result.exit_code == 0
    assert "Merge Readiness" in result.output
    mock_detect.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10: pipeline runs identity resolution
# ---------------------------------------------------------------------------


def test_pipeline_runs_identity_resolution():
    """discover_dark_vessels result dict contains 'identity_resolution' key.

    The pipeline uses lazy local imports in try/except blocks, so we mock
    each step function at its source module to intercept the local import.
    """
    from app.modules.dark_vessel_discovery import discover_dark_vessels

    mock_db = MagicMock()

    with (
        patch("app.modules.gap_detector.run_gap_detection", return_value={}),
        patch("app.modules.gap_detector.run_spoofing_detection", return_value={}),
        patch("app.modules.loitering_detector.run_loitering_detection", return_value={}),
        patch("app.modules.sts_detector.detect_sts_events", return_value={}),
        patch("app.modules.risk_scoring.rescore_all_alerts", return_value={}),
        patch("app.modules.dark_vessel_discovery.cluster_dark_detections", return_value={}),
        patch("app.modules.dark_vessel_discovery.auto_hunt_dark_vessels", return_value={}),
        patch(
            "app.modules.identity_resolver.detect_merge_candidates",
            return_value={"candidates_created": 0, "auto_merged": 0, "skipped": 0},
        ),
    ):
        result = discover_dark_vessels(
            mock_db,
            start_date="2025-01-01",
            end_date="2025-01-31",
            skip_fetch=True,
        )

    assert "identity_resolution" in result.get("steps", {})
