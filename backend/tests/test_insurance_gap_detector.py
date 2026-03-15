"""Tests for insurance gap timeline detector."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.insurance_gap_event import InsuranceGapEvent
from app.models.vessel_history import VesselHistory
from app.modules.insurance_gap_detector import (
    _build_pi_timeline,
    _check_coinciding_events,
    _find_coverage_gaps,
    _score_gap,
    detect_insurance_gaps,
    get_vessel_insurance_gaps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_history(
    vessel_id: int,
    field_changed: str,
    old_value: str | None,
    new_value: str | None,
    observed_at: datetime,
) -> MagicMock:
    h = MagicMock(spec=VesselHistory)
    h.vessel_history_id = 1
    h.vessel_id = vessel_id
    h.field_changed = field_changed
    h.old_value = old_value
    h.new_value = new_value
    h.observed_at = observed_at
    h.source = "test"
    return h


# ---------------------------------------------------------------------------
# _build_pi_timeline tests
# ---------------------------------------------------------------------------


class TestBuildPiTimeline:
    def test_empty_history_no_owner(self):
        result = _build_pi_timeline([], None)
        assert result == []

    def test_empty_history_with_owner_baseline(self):
        result = _build_pi_timeline([], "Gard P&I")
        assert len(result) == 1
        assert result[0]["club_name"] == "Gard P&I"
        assert result[0]["start_date"] is None
        assert result[0]["end_date"] is None

    def test_single_record_with_old_value(self):
        t1 = datetime(2024, 1, 1)
        records = [_make_history(1, "pi_club_name", "Skuld", "Gard", t1)]
        result = _build_pi_timeline(records, None)
        assert len(result) == 2
        assert result[0]["club_name"] == "Skuld"
        assert result[0]["end_date"] == t1
        assert result[1]["club_name"] == "Gard"
        assert result[1]["start_date"] == t1

    def test_single_record_null_old_with_owner_baseline(self):
        t1 = datetime(2024, 1, 1)
        records = [_make_history(1, "pi_club_name", None, "Gard", t1)]
        result = _build_pi_timeline(records, "Skuld")
        assert len(result) == 2
        assert result[0]["club_name"] == "Skuld"
        assert result[0]["end_date"] == t1
        assert result[1]["club_name"] == "Gard"

    def test_multiple_changes(self):
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 6, 1)
        records = [
            _make_history(1, "pi_club_name", "Skuld", "Gard", t1),
            _make_history(1, "pi_club_name", "Gard", "Standard Club", t2),
        ]
        result = _build_pi_timeline(records, None)
        # Skuld (until t1), Gard (t1 to t2), Standard Club (t2 to None)
        assert len(result) == 3
        assert result[0]["club_name"] == "Skuld"
        assert result[1]["club_name"] == "Gard"
        assert result[1]["end_date"] == t2
        assert result[2]["club_name"] == "Standard Club"
        assert result[2]["start_date"] == t2
        assert result[2]["end_date"] is None

    def test_null_new_value_club_removed(self):
        t1 = datetime(2024, 1, 1)
        records = [_make_history(1, "pi_club_name", "Skuld", None, t1)]
        result = _build_pi_timeline(records, None)
        # Skuld ended at t1, no new club
        assert len(result) == 1
        assert result[0]["club_name"] == "Skuld"
        assert result[0]["end_date"] == t1


# ---------------------------------------------------------------------------
# _find_coverage_gaps tests
# ---------------------------------------------------------------------------


class TestFindCoverageGaps:
    def test_no_gaps_continuous(self):
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 6, 1)
        timeline = [
            {"club_name": "Skuld", "start_date": None, "end_date": t1},
            {"club_name": "Gard", "start_date": t1, "end_date": t2},
            {"club_name": "Standard", "start_date": t2, "end_date": None},
        ]
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 0

    def test_gap_between_clubs(self):
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 4, 1)  # 91 days later
        timeline = [
            {"club_name": "Skuld", "start_date": None, "end_date": t1},
            {"club_name": "Gard", "start_date": t2, "end_date": None},
        ]
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 1
        assert gaps[0]["gap_days"] == 91
        assert gaps[0]["previous_club"] == "Skuld"
        assert gaps[0]["next_club"] == "Gard"

    def test_gap_below_min_days(self):
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 1, 4)  # 3 days
        timeline = [
            {"club_name": "Skuld", "start_date": None, "end_date": t1},
            {"club_name": "Gard", "start_date": t2, "end_date": None},
        ]
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 0

    def test_multiple_gaps(self):
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 2, 15)  # 45 day gap
        t3 = datetime(2024, 4, 1)
        t4 = datetime(2024, 7, 1)  # 91 day gap
        timeline = [
            {"club_name": "A", "start_date": None, "end_date": t1},
            {"club_name": "B", "start_date": t2, "end_date": t3},
            {"club_name": "C", "start_date": t4, "end_date": None},
        ]
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 2

    def test_empty_timeline(self):
        gaps = _find_coverage_gaps([], 7)
        assert len(gaps) == 0

    def test_single_entry_no_gap(self):
        timeline = [{"club_name": "Skuld", "start_date": None, "end_date": None}]
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 0


# ---------------------------------------------------------------------------
# _check_coinciding_events tests
# ---------------------------------------------------------------------------


class TestCheckCoincidingEvents:
    def test_flag_change_within_window(self):
        db = MagicMock()
        gap_start = datetime(2024, 3, 1)
        gap_end = datetime(2024, 5, 1)

        flag_change = _make_history(1, "flag", "Panama", "Cameroon", datetime(2024, 3, 15))
        db.query.return_value.filter.return_value.all.return_value = [flag_change]

        result = _check_coinciding_events(db, 1, gap_start, gap_end)
        assert result["flag_change"] is True
        assert result["ownership_change"] is False

    def test_ownership_change_within_window(self):
        db = MagicMock()
        gap_start = datetime(2024, 3, 1)
        gap_end = datetime(2024, 5, 1)

        owner_change = _make_history(1, "owner_name", "OldCo", "NewCo", datetime(2024, 4, 1))
        db.query.return_value.filter.return_value.all.return_value = [owner_change]

        result = _check_coinciding_events(db, 1, gap_start, gap_end)
        assert result["flag_change"] is False
        assert result["ownership_change"] is True

    def test_both_changes(self):
        db = MagicMock()
        gap_start = datetime(2024, 3, 1)
        gap_end = datetime(2024, 5, 1)

        flag_change = _make_history(1, "flag", "Panama", "Cameroon", datetime(2024, 3, 15))
        owner_change = _make_history(1, "owner_name", "OldCo", "NewCo", datetime(2024, 4, 1))
        db.query.return_value.filter.return_value.all.return_value = [flag_change, owner_change]

        result = _check_coinciding_events(db, 1, gap_start, gap_end)
        assert result["flag_change"] is True
        assert result["ownership_change"] is True

    def test_no_changes(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = _check_coinciding_events(db, 1, datetime(2024, 3, 1), datetime(2024, 5, 1))
        assert result["flag_change"] is False
        assert result["ownership_change"] is False

    def test_ongoing_gap_none_end(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = _check_coinciding_events(db, 1, datetime(2024, 3, 1), None)
        assert result["flag_change"] is False
        assert result["ownership_change"] is False


# ---------------------------------------------------------------------------
# _score_gap tests
# ---------------------------------------------------------------------------


class TestScoreGap:
    def test_gap_under_30d(self):
        assert _score_gap(15, False, False, False, False) == 0.0

    def test_gap_30d(self):
        assert _score_gap(30, False, False, False, False) == 15.0

    def test_gap_60d(self):
        assert _score_gap(60, False, False, False, False) == 25.0

    def test_gap_90d(self):
        assert _score_gap(90, False, False, False, False) == 35.0

    def test_gap_120d(self):
        assert _score_gap(120, False, False, False, False) == 35.0

    def test_non_ig_transition_bonus(self):
        # prev_ig=True, next_ig=False
        score = _score_gap(90, False, False, True, False)
        assert score == 40.0  # 35 + 5

    def test_no_bonus_if_next_is_also_ig(self):
        score = _score_gap(90, False, False, True, True)
        assert score == 35.0  # no bonus

    def test_flag_change_bonus(self):
        score = _score_gap(90, True, False, False, False)
        assert score == 45.0  # 35 + 10

    def test_ownership_change_bonus(self):
        score = _score_gap(90, False, True, False, False)
        assert score == 45.0  # 35 + 10

    def test_all_bonuses(self):
        score = _score_gap(90, True, True, True, False)
        assert score == 60.0  # 35 + 5 + 10 + 10

    def test_30d_with_flag_bonus(self):
        score = _score_gap(30, True, False, False, False)
        assert score == 25.0  # 15 + 10

    def test_gap_exactly_at_boundary_60(self):
        assert _score_gap(59, False, False, False, False) == 15.0
        assert _score_gap(60, False, False, False, False) == 25.0


# ---------------------------------------------------------------------------
# detect_insurance_gaps integration tests
# ---------------------------------------------------------------------------


class TestDetectInsuranceGaps:
    @patch("app.modules.insurance_gap_detector.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.INSURANCE_GAP_DETECTION_ENABLED = False
        db = MagicMock()
        result = detect_insurance_gaps(db, 1)
        assert result == []

    @patch("app.modules.insurance_gap_detector.settings")
    def test_no_history_no_owner(self, mock_settings):
        mock_settings.INSURANCE_GAP_DETECTION_ENABLED = True
        mock_settings.INSURANCE_GAP_MIN_DAYS = 7
        db = MagicMock()
        # pi_changes query returns empty
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # VesselOwner query returns None
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = detect_insurance_gaps(db, 1)
        assert result == []

    @patch("app.modules.insurance_gap_detector.settings")
    def test_single_owner_baseline_no_history(self, mock_settings):
        mock_settings.INSURANCE_GAP_DETECTION_ENABLED = True
        mock_settings.INSURANCE_GAP_MIN_DAYS = 7
        db = MagicMock()

        # pi_changes query returns empty
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # VesselOwner with pi_club
        owner = MagicMock()
        owner.pi_club_name = "Gard"
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = owner

        result = detect_insurance_gaps(db, 1)
        # Single owner with current club - no gap
        assert result == []


# ---------------------------------------------------------------------------
# get_vessel_insurance_gaps tests
# ---------------------------------------------------------------------------


class TestGetVesselInsuranceGaps:
    def test_returns_query_results(self):
        db = MagicMock()
        mock_events = [MagicMock(spec=InsuranceGapEvent), MagicMock(spec=InsuranceGapEvent)]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_events

        result = get_vessel_insurance_gaps(db, 1)
        assert len(result) == 2

    def test_returns_empty_list(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = get_vessel_insurance_gaps(db, 1)
        assert result == []


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestInsuranceGapEventModel:
    def test_table_name(self):
        assert InsuranceGapEvent.__tablename__ == "insurance_gap_events"

    def test_unique_constraint_name(self):
        constraints = [
            c.name for c in InsuranceGapEvent.__table_args__
            if hasattr(c, "name")
        ]
        assert "uq_insurance_gap_vessel_start" in constraints

    def test_default_values(self):
        event = InsuranceGapEvent.__new__(InsuranceGapEvent)
        # Check column defaults are defined
        cols = {c.name: c for c in InsuranceGapEvent.__table__.columns}
        assert cols["previous_club_is_ig"].default.arg is False
        assert cols["next_club_is_ig"].default.arg is False
        assert cols["coincides_with_flag_change"].default.arg is False
        assert cols["coincides_with_ownership_change"].default.arg is False
        assert cols["risk_score_component"].default.arg == 0.0


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_timeline_null_new_value_creates_removal(self):
        """NULL new_value in VesselHistory means club was removed."""
        t1 = datetime(2024, 1, 1)
        records = [_make_history(1, "pi_club_name", "Skuld", None, t1)]
        timeline = _build_pi_timeline(records, None)
        assert len(timeline) == 1
        assert timeline[0]["club_name"] == "Skuld"
        assert timeline[0]["end_date"] == t1

    def test_timeline_null_old_value_club_appeared(self):
        """NULL old_value means club appeared from nothing."""
        t1 = datetime(2024, 1, 1)
        records = [_make_history(1, "pi_club_name", None, "Gard", t1)]
        timeline = _build_pi_timeline(records, None)
        assert len(timeline) == 1
        assert timeline[0]["club_name"] == "Gard"
        assert timeline[0]["start_date"] == t1

    def test_gap_with_removal_and_new_club(self):
        """Club removed, then new club added after a gap."""
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 4, 1)  # 91 days later
        records = [
            _make_history(1, "pi_club_name", "Skuld", None, t1),
            _make_history(1, "pi_club_name", None, "Gard", t2),
        ]
        timeline = _build_pi_timeline(records, None)
        # Skuld ended at t1; Gard started at t2
        # There should be a timeline entry for Skuld ending, and Gard starting
        assert len(timeline) == 2
        gaps = _find_coverage_gaps(timeline, 7)
        assert len(gaps) == 1
        assert gaps[0]["gap_days"] == 91

    def test_min_gap_days_configuration(self):
        """Verify min_gap_days parameter filters correctly."""
        t1 = datetime(2024, 1, 1)
        t2 = datetime(2024, 1, 20)  # 19 days
        timeline = [
            {"club_name": "A", "start_date": None, "end_date": t1},
            {"club_name": "B", "start_date": t2, "end_date": None},
        ]
        # With min 7 days, should find gap
        assert len(_find_coverage_gaps(timeline, 7)) == 1
        # With min 20 days, should not find gap
        assert len(_find_coverage_gaps(timeline, 20)) == 0
        # With min 19 days, should find gap (19 >= 19)
        assert len(_find_coverage_gaps(timeline, 19)) == 1
