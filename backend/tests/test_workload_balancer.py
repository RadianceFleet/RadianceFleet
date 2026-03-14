"""Tests for the smart workload balancer module."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workload_balancer import (
    calculate_weighted_workload,
    is_on_shift,
    match_specialization,
    suggest_assignment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyst(
    analyst_id=1,
    is_active=True,
    max_concurrent_alerts=10,
    shift_start_hour=None,
    shift_end_hour=None,
    specializations_json=None,
):
    a = MagicMock()
    a.analyst_id = analyst_id
    a.is_active = is_active
    a.max_concurrent_alerts = max_concurrent_alerts
    a.shift_start_hour = shift_start_hour
    a.shift_end_hour = shift_end_hour
    a.specializations_json = specializations_json
    return a


def _make_alert(
    gap_event_id=1,
    risk_score=50,
    assigned_to=None,
    assigned_at=None,
    status="new",
    corridor_id=None,
    source=None,
):
    a = MagicMock()
    a.gap_event_id = gap_event_id
    a.risk_score = risk_score
    a.assigned_to = assigned_to
    a.assigned_at = assigned_at
    a.status = status
    a.corridor_id = corridor_id
    a.source = source
    return a


# ---------------------------------------------------------------------------
# calculate_weighted_workload
# ---------------------------------------------------------------------------


class TestCalculateWeightedWorkload:
    def test_empty_no_alerts(self, mock_db):
        """No alerts assigned — utilization should be 0."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        # For the risk_score query
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = calculate_weighted_workload(mock_db, 1)
        assert result == 0.0

    def test_high_priority_alerts(self, mock_db):
        """Score >= 80 should count 2x weight."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        # 2 high-priority alerts: 2 * 2.0 = 4.0 / 10 = 0.4
        mock_db.query.return_value.filter.return_value.all.return_value = [(85,), (90,)]

        result = calculate_weighted_workload(mock_db, 1)
        assert result == pytest.approx(0.4)

    def test_medium_priority_alerts(self, mock_db):
        """Score 60-79 should count 1.5x weight."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        # 2 medium alerts: 2 * 1.5 = 3.0 / 10 = 0.3
        mock_db.query.return_value.filter.return_value.all.return_value = [(65,), (70,)]

        result = calculate_weighted_workload(mock_db, 1)
        assert result == pytest.approx(0.3)

    def test_low_priority_alerts(self, mock_db):
        """Score < 60 should count 1.0x weight."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        # 3 low alerts: 3 * 1.0 = 3.0 / 10 = 0.3
        mock_db.query.return_value.filter.return_value.all.return_value = [(30,), (45,), (55,)]

        result = calculate_weighted_workload(mock_db, 1)
        assert result == pytest.approx(0.3)

    def test_mixed_priorities(self, mock_db):
        """Mixed priority alerts should sum correctly."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        # 1 high (2.0) + 1 medium (1.5) + 1 low (1.0) = 4.5 / 10 = 0.45
        mock_db.query.return_value.filter.return_value.all.return_value = [(85,), (65,), (30,)]

        result = calculate_weighted_workload(mock_db, 1)
        assert result == pytest.approx(0.45)

    def test_unknown_analyst(self, mock_db):
        """Unknown analyst should return 1.0 (fully loaded)."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = calculate_weighted_workload(mock_db, 999)
        assert result == 1.0

    def test_custom_max_concurrent(self, mock_db):
        """Custom max_concurrent_alerts should change denominator."""
        analyst = _make_analyst(analyst_id=1, max_concurrent_alerts=5)
        mock_db.query.return_value.filter.return_value.first.return_value = analyst
        # 2 low alerts: 2 * 1.0 = 2.0 / 5 = 0.4
        mock_db.query.return_value.filter.return_value.all.return_value = [(30,), (40,)]

        result = calculate_weighted_workload(mock_db, 1)
        assert result == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# is_on_shift
# ---------------------------------------------------------------------------


class TestIsOnShift:
    def test_always_on_both_none(self):
        """Both shift hours None means always on shift."""
        analyst = _make_analyst(shift_start_hour=None, shift_end_hour=None)
        assert is_on_shift(analyst) is True

    def test_within_window(self):
        """Current hour within normal shift window."""
        analyst = _make_analyst(shift_start_hour=9, shift_end_hour=17)
        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 12
            mock_dt.now.return_value = mock_now
            assert is_on_shift(analyst) is True

    def test_outside_window(self):
        """Current hour outside normal shift window."""
        analyst = _make_analyst(shift_start_hour=9, shift_end_hour=17)
        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 20
            mock_dt.now.return_value = mock_now
            assert is_on_shift(analyst) is False

    def test_wraparound_inside(self):
        """Wraparound shift (22-06), current hour 2 should be on shift."""
        analyst = _make_analyst(shift_start_hour=22, shift_end_hour=6)
        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 2
            mock_dt.now.return_value = mock_now
            assert is_on_shift(analyst) is True

    def test_wraparound_outside(self):
        """Wraparound shift (22-06), current hour 12 should be off shift."""
        analyst = _make_analyst(shift_start_hour=22, shift_end_hour=6)
        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 12
            mock_dt.now.return_value = mock_now
            assert is_on_shift(analyst) is False

    def test_wraparound_at_start(self):
        """Wraparound shift (22-06), current hour 22 should be on shift."""
        analyst = _make_analyst(shift_start_hour=22, shift_end_hour=6)
        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 22
            mock_dt.now.return_value = mock_now
            assert is_on_shift(analyst) is True

    def test_only_start_set(self):
        """Only shift_start_hour set — treat as always on (incomplete config)."""
        analyst = _make_analyst(shift_start_hour=9, shift_end_hour=None)
        assert is_on_shift(analyst) is True


# ---------------------------------------------------------------------------
# match_specialization
# ---------------------------------------------------------------------------


class TestMatchSpecialization:
    def test_corridor_match(self):
        """Analyst specialized in corridor 5, alert in corridor 5 -> 1.0."""
        analyst = _make_analyst(specializations_json=json.dumps([5, 10]))
        alert = _make_alert(corridor_id=5)
        assert match_specialization(analyst, alert) == 1.0

    def test_no_match(self):
        """Analyst specialized in corridor 5, alert in corridor 99 -> 0.0."""
        analyst = _make_analyst(specializations_json=json.dumps([5, 10]))
        alert = _make_alert(corridor_id=99)
        assert match_specialization(analyst, alert) == 0.0

    def test_no_specializations(self):
        """No specializations_json -> 0.0."""
        analyst = _make_analyst(specializations_json=None)
        alert = _make_alert(corridor_id=5)
        assert match_specialization(analyst, alert) == 0.0

    def test_empty_specializations(self):
        """Empty specializations list -> 0.0."""
        analyst = _make_analyst(specializations_json=json.dumps([]))
        alert = _make_alert(corridor_id=5)
        assert match_specialization(analyst, alert) == 0.0

    def test_type_match(self):
        """String specialization matching alert source -> 0.5."""
        analyst = _make_analyst(specializations_json=json.dumps(["gfw"]))
        alert = _make_alert(source="gfw")
        assert match_specialization(analyst, alert) == 0.5

    def test_invalid_json(self):
        """Invalid JSON in specializations_json -> 0.0."""
        analyst = _make_analyst(specializations_json="not json")
        alert = _make_alert(corridor_id=5)
        assert match_specialization(analyst, alert) == 0.0

    def test_corridor_as_string(self):
        """Corridor ID stored as string in specializations -> still matches."""
        analyst = _make_analyst(specializations_json=json.dumps(["5", "10"]))
        alert = _make_alert(corridor_id=5)
        assert match_specialization(analyst, alert) == 1.0


# ---------------------------------------------------------------------------
# suggest_assignment
# ---------------------------------------------------------------------------


class TestSuggestAssignment:
    """Tests for suggest_assignment using patched helper functions.

    Since suggest_assignment involves complex multi-query DB interaction,
    we use a dispatch-based mock that routes db.query() calls based on
    the model class argument.
    """

    def _setup_db(self, mock_db, analysts, alert=None, open_alerts=None, last_assigned=None):
        """Wire up mock DB for suggest_assignment queries using dispatch."""
        from app.models.analyst import Analyst

        def query_dispatch(*args):
            q = MagicMock()
            # Detect which model is being queried by inspecting args
            # query(Analyst) -> filter(is_active).all()
            if args and args[0] is Analyst:
                q.filter.return_value.all.return_value = analysts
                return q

            # query(AISGapEvent) -> filter(gap_event_id == X).first()
            # Also: query(AISGapEvent.assigned_to, AISGapEvent.risk_score)
            #   -> filter(...).filter(...).all()
            # Also: query(AISGapEvent.assigned_to, func.max(...))
            #   -> filter(...).group_by(...).all()

            # For AISGapEvent single-object query (alert lookup)
            q.filter.return_value.first.return_value = alert

            # For batch open alerts query (two chained filters)
            q.filter.return_value.filter.return_value.all.return_value = open_alerts or []
            # Also handle .in_() chaining
            q.filter.return_value.all.return_value = open_alerts or []

            # For fairness query (filter -> group_by -> all)
            q.filter.return_value.group_by.return_value.all.return_value = last_assigned or []

            return q

        mock_db.query.side_effect = query_dispatch

    def test_basic_picks_least_loaded(self, mock_db):
        """Should pick analyst with lowest utilization."""
        a1 = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        a2 = _make_analyst(analyst_id=2, max_concurrent_alerts=10)
        # a1 has 5 low-priority alerts, a2 has 0
        self._setup_db(
            mock_db,
            analysts=[a1, a2],
            open_alerts=[(1, 30), (1, 40), (1, 50), (1, 45), (1, 35)],
        )

        result = suggest_assignment(mock_db)
        assert result == 2  # a2 is less loaded

    def test_excludes_off_shift(self, mock_db):
        """Off-shift analysts should be excluded."""
        a1 = _make_analyst(analyst_id=1, shift_start_hour=9, shift_end_hour=17)
        a2 = _make_analyst(analyst_id=2)  # always on

        with patch("app.modules.workload_balancer.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 20  # outside a1's shift
            mock_dt.now.return_value = mock_now

            self._setup_db(mock_db, analysts=[a1, a2])
            result = suggest_assignment(mock_db)
            assert result == 2

    def test_excludes_inactive(self, mock_db):
        """Inactive analysts should not appear (filtered at DB level)."""
        a1 = _make_analyst(analyst_id=1)
        self._setup_db(mock_db, analysts=[a1])

        result = suggest_assignment(mock_db)
        assert result == 1

    def test_with_exclude_ids(self, mock_db):
        """Excluded IDs should be filtered out."""
        a1 = _make_analyst(analyst_id=1)
        a2 = _make_analyst(analyst_id=2)
        self._setup_db(mock_db, analysts=[a1, a2])

        result = suggest_assignment(mock_db, exclude_ids=[1])
        assert result == 2

    def test_no_candidates(self, mock_db):
        """No active analysts -> None."""
        self._setup_db(mock_db, analysts=[])
        result = suggest_assignment(mock_db)
        assert result is None

    def test_all_excluded(self, mock_db):
        """All analysts excluded -> None."""
        a1 = _make_analyst(analyst_id=1)
        self._setup_db(mock_db, analysts=[a1])
        result = suggest_assignment(mock_db, exclude_ids=[1])
        assert result is None

    def test_specialization_boost(self, mock_db):
        """Analyst with matching specialization should get a boost."""
        a1 = _make_analyst(analyst_id=1, specializations_json=json.dumps([5]))
        a2 = _make_analyst(analyst_id=2, specializations_json=json.dumps([99]))

        alert = _make_alert(gap_event_id=10, corridor_id=5)
        self._setup_db(mock_db, analysts=[a1, a2], alert=alert)

        result = suggest_assignment(mock_db, alert_id=10)
        assert result == 1  # a1 has corridor match

    def test_fairness_component(self, mock_db):
        """Analyst who hasn't been assigned recently should be preferred."""
        a1 = _make_analyst(analyst_id=1, max_concurrent_alerts=10)
        a2 = _make_analyst(analyst_id=2, max_concurrent_alerts=10)

        now = datetime.now(UTC)
        row1 = (1, now - timedelta(minutes=5))
        row2 = (2, now - timedelta(days=30))

        self._setup_db(mock_db, analysts=[a1, a2], last_assigned=[row1, row2])

        result = suggest_assignment(mock_db)
        # Both have 0 open alerts so utilization is equal (both get fairness data).
        # a2 has higher fairness => should be preferred.
        assert result == 2
