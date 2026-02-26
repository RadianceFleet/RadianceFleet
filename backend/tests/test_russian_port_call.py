"""Tests for Russian port call scoring signal."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.risk_scoring import compute_gap_score, _had_russian_port_call


def _make_gap(vessel_id=1, duration_minutes=1560, corridor=None, corridor_type=None):
    """Create a mock gap event."""
    gap = MagicMock()
    gap.vessel_id = vessel_id
    gap.duration_minutes = duration_minutes
    gap.gap_start_utc = datetime(2026, 2, 15, 12, 0, 0)
    gap.gap_end_utc = gap.gap_start_utc + timedelta(minutes=duration_minutes)
    gap.velocity_plausibility_ratio = None
    gap.impossible_speed_flag = False
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.gap_event_id = 1

    if corridor_type:
        mock_corridor = MagicMock()
        mock_corridor.corridor_type = MagicMock()
        mock_corridor.corridor_type.value = corridor_type
        gap.corridor = mock_corridor
    else:
        gap.corridor = corridor

    return gap


def _make_vessel(flag_risk="medium_risk", deadweight=None, mmsi="351000000"):
    """Create a mock vessel."""
    v = MagicMock()
    v.vessel_id = 1
    v.mmsi = mmsi
    v.deadweight = deadweight
    v.year_built = None
    v.flag = "PA"
    v.flag_risk_category = MagicMock()
    v.flag_risk_category.value = flag_risk
    v.ais_class = MagicMock()
    v.ais_class.value = "A"
    v.pi_coverage_status = MagicMock()
    v.pi_coverage_status.value = "unknown"
    v.psc_detained_last_12m = False
    v.psc_major_deficiencies_last_12m = 0
    v.vessel_laid_up_30d = False
    v.vessel_laid_up_60d = False
    v.vessel_laid_up_in_sts_zone = False
    v.mmsi_first_seen_utc = None
    return v


class TestRussianPortCall:
    def test_russian_port_call_before_gap_fires_signal(self):
        """Vessel near Russian oil terminal before gap → russian_port_recent signal."""
        vessel = _make_vessel()
        gap = _make_gap()
        gap.vessel = vessel

        # Mock _had_russian_port_call to return True
        with patch("app.modules.risk_scoring._had_russian_port_call", return_value=True):
            # Minimal DB mock for compute_gap_score
            db = MagicMock()
            # All DB query chains return empty lists/None
            db.query.return_value.filter.return_value.all.return_value = []
            db.query.return_value.filter.return_value.first.return_value = None
            db.query.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            db.query.return_value.join.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.get.return_value = None

            score, breakdown = compute_gap_score(
                gap, {}, db=db, scoring_date=datetime(2026, 2, 20),
            )

        assert "russian_port_recent" in breakdown
        assert breakdown["russian_port_recent"] == 25

    def test_no_russian_port_call_no_signal(self):
        """No Russian port call → no signal in breakdown."""
        vessel = _make_vessel()
        gap = _make_gap()
        gap.vessel = vessel

        with patch("app.modules.risk_scoring._had_russian_port_call", return_value=False):
            db = MagicMock()
            db.query.return_value.filter.return_value.all.return_value = []
            db.query.return_value.filter.return_value.first.return_value = None
            db.query.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            db.query.return_value.join.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.get.return_value = None

            score, breakdown = compute_gap_score(
                gap, {}, db=db, scoring_date=datetime(2026, 2, 20),
            )

        assert "russian_port_recent" not in breakdown
        assert "russian_port_gap_sts" not in breakdown

    def test_russian_port_in_sts_corridor_fires_composite(self):
        """Russian port call + gap in STS zone → russian_port_gap_sts (40 pts)."""
        vessel = _make_vessel()
        gap = _make_gap(corridor_type="sts_zone")
        gap.vessel = vessel

        with patch("app.modules.risk_scoring._had_russian_port_call", return_value=True):
            db = MagicMock()
            db.query.return_value.filter.return_value.all.return_value = []
            db.query.return_value.filter.return_value.first.return_value = None
            db.query.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            db.query.return_value.join.return_value.filter.return_value.count.return_value = 0
            db.query.return_value.get.return_value = None

            score, breakdown = compute_gap_score(
                gap, {}, db=db, scoring_date=datetime(2026, 2, 20),
            )

        assert "russian_port_gap_sts" in breakdown
        assert breakdown["russian_port_gap_sts"] == 40
        assert "russian_port_recent" not in breakdown  # Composite replaces simple
