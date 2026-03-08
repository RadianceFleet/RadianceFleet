"""Tests for enhanced PSC detention scoring in risk_scoring.py."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_mock_vessel


def _make_gap(vessel_id=1, vessel=None):
    """Create a mock gap event with the minimum attributes for compute_gap_score."""
    gap = MagicMock()
    gap.vessel_id = vessel_id
    gap.duration_minutes = 600  # 10 hours
    gap.gap_start_utc = datetime(2026, 1, 15, 0, 0)
    gap.gap_end_utc = datetime(2026, 1, 15, 10, 0)
    gap.gap_event_id = 1
    gap.lat_start = 55.0
    gap.lon_start = 25.0
    gap.lat_end = 55.1
    gap.lon_end = 25.1
    gap.is_feed_outage = False
    gap.original_vessel_id = None
    gap.corridor_id = None
    gap.corridor = None
    gap.pre_gap_sog = None
    gap.risk_score = 0
    gap.risk_breakdown_json = None
    # Attach vessel
    if vessel is None:
        vessel = make_mock_vessel(
            vessel_id=vessel_id,
            year_built=2010,
            flag="PA",
            imo="1234567",
            vessel_type="Crude Oil Tanker",
            ais_class=MagicMock(value="A"),
            flag_risk_category=MagicMock(value="neutral"),
            pi_coverage_status=MagicMock(value="unknown"),
            psc_detained_last_12m=False,
            psc_major_deficiencies_last_12m=0,
            mmsi_first_seen_utc=datetime(2020, 1, 1),
            vessel_laid_up_30d=False,
            vessel_laid_up_60d=False,
            vessel_laid_up_in_sts_zone=False,
            psc_detentions=[],
        )
    gap.vessel = vessel
    return gap


def _make_detention(detention_date, ban_type=None, deficiency_count=0, mou_source="tokyo_mou"):
    """Create a mock PscDetention record."""
    d = MagicMock()
    d.vessel_id = 1
    d.detention_date = detention_date
    d.ban_type = ban_type
    d.deficiency_count = deficiency_count
    d.mou_source = mou_source
    d.major_deficiency_count = 0
    d.release_date = None
    d.port_name = "Test Port"
    d.port_country = "JP"
    d.data_source = "opensanctions_ftm"
    d.raw_entity_id = "test-123"
    return d


def _mock_db_with_detentions(detentions):
    """Create a mock DB session that returns given detentions for PscDetention queries."""
    db = MagicMock()

    def query_side_effect(model):
        result = MagicMock()

        # Check if this is a PscDetention query by class name
        model_name = getattr(model, "__name__", "") or getattr(model, "__tablename__", "")
        if "PscDetention" in str(model_name) or "psc" in str(getattr(model, "__tablename__", "")):
            # PscDetention query — return our detentions
            def filter_side_effect(*args, **kwargs):
                filtered = MagicMock()
                filtered.filter = filter_side_effect
                filtered.all = MagicMock(return_value=detentions)
                filtered.first = MagicMock(return_value=detentions[0] if detentions else None)
                filtered.count = MagicMock(return_value=len(detentions))
                return filtered
            result.filter = filter_side_effect
            result.all = MagicMock(return_value=detentions)
        else:
            # Default: return empty for other models
            def default_filter(*args, **kwargs):
                f = MagicMock()
                f.filter = default_filter
                f.all = MagicMock(return_value=[])
                f.first = MagicMock(return_value=None)
                f.count = MagicMock(return_value=0)
                f.order_by = MagicMock(return_value=f)
                return f
            result.filter = default_filter
            result.all = MagicMock(return_value=[])
            result.first = MagicMock(return_value=None)

        return result

    db.query = MagicMock(side_effect=query_side_effect)
    return db


class TestPscEnhancedScoring:
    """Tests for enhanced PSC detention record scoring."""

    def _score(self, detentions=None, vessel_kwargs=None):
        """Helper to compute score with optional detentions."""
        from app.modules.risk_scoring import compute_gap_score
        from app.modules.scoring_config import load_scoring_config

        config = load_scoring_config()
        vkw = vessel_kwargs or {}
        vessel = make_mock_vessel(
            vessel_id=1,
            year_built=2010,
            flag="PA",
            imo="1234567",
            vessel_type="Crude Oil Tanker",
            ais_class=MagicMock(value="A"),
            flag_risk_category=MagicMock(value="neutral"),
            pi_coverage_status=MagicMock(value="unknown"),
            psc_detained_last_12m=False,
            psc_major_deficiencies_last_12m=0,
            mmsi_first_seen_utc=datetime(2020, 1, 1),
            vessel_laid_up_30d=False,
            vessel_laid_up_60d=False,
            vessel_laid_up_in_sts_zone=False,
            psc_detentions=[],
            **vkw,
        )
        gap = _make_gap(vessel=vessel)
        db = _mock_db_with_detentions(detentions or [])
        score, breakdown = compute_gap_score(gap, config, db=db)
        return score, breakdown

    def test_no_detentions_no_extra_points(self):
        """No PSC detention records should not add enhanced scoring signals."""
        _, breakdown = self._score(detentions=[])
        assert "psc_multiple_detentions_2" not in breakdown
        assert "psc_multiple_detentions_3_plus" not in breakdown
        assert "psc_detention_in_last_30d" not in breakdown
        assert "psc_detention_in_last_90d" not in breakdown
        assert "psc_paris_mou_ban" not in breakdown
        assert "psc_deficiency_count_10_plus" not in breakdown

    def test_two_detentions_in_24m(self):
        """Two detentions in 24 months triggers multiple_detentions_2 signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=100)).date()),
            _make_detention((now - timedelta(days=200)).date()),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_multiple_detentions_2" in breakdown
        assert breakdown["psc_multiple_detentions_2"] == 10

    def test_three_plus_detentions_in_24m(self):
        """Three+ detentions in 24 months triggers multiple_detentions_3_plus signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=50)).date()),
            _make_detention((now - timedelta(days=150)).date()),
            _make_detention((now - timedelta(days=300)).date()),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_multiple_detentions_3_plus" in breakdown
        assert breakdown["psc_multiple_detentions_3_plus"] == 20
        assert "psc_multiple_detentions_2" not in breakdown  # mutually exclusive

    def test_recent_detention_30d(self):
        """Detention within 30 days adds recency signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=10)).date()),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_detention_in_last_30d" in breakdown
        assert breakdown["psc_detention_in_last_30d"] == 15
        assert "psc_detention_in_last_90d" not in breakdown  # 30d takes precedence

    def test_recent_detention_90d(self):
        """Detention within 90 days (but not 30) adds 90d recency signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=60)).date()),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_detention_in_last_90d" in breakdown
        assert breakdown["psc_detention_in_last_90d"] == 10
        assert "psc_detention_in_last_30d" not in breakdown

    def test_ban_type_adds_paris_mou_ban(self):
        """Detention with ban_type adds paris_mou_ban signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=100)).date(), ban_type="access_refusal"),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_paris_mou_ban" in breakdown
        assert breakdown["psc_paris_mou_ban"] == 15

    def test_high_deficiency_count(self):
        """Total deficiency count >= 10 adds deficiency_count_10_plus signal."""
        now = datetime.now(timezone.utc)
        detentions = [
            _make_detention((now - timedelta(days=100)).date(), deficiency_count=6),
            _make_detention((now - timedelta(days=200)).date(), deficiency_count=5),
        ]
        _, breakdown = self._score(detentions=detentions)
        assert "psc_deficiency_count_10_plus" in breakdown
        assert breakdown["psc_deficiency_count_10_plus"] == 8
