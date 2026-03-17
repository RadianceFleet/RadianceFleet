"""Tests for signal-corridor FP rate cross-tabulation matrix.

Covers:
  - Signal-corridor matrix computation
  - Signal-region matrix aggregation
  - Suppression candidate identification
  - Edge cases (nulls, string JSON, underscore keys, etc.)

Uses in-memory SQLite for all tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.scoring_region import ScoringRegion
from app.models.vessel import Vessel
from app.modules.signal_corridor_matrix import (
    compute_signal_corridor_matrix,
    compute_signal_region_matrix,
    identify_regional_suppressions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

_vessel_counter = 0


def _make_vessel(db, **kwargs):
    global _vessel_counter
    _vessel_counter += 1
    defaults = {"mmsi": f"21100{_vessel_counter:04d}", "name": f"TEST VESSEL {_vessel_counter}"}
    defaults.update(kwargs)
    v = Vessel(**defaults)
    db.add(v)
    db.flush()
    return v


def _make_corridor(db, name="Test Corridor", **kwargs):
    c = Corridor(name=name, **kwargs)
    db.add(c)
    db.flush()
    return c


def _make_alert(
    db,
    vessel,
    corridor,
    is_false_positive,
    breakdown=None,
    review_date=None,
):
    """Create a reviewed AISGapEvent with the given verdict and breakdown."""
    now = review_date or datetime(2026, 3, 1)
    gap = AISGapEvent(
        vessel_id=vessel.vessel_id,
        corridor_id=corridor.corridor_id if corridor else None,
        gap_start_utc=now - timedelta(hours=4),
        gap_end_utc=now - timedelta(hours=2),
        duration_minutes=120,
        risk_score=50,
        risk_breakdown_json=breakdown,
        is_false_positive=is_false_positive,
        review_date=now,
        reviewed_by="analyst@test.com",
    )
    db.add(gap)
    db.flush()
    return gap


def _make_unreviewed_alert(db, vessel, corridor, breakdown=None):
    """Create an alert with no analyst verdict."""
    now = datetime(2026, 3, 1)
    gap = AISGapEvent(
        vessel_id=vessel.vessel_id,
        corridor_id=corridor.corridor_id if corridor else None,
        gap_start_utc=now - timedelta(hours=4),
        gap_end_utc=now - timedelta(hours=2),
        duration_minutes=120,
        risk_score=50,
        risk_breakdown_json=breakdown,
        is_false_positive=None,
        review_date=None,
    )
    db.add(gap)
    db.flush()
    return gap


def _make_region(db, name="Test Region", corridor_ids=None, is_active=True):
    r = ScoringRegion(
        name=name,
        corridor_ids_json=json.dumps(corridor_ids) if corridor_ids is not None else None,
        is_active=is_active,
    )
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# Matrix computation tests
# ---------------------------------------------------------------------------


class TestSignalCorridorMatrix:
    @patch("app.modules.signal_corridor_matrix.settings", create=True)
    def test_empty_db_returns_empty(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 5
        with patch("app.modules.signal_corridor_matrix.compute_signal_corridor_matrix.__module__"):
            pass
        result = compute_signal_corridor_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_single_alert_single_signal(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Baltic Route")
        breakdown = {"gap_duration_4h_8h": 10}
        # Create 1 FP alert
        _make_alert(db, v, c, is_false_positive=True, breakdown=breakdown)
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        cell = result[0]
        assert cell.signal_name == "gap_duration_4h_8h"
        assert cell.corridor_id == c.corridor_id
        assert cell.corridor_name == "Baltic Route"
        assert cell.fp_count == 1
        assert cell.tp_count == 0
        assert cell.total == 1
        assert cell.fp_rate == 1.0

    @patch("app.config.settings")
    def test_multiple_signals_multiple_corridors(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c1 = _make_corridor(db, name="Route A")
        c2 = _make_corridor(db, name="Route B")

        # Route A: 2 FP with signal_a, 1 TP with signal_a
        _make_alert(db, v, c1, True, {"signal_a": 5})
        _make_alert(db, v, c1, True, {"signal_a": 5})
        _make_alert(db, v, c1, False, {"signal_a": 5})

        # Route B: 1 TP with signal_a, 1 TP with signal_b
        _make_alert(db, v, c2, False, {"signal_a": 5, "signal_b": 3})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        # Expect: (signal_a, Route A), (signal_a, Route B), (signal_b, Route B)
        assert len(result) == 3

        by_key = {(c.signal_name, c.corridor_id): c for c in result}
        cell_a1 = by_key[("signal_a", c1.corridor_id)]
        assert cell_a1.fp_count == 2
        assert cell_a1.tp_count == 1
        assert cell_a1.total == 3

        cell_a2 = by_key[("signal_a", c2.corridor_id)]
        assert cell_a2.fp_count == 0
        assert cell_a2.tp_count == 1

    @patch("app.config.settings")
    def test_skip_underscore_keys(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        breakdown = {
            "_total": 100,
            "_corridor_multiplier": 1.5,
            "gap_duration_2h_4h": 10,
        }
        _make_alert(db, v, c, True, breakdown)
        db.commit()

        result = compute_signal_corridor_matrix(db)
        signal_names = {cell.signal_name for cell in result}
        assert "_total" not in signal_names
        assert "_corridor_multiplier" not in signal_names
        assert "gap_duration_2h_4h" in signal_names

    @patch("app.config.settings")
    def test_null_breakdown_skipped(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        _make_alert(db, v, c, True, breakdown=None)
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_string_breakdown_parsed(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        # Store breakdown as a JSON string
        _make_alert(db, v, c, True, breakdown=json.dumps({"speed_impossible": 15}))
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        assert result[0].signal_name == "speed_impossible"

    @patch("app.config.settings")
    def test_since_filter(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        old_date = datetime(2025, 1, 1)
        new_date = datetime(2026, 3, 10)
        _make_alert(db, v, c, True, {"sig_a": 5}, review_date=old_date)
        _make_alert(db, v, c, False, {"sig_a": 5}, review_date=new_date)
        db.commit()

        # With since=2026-03-01, only the new alert should be included
        result = compute_signal_corridor_matrix(db, since=datetime(2026, 3, 1))
        assert len(result) == 1
        assert result[0].tp_count == 1
        assert result[0].fp_count == 0

    @patch("app.config.settings")
    def test_min_verdicts_filter(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 3
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        # Only 2 alerts — below threshold of 3
        _make_alert(db, v, c, True, {"sig_a": 5})
        _make_alert(db, v, c, False, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_lift_computation(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c1 = _make_corridor(db, name="High FP Corridor")
        c2 = _make_corridor(db, name="Low FP Corridor")

        # Corridor 1: 4 FP, 1 TP for sig_a -> FP rate 0.8
        for _ in range(4):
            _make_alert(db, v, c1, True, {"sig_a": 5})
        _make_alert(db, v, c1, False, {"sig_a": 5})

        # Corridor 2: 1 FP, 4 TP for sig_a -> FP rate 0.2
        _make_alert(db, v, c2, True, {"sig_a": 5})
        for _ in range(4):
            _make_alert(db, v, c2, False, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        by_cid = {c.corridor_id: c for c in result}

        # Global FP rate = 5/10 = 0.5
        c1_cell = by_cid[c1.corridor_id]
        assert c1_cell.fp_rate == 0.8
        # lift = 0.8 / 0.5 = 1.6
        assert c1_cell.lift == 1.6

        c2_cell = by_cid[c2.corridor_id]
        assert c2_cell.fp_rate == 0.2
        # lift = 0.2 / 0.5 = 0.4
        assert c2_cell.lift == 0.4

    @patch("app.config.settings")
    def test_lift_zero_global(self, mock_settings, db):
        """When global FP rate is 0 for a signal, lift should be 0."""
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        # All TP — global FP rate is 0
        _make_alert(db, v, c, False, {"sig_a": 5})
        _make_alert(db, v, c, False, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        assert result[0].fp_rate == 0.0
        assert result[0].lift == 0.0

    @patch("app.config.settings")
    def test_corridor_names_populated(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Turkish Straits")
        _make_alert(db, v, c, True, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert result[0].corridor_name == "Turkish Straits"

    @patch("app.config.settings")
    def test_all_fp_corridor(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="100% FP")
        for _ in range(5):
            _make_alert(db, v, c, True, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        assert result[0].fp_rate == 1.0
        assert result[0].fp_count == 5
        assert result[0].tp_count == 0

    @patch("app.config.settings")
    def test_all_tp_corridor(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="0% FP")
        for _ in range(5):
            _make_alert(db, v, c, False, {"sig_a": 5})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        assert result[0].fp_rate == 0.0
        assert result[0].tp_count == 5


# ---------------------------------------------------------------------------
# Region matrix tests
# ---------------------------------------------------------------------------


class TestSignalRegionMatrix:
    @patch("app.config.settings")
    def test_region_matrix_empty(self, mock_settings, db):
        """No regions defined — returns empty."""
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        result = compute_signal_region_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_region_matrix_basic(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c1 = _make_corridor(db, name="Corridor A")
        c2 = _make_corridor(db, name="Corridor B")
        _make_region(db, name="Baltic Region", corridor_ids=[c1.corridor_id, c2.corridor_id])
        db.commit()

        # Alerts in both corridors
        _make_alert(db, v, c1, True, {"sig_a": 5})
        _make_alert(db, v, c2, False, {"sig_a": 5})
        db.commit()

        result = compute_signal_region_matrix(db)
        assert len(result) == 1
        cell = result[0]
        assert cell.region_name == "Baltic Region"
        assert cell.signal_name == "sig_a"
        assert cell.tp_count == 1
        assert cell.fp_count == 1
        assert cell.total == 2
        assert cell.fp_rate == 0.5

    @patch("app.config.settings")
    def test_region_matrix_inactive_region(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Corridor X")
        _make_region(db, name="Inactive Region", corridor_ids=[c.corridor_id], is_active=False)
        db.commit()

        _make_alert(db, v, c, True, {"sig_a": 5})
        db.commit()

        result = compute_signal_region_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_region_matrix_empty_corridor_ids(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Orphan Corridor")
        _make_region(db, name="Empty Region", corridor_ids=None)
        db.commit()

        _make_alert(db, v, c, True, {"sig_a": 5})
        db.commit()

        result = compute_signal_region_matrix(db)
        assert result == []


# ---------------------------------------------------------------------------
# Suppression candidate tests
# ---------------------------------------------------------------------------


class TestSuppressionCandidates:
    @patch("app.config.settings")
    def test_no_suppressions_below_threshold(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        mock_settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD = 0.50
        v = _make_vessel(db)
        c = _make_corridor(db, name="Clean Corridor")
        # 1 FP, 4 TP -> FP rate 0.2, below 0.50
        _make_alert(db, v, c, True, {"sig_a": 5})
        for _ in range(4):
            _make_alert(db, v, c, False, {"sig_a": 5})
        db.commit()

        result = identify_regional_suppressions(db)
        assert result == []

    @patch("app.config.settings")
    def test_suppression_above_threshold(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        mock_settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD = 0.50
        v = _make_vessel(db)
        c = _make_corridor(db, name="Noisy Corridor")
        # 4 FP, 1 TP -> FP rate 0.8, above 0.50
        for _ in range(4):
            _make_alert(db, v, c, True, {"sig_a": 5})
        _make_alert(db, v, c, False, {"sig_a": 5})
        db.commit()

        result = identify_regional_suppressions(db)
        assert len(result) == 1
        assert result[0].signal_name == "sig_a"
        assert result[0].corridor_name == "Noisy Corridor"
        assert result[0].fp_rate == 0.8

    @patch("app.config.settings")
    def test_suppression_suggested_action(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        mock_settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD = 0.50
        v = _make_vessel(db)

        # 90%+ FP rate -> "Consider suppressing"
        c1 = _make_corridor(db, name="Suppress")
        for _ in range(9):
            _make_alert(db, v, c1, True, {"sig_x": 5})
        _make_alert(db, v, c1, False, {"sig_x": 5})

        # 75%+ FP rate -> "Reduce weight by 75%"
        c2 = _make_corridor(db, name="Reduce75")
        for _ in range(3):
            _make_alert(db, v, c2, True, {"sig_y": 5})
        _make_alert(db, v, c2, False, {"sig_y": 5})

        # 60%+ FP rate -> "Reduce weight by 50%"
        c3 = _make_corridor(db, name="Reduce50")
        for _ in range(7):
            _make_alert(db, v, c3, True, {"sig_z": 5})
        for _ in range(3):
            _make_alert(db, v, c3, False, {"sig_z": 5})
        db.commit()

        result = identify_regional_suppressions(db)
        by_corridor = {s.corridor_name: s for s in result}

        assert "suppressing" in by_corridor["Suppress"].suggested_action.lower()
        assert "75%" in by_corridor["Reduce75"].suggested_action
        assert "50%" in by_corridor["Reduce50"].suggested_action

    @patch("app.config.settings")
    def test_suppression_min_verdicts_filter(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 10
        mock_settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD = 0.50
        v = _make_vessel(db)
        c = _make_corridor(db, name="Low Count")
        # 3 FP, 0 TP = 100% FP rate but only 3 verdicts (below min of 10)
        for _ in range(3):
            _make_alert(db, v, c, True, {"sig_a": 5})
        db.commit()

        result = identify_regional_suppressions(db)
        assert result == []

    @patch("app.config.settings")
    def test_suppression_includes_global_rate(self, mock_settings, db):
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        mock_settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD = 0.50
        v = _make_vessel(db)
        c1 = _make_corridor(db, name="Bad Corridor")
        c2 = _make_corridor(db, name="Good Corridor")

        # Bad corridor: 4 FP, 1 TP
        for _ in range(4):
            _make_alert(db, v, c1, True, {"sig_a": 5})
        _make_alert(db, v, c1, False, {"sig_a": 5})

        # Good corridor: 0 FP, 5 TP
        for _ in range(5):
            _make_alert(db, v, c2, False, {"sig_a": 5})
        db.commit()

        result = identify_regional_suppressions(db)
        assert len(result) == 1
        candidate = result[0]
        # Global rate for sig_a across both corridors: 4/10 = 0.4
        assert candidate.global_fp_rate == 0.4
        assert candidate.fp_rate == 0.8


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @patch("app.config.settings")
    def test_corridor_without_corridor_id(self, mock_settings, db):
        """Alerts with NULL corridor_id should be skipped."""
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Valid")

        # Alert with corridor_id=NULL
        gap_no_corridor = AISGapEvent(
            vessel_id=v.vessel_id,
            corridor_id=None,
            gap_start_utc=datetime(2026, 3, 1),
            gap_end_utc=datetime(2026, 3, 1, 2),
            duration_minutes=120,
            risk_score=50,
            risk_breakdown_json={"sig_a": 10},
            is_false_positive=True,
            review_date=datetime(2026, 3, 1),
        )
        db.add(gap_no_corridor)
        db.flush()

        # Alert with corridor_id set
        _make_alert(db, v, c, False, {"sig_a": 10})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 1
        assert result[0].corridor_id == c.corridor_id

    @patch("app.config.settings")
    def test_unreviewed_alerts_excluded(self, mock_settings, db):
        """Alerts without verdicts (is_false_positive=NULL) should be excluded."""
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        _make_unreviewed_alert(db, v, c, {"sig_a": 10})
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert result == []

    @patch("app.config.settings")
    def test_large_breakdown_many_signals(self, mock_settings, db):
        """Alert with 30+ signals should be parsed correctly."""
        mock_settings.SIGNAL_MATRIX_MIN_VERDICTS = 1
        v = _make_vessel(db)
        c = _make_corridor(db, name="Test")
        breakdown = {f"signal_{i}": i + 1 for i in range(35)}
        _make_alert(db, v, c, True, breakdown)
        db.commit()

        result = compute_signal_corridor_matrix(db)
        assert len(result) == 35
        signal_names = {cell.signal_name for cell in result}
        for i in range(35):
            assert f"signal_{i}" in signal_names
