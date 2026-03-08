"""Tests for fleet-level behavioural analysis."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────────


def _make_vessel(vessel_id, flag="PA", owner_name=None, pi_coverage_status=None):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.flag = flag
    v.owner_name = owner_name
    v.pi_coverage_status = pi_coverage_status
    return v


def _make_cluster(cluster_id=1, is_sanctioned=False):
    c = MagicMock()
    c.cluster_id = cluster_id
    c.is_sanctioned = is_sanctioned
    return c


def _make_sts_event(vessel_1_id, vessel_2_id, corridor_id, start_time):
    e = MagicMock()
    e.vessel_1_id = vessel_1_id
    e.vessel_2_id = vessel_2_id
    e.corridor_id = corridor_id
    e.start_time_utc = start_time
    return e


def _make_gap_event(vessel_id, gap_start, risk_score=0, corridor_id=None, source=None):
    g = MagicMock()
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start
    g.risk_score = risk_score
    g.corridor_id = corridor_id
    g.source = source
    g.gap_off_lat = None
    g.gap_off_lon = None
    return g


# ── Tests: _check_flag_diversity ─────────────────────────────────────


class TestCheckFlagDiversity:
    def test_triggers_with_4_flags(self):
        from app.modules.fleet_analyzer import _check_flag_diversity

        cluster = _make_cluster()
        vessels = [
            _make_vessel(1, "PA"),
            _make_vessel(2, "LR"),
            _make_vessel(3, "MH"),
            _make_vessel(4, "CM"),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_flag_diversity"
        assert alert.risk_score_component == 20

    def test_no_trigger_with_3_flags(self):
        from app.modules.fleet_analyzer import _check_flag_diversity

        cluster = _make_cluster()
        vessels = [
            _make_vessel(1, "PA"),
            _make_vessel(2, "LR"),
            _make_vessel(3, "PA"),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is None

    def test_ignores_none_flags(self):
        from app.modules.fleet_analyzer import _check_flag_diversity

        cluster = _make_cluster()
        vessels = [
            _make_vessel(1, None),
            _make_vessel(2, None),
            _make_vessel(3, "PA"),
            _make_vessel(4, "LR"),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is None


# ── Tests: _check_sts_concentration ──────────────────────────────────


class TestCheckStsConcentration:
    def test_no_trigger_too_few_vessels(self):
        from app.modules.fleet_analyzer import _check_sts_concentration

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(1), _make_vessel(2)]

        result = _check_sts_concentration(db, cluster, vessels)
        assert result is None

    def test_triggers_with_3_vessels_same_corridor(self):
        from app.modules.fleet_analyzer import _check_sts_concentration

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(i) for i in range(1, 5)]

        base = datetime(2026, 1, 15)
        sts_events = [
            _make_sts_event(1, 10, corridor_id=1, start_time=base),
            _make_sts_event(2, 10, corridor_id=1, start_time=base + timedelta(days=5)),
            _make_sts_event(3, 10, corridor_id=1, start_time=base + timedelta(days=10)),
        ]

        db.query.return_value.filter.return_value.all.return_value = sts_events

        alert = _check_sts_concentration(db, cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_sts_concentration"
        assert alert.risk_score_component == 30


# ── Tests: _check_dark_coordination ──────────────────────────────────


class TestCheckDarkCoordination:
    def test_no_trigger_too_few_vessels(self):
        from app.modules.fleet_analyzer import _check_dark_coordination

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(1)]

        result = _check_dark_coordination(db, cluster, vessels)
        assert result is None

    def test_triggers_3_vessels_dark_within_48h_same_corridor(self):
        from app.modules.fleet_analyzer import _check_dark_coordination

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(i) for i in range(1, 5)]

        base = datetime(2026, 1, 15, 10, 0, 0)
        gaps = [
            _make_gap_event(1, base, corridor_id=5),
            _make_gap_event(2, base + timedelta(hours=6), corridor_id=5),
            _make_gap_event(3, base + timedelta(hours=12), corridor_id=5),
        ]

        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = gaps

        alert = _check_dark_coordination(db, cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_dark_coordination"

    def test_no_trigger_different_corridors_no_location(self):
        from app.modules.fleet_analyzer import _check_dark_coordination

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(i) for i in range(1, 5)]

        base = datetime(2026, 1, 15, 10, 0, 0)
        gaps = [
            _make_gap_event(1, base, corridor_id=1),
            _make_gap_event(2, base + timedelta(hours=1), corridor_id=2),
            _make_gap_event(3, base + timedelta(hours=2), corridor_id=3),
        ]

        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = gaps

        alert = _check_dark_coordination(db, cluster, vessels)
        assert alert is None


# ── Tests: _check_high_risk_average ──────────────────────────────────


class TestCheckHighRiskAverage:
    def test_triggers_above_threshold(self):
        from app.modules.fleet_analyzer import _check_high_risk_average

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(1), _make_vessel(2)]

        gaps = [
            _make_gap_event(1, datetime(2026, 1, 15), risk_score=80),
            _make_gap_event(2, datetime(2026, 1, 15), risk_score=60),
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps

        alert = _check_high_risk_average(cluster, vessels, db)
        assert alert is not None
        assert alert.alert_type == "fleet_high_risk_average"

    def test_no_trigger_below_threshold(self):
        from app.modules.fleet_analyzer import _check_high_risk_average

        db = MagicMock()
        cluster = _make_cluster()
        vessels = [_make_vessel(1), _make_vessel(2)]

        gaps = [
            _make_gap_event(1, datetime(2026, 1, 15), risk_score=30),
            _make_gap_event(2, datetime(2026, 1, 15), risk_score=20),
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps

        alert = _check_high_risk_average(cluster, vessels, db)
        assert alert is None

    def test_returns_none_no_vessels(self):
        from app.modules.fleet_analyzer import _check_high_risk_average

        db = MagicMock()
        cluster = _make_cluster()

        alert = _check_high_risk_average(cluster, [], db)
        assert alert is None


# ── Tests: _check_shared_pi_club ─────────────────────────────────────


class TestCheckSharedPiClub:
    def test_triggers_sanctioned_cluster(self):
        from app.modules.fleet_analyzer import _check_shared_pi_club

        db = MagicMock()
        cluster = _make_cluster(is_sanctioned=True)
        vessels = [
            _make_vessel(1, pi_coverage_status="expired"),
            _make_vessel(2, pi_coverage_status="expired"),
        ]

        alert = _check_shared_pi_club(cluster, vessels, db)
        assert alert is not None
        assert alert.alert_type == "shared_pi_club_high_risk"

    def test_no_trigger_non_sanctioned(self):
        from app.modules.fleet_analyzer import _check_shared_pi_club

        db = MagicMock()
        cluster = _make_cluster(is_sanctioned=False)
        vessels = [
            _make_vessel(1, pi_coverage_status="expired"),
            _make_vessel(2, pi_coverage_status="expired"),
        ]

        alert = _check_shared_pi_club(cluster, vessels, db)
        assert alert is None


# ── Tests: _geo_bin_key ──────────────────────────────────────────────


class TestGeoBinKey:
    def test_returns_correct_bin(self):
        from app.modules.fleet_analyzer import _geo_bin_key

        result = _geo_bin_key(25.3, 55.7)
        assert result == (5, 11)

    def test_returns_none_for_missing_coords(self):
        from app.modules.fleet_analyzer import _geo_bin_key

        assert _geo_bin_key(None, 55.0) is None
        assert _geo_bin_key(25.0, None) is None
        assert _geo_bin_key(None, None) is None


# ── Tests: run_fleet_analysis ────────────────────────────────────────


class TestRunFleetAnalysis:
    @patch.object(MagicMock, "FLEET_ANALYSIS_ENABLED", False, create=True)
    def test_disabled_returns_status(self):
        from app.modules.fleet_analyzer import run_fleet_analysis

        with patch("app.modules.fleet_analyzer.settings") as mock_settings:
            mock_settings.FLEET_ANALYSIS_ENABLED = False
            result = run_fleet_analysis(MagicMock())
            assert result["status"] == "disabled"
