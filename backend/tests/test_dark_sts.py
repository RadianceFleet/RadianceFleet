"""Tests for Phase N: Dark STS + Gap Rate Baseline.

Covers dark-dark STS detection (both vessels AIS-dark simultaneously),
gap rate baseline computation, satellite tasking candidate creation,
and feature flag gating.
"""
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.models.base import STSDetectionTypeEnum, FlagRiskEnum, CorridorTypeEnum


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_vessel(
    vessel_id: int,
    vessel_type: str = "Crude Oil Tanker",
    flag_risk: str = "high_risk",
    year_built: int = 1998,
    psc_detained: bool = False,
    laid_up_sts: bool = False,
    mmsi: str = None,
):
    """Build a mock Vessel with sensible defaults."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi or f"24100{vessel_id:04d}"
    v.vessel_type = vessel_type
    v.flag_risk_category = MagicMock()
    v.flag_risk_category.value = flag_risk
    v.year_built = year_built
    v.psc_detained_last_12m = psc_detained
    v.vessel_laid_up_in_sts_zone = laid_up_sts
    v.name = f"Test Vessel {vessel_id}"
    return v


def _make_gap(
    vessel_id: int,
    start: datetime,
    end: datetime,
    corridor_id: int = None,
    off_lat: float = None,
    off_lon: float = None,
    on_lat: float = None,
    on_lon: float = None,
):
    """Build a mock AISGapEvent."""
    g = MagicMock()
    g.vessel_id = vessel_id
    g.gap_event_id = vessel_id * 100 + 1
    g.gap_start_utc = start
    g.gap_end_utc = end
    g.duration_minutes = int((end - start).total_seconds() / 60)
    g.corridor_id = corridor_id
    g.gap_off_lat = off_lat
    g.gap_off_lon = off_lon
    g.gap_on_lat = on_lat
    g.gap_on_lon = on_lon
    g.risk_score = 0
    g.in_dark_zone = False
    return g


def _make_corridor(
    corridor_id: int = 1,
    name: str = "Laconian Gulf STS Zone",
    geometry: str = "POLYGON((36.0 22.0, 37.0 22.0, 37.0 23.0, 36.0 23.0, 36.0 22.0))",
    corridor_type: str = "sts_zone",
):
    """Build a mock Corridor."""
    c = MagicMock()
    c.corridor_id = corridor_id
    c.name = name
    c.geometry = geometry
    c.corridor_type = MagicMock()
    c.corridor_type.value = corridor_type
    c.is_jamming_zone = False
    return c


def _make_settings(dark_sts_enabled: bool = True, dark_sts_scoring: bool = True):
    """Build a mock settings object."""
    s = MagicMock()
    s.DARK_STS_DETECTION_ENABLED = dark_sts_enabled
    s.DARK_STS_SCORING_ENABLED = dark_sts_scoring
    s.STS_MIN_WINDOWS = 8
    s.STS_PROXIMITY_METERS = 200.0
    return s


def _standard_config():
    """Return a standard dark_sts config dict."""
    return {
        "dark_sts": {
            "high_confidence_5nm": 30,
            "medium_confidence_15nm": 20,
            "low_confidence_50nm": 10,
            "min_overlap_hours": 4,
            "max_candidates_per_corridor": 100,
            "p95_suppression": True,
        },
        "sts": {
            "one_vessel_dark_during_proximity": 15,
        },
    }


# ── Tests: _phase_c_dark_dark ───────────────────────────────────────────────

class TestDarkDarkHighConfidence:
    """Two tankers 3nm apart, 6h gap overlap -> HIGH confidence event."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=3.0)
    def test_high_confidence_creates_event(
        self, mock_haversine, mock_p95, mock_overlap, mock_settings
    ):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        # Setup
        corridor = _make_corridor()
        vessel_a = _make_vessel(1, flag_risk="high_risk", year_built=1995)
        vessel_b = _make_vessel(2, flag_risk="high_risk", year_built=1993)

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5, on_lat=36.5, on_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=36.52, off_lon=22.52, on_lat=36.52, on_lon=22.52)

        db = MagicMock()
        # db.query(AISGapEvent).all() returns our gaps
        # db.query(Vessel).all() returns our vessels
        # We need the queries to return the right data
        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 1
        # Check db.add was called (StsTransferEvent + SatelliteTaskingCandidate)
        assert db.add.call_count == 2
        db.commit.assert_called_once()

        # Verify the StsTransferEvent was created with correct attributes
        sts_event_call = db.add.call_args_list[0]
        sts_event = sts_event_call[0][0]
        assert sts_event.detection_type == STSDetectionTypeEnum.DARK_DARK
        assert sts_event.risk_score_component == 30  # high confidence

        # Verify the SatelliteTaskingCandidate
        candidate_call = db.add.call_args_list[1]
        candidate = candidate_call[0][0]
        assert candidate.confidence_level == "high"
        assert candidate.risk_score_component == 30


class TestDarkDarkJammingSuppression:
    """Two tankers during jamming (P95+ gap rate) -> SUPPRESSED."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=True)
    def test_p95_suppresses_detection(self, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        vessel_a = _make_vessel(1, flag_risk="high_risk")
        vessel_b = _make_vessel(2, flag_risk="high_risk")

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=36.52, off_lon=22.52)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 0
        # No events should be added
        db.add.assert_not_called()


class TestDarkDarkTankerFilter:
    """Tanker + bulk carrier -> FILTERED (both must be tankers)."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=3.0)
    def test_non_tanker_filtered(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        vessel_a = _make_vessel(1, vessel_type="Crude Oil Tanker", flag_risk="high_risk")
        vessel_b = _make_vessel(2, vessel_type="Bulk Carrier", flag_risk="high_risk")

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=36.52, off_lon=22.52)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        # Bulk carrier should be filtered -- gap_b's vessel is not a tanker
        assert result == 0


class TestDarkDarkMinimumOverlap:
    """30-min overlap -> FILTERED (below 4h minimum)."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=3.0)
    def test_short_overlap_filtered(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        vessel_a = _make_vessel(1, flag_risk="high_risk")
        vessel_b = _make_vessel(2, flag_risk="high_risk")

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        # Only 30 minutes of overlap
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=2), corridor_id=1,
                          off_lat=36.5, off_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1, minutes=30), t0 + timedelta(hours=4), corridor_id=1,
                          off_lat=36.52, off_lon=22.52)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 0


class TestDarkDarkMaxCandidates:
    """>100 candidates in corridor -> early exit."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=3.0)
    def test_early_exit_at_max_candidates(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)

        # Create many vessels and gaps to exceed the limit
        # With max_candidates=5 for this test
        vessels = []
        gaps = []
        for i in range(1, 25):  # 24 vessels -> up to C(24,2)=276 pairs
            v = _make_vessel(i, flag_risk="high_risk", year_built=1990)
            vessels.append(v)
            g = _make_gap(
                i, t0, t0 + timedelta(hours=8), corridor_id=1,
                off_lat=36.5 + i * 0.001, off_lon=22.5 + i * 0.001,
                on_lat=36.5 + i * 0.001, on_lon=22.5 + i * 0.001,
            )
            gaps.append(g)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = gaps
            elif model_name == "Vessel":
                mock_q.all.return_value = vessels
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        # Use a very low max to test early exit
        config = _standard_config()
        config["dark_sts"]["max_candidates_per_corridor"] = 5

        result = _phase_c_dark_dark(db, [corridor], config)

        # Should stop at 5 candidates
        assert result == 5
        # db.add is called twice per candidate (STS event + tasking candidate)
        assert db.add.call_count == 10


class TestDarkDarkFeatureFlagDisabled:
    """Feature flag disabled -> no-op."""

    @patch("app.modules.sts_detector._settings")
    def test_disabled_returns_zero(self, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = False

        db = MagicMock()
        result = _phase_c_dark_dark(db, [], {})

        assert result == 0
        db.query.assert_not_called()
        db.add.assert_not_called()
        db.commit.assert_not_called()


class TestDarkDarkMediumConfidence:
    """Medium confidence (5-15nm) test."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=10.0)
    def test_medium_confidence_detection(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        vessel_a = _make_vessel(1, flag_risk="high_risk", year_built=1995)
        vessel_b = _make_vessel(2, flag_risk="high_risk", year_built=1993)

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5, on_lat=36.5, on_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=36.6, off_lon=22.6, on_lat=36.6, on_lon=22.6)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 1
        # Verify medium confidence
        sts_event = db.add.call_args_list[0][0][0]
        assert sts_event.risk_score_component == 20

        candidate = db.add.call_args_list[1][0][0]
        assert candidate.confidence_level == "medium"


class TestDarkDarkLowConfidence:
    """Low confidence (15-50nm, both high-risk) test."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=30.0)
    def test_low_confidence_both_high_risk(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        # Both vessels high-risk -- required for low confidence tier
        vessel_a = _make_vessel(1, flag_risk="high_risk", year_built=1990)
        vessel_b = _make_vessel(2, flag_risk="high_risk", year_built=1992)

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5, on_lat=36.5, on_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=36.8, off_lon=22.8, on_lat=36.8, on_lon=22.8)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 1
        sts_event = db.add.call_args_list[0][0][0]
        assert sts_event.risk_score_component == 10  # low confidence

        candidate = db.add.call_args_list[1][0][0]
        assert candidate.confidence_level == "low"


class TestDarkDarkDistanceTooFar:
    """>50nm distance -> discarded."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=60.0)
    def test_over_50nm_discarded(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()
        vessel_a = _make_vessel(1, flag_risk="high_risk")
        vessel_b = _make_vessel(2, flag_risk="high_risk")

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=8), corridor_id=1,
                          off_lat=36.5, off_lon=22.5, on_lat=36.5, on_lon=22.5)
        gap_b = _make_gap(2, t0 + timedelta(hours=1), t0 + timedelta(hours=7), corridor_id=1,
                          off_lat=37.5, off_lon=23.5, on_lat=37.5, on_lon=23.5)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 0


class TestDarkDarkSatelliteCandidate:
    """SatelliteTaskingCandidate created with correct fields."""

    @patch("app.modules.sts_detector._settings")
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    @patch("app.modules.gap_rate_baseline.is_above_p95", return_value=False)
    @patch("app.utils.geo.haversine_nm", return_value=2.0)
    def test_candidate_has_correct_fields(self, mock_haversine, mock_p95, mock_overlap, mock_settings):
        from app.modules.sts_detector import _phase_c_dark_dark

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor(corridor_id=5)
        vessel_a = _make_vessel(10, flag_risk="high_risk", year_built=1995)
        vessel_b = _make_vessel(20, flag_risk="high_risk", year_built=1993)

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gap_a = _make_gap(10, t0, t0 + timedelta(hours=10), corridor_id=5,
                          off_lat=36.5, off_lon=22.5, on_lat=36.5, on_lon=22.5)
        gap_b = _make_gap(20, t0 + timedelta(hours=2), t0 + timedelta(hours=8), corridor_id=5,
                          off_lat=36.52, off_lon=22.52, on_lat=36.52, on_lon=22.52)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "AISGapEvent":
                mock_q.all.return_value = [gap_a, gap_b]
            elif model_name == "Vessel":
                mock_q.all.return_value = [vessel_a, vessel_b]
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        config = _standard_config()
        result = _phase_c_dark_dark(db, [corridor], config)

        assert result == 1

        # Check the SatelliteTaskingCandidate
        candidate = db.add.call_args_list[1][0][0]
        assert candidate.corridor_id == 5
        assert candidate.vessel_a_id == 10
        assert candidate.vessel_b_id == 20
        assert candidate.confidence_level == "high"
        assert candidate.risk_score_component == 30
        # Overlap: t0+2h to t0+8h = 6 hours
        assert candidate.gap_overlap_hours == 6.0
        assert candidate.proximity_nm == 2.0
        assert candidate.recommended_imagery_window_start == t0 + timedelta(hours=2)
        assert candidate.recommended_imagery_window_end == t0 + timedelta(hours=8)


# ── Tests: Gap Rate Baseline ────────────────────────────────────────────────

class TestGapRateBaselineComputation:
    """Test compute_gap_rate_baseline."""

    @patch("app.modules.gap_rate_baseline.settings")
    def test_disabled_returns_zero(self, mock_settings):
        from app.modules.gap_rate_baseline import compute_gap_rate_baseline

        mock_settings.DARK_STS_DETECTION_ENABLED = False

        db = MagicMock()
        result = compute_gap_rate_baseline(db)

        assert result == {"corridors_processed": 0, "baselines_created": 0}
        db.query.assert_not_called()

    @patch("app.modules.gap_rate_baseline.settings")
    def test_baseline_computation_works(self, mock_settings):
        from app.modules.gap_rate_baseline import compute_gap_rate_baseline

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        corridor = _make_corridor()

        t0 = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
        gaps = [
            _make_gap(1, t0, t0 + timedelta(hours=4), corridor_id=1),
            _make_gap(2, t0 + timedelta(days=1), t0 + timedelta(days=1, hours=6), corridor_id=1),
            _make_gap(3, t0 + timedelta(days=3), t0 + timedelta(days=3, hours=8), corridor_id=1),
        ]

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "Corridor":
                mock_q.all.return_value = [corridor]
            elif model_name == "AISGapEvent":
                mock_q.all.return_value = gaps
            elif model_name == "CorridorGapBaseline":
                mock_q.filter.return_value.update.return_value = None
            else:
                mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        result = compute_gap_rate_baseline(db)

        assert result["corridors_processed"] == 1
        assert result["baselines_created"] >= 1
        db.commit.assert_called_once()

    @patch("app.modules.gap_rate_baseline.settings")
    def test_no_gaps_returns_zero(self, mock_settings):
        from app.modules.gap_rate_baseline import compute_gap_rate_baseline

        mock_settings.DARK_STS_DETECTION_ENABLED = True

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            mock_q.all.return_value = []
            return mock_q

        db.query.side_effect = query_side_effect

        result = compute_gap_rate_baseline(db)

        assert result == {"corridors_processed": 0, "baselines_created": 0}


class TestIsAboveP95:
    """Test is_above_p95 function."""

    def test_above_p95_returns_true(self):
        from app.modules.gap_rate_baseline import is_above_p95

        baseline = MagicMock()
        baseline.gap_count = 20
        baseline.p95_threshold = 15.0

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = baseline

        ref_time = datetime(2025, 6, 5, 12, 0, tzinfo=timezone.utc)
        assert is_above_p95(db, 1, ref_time) is True

    def test_below_p95_returns_false(self):
        from app.modules.gap_rate_baseline import is_above_p95

        baseline = MagicMock()
        baseline.gap_count = 5
        baseline.p95_threshold = 15.0

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = baseline

        ref_time = datetime(2025, 6, 5, 12, 0, tzinfo=timezone.utc)
        assert is_above_p95(db, 1, ref_time) is False

    def test_no_baseline_returns_false(self):
        from app.modules.gap_rate_baseline import is_above_p95

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        ref_time = datetime(2025, 6, 5, 12, 0, tzinfo=timezone.utc)
        assert is_above_p95(db, 1, ref_time) is False


# ── Tests: Helper functions ──────────────────────────────────────────────────

class TestPercentile:
    """Test _percentile helper."""

    def test_percentile_single_value(self):
        from app.modules.gap_rate_baseline import _percentile

        assert _percentile([10], 95) == 10.0

    def test_percentile_basic(self):
        from app.modules.gap_rate_baseline import _percentile

        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        p50 = _percentile(values, 50)
        assert 5.0 <= p50 <= 6.0

        p95 = _percentile(values, 95)
        assert p95 >= 9.0

    def test_percentile_empty(self):
        from app.modules.gap_rate_baseline import _percentile

        assert _percentile([], 95) == 0.0


class TestVesselHasRiskFactor:
    """Test _vessel_has_risk_factor helper."""

    def test_high_risk_flag(self):
        from app.modules.sts_detector import _vessel_has_risk_factor

        vessel = _make_vessel(1, flag_risk="high_risk", year_built=2015)
        assert _vessel_has_risk_factor(vessel) is True

    def test_old_vessel(self):
        from app.modules.sts_detector import _vessel_has_risk_factor

        vessel = _make_vessel(1, flag_risk="low_risk", year_built=1990)
        assert _vessel_has_risk_factor(vessel) is True

    def test_psc_detained(self):
        from app.modules.sts_detector import _vessel_has_risk_factor

        vessel = _make_vessel(1, flag_risk="low_risk", year_built=2015, psc_detained=True)
        assert _vessel_has_risk_factor(vessel) is True

    def test_clean_vessel_no_risk(self):
        from app.modules.sts_detector import _vessel_has_risk_factor

        vessel = _make_vessel(1, flag_risk="low_risk", year_built=2015)
        assert _vessel_has_risk_factor(vessel) is False


class TestDarkDarkProximity:
    """Test _dark_dark_proximity helper."""

    @patch("app.utils.geo.haversine_nm")
    def test_uses_off_positions(self, mock_haversine):
        from app.modules.sts_detector import _dark_dark_proximity

        mock_haversine.return_value = 3.5

        t0 = datetime(2025, 6, 1, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=6),
                          off_lat=36.5, off_lon=22.5,
                          on_lat=36.6, on_lon=22.6)
        gap_b = _make_gap(2, t0, t0 + timedelta(hours=6),
                          off_lat=36.52, off_lon=22.52,
                          on_lat=36.62, on_lon=22.62)

        result = _dark_dark_proximity(gap_a, gap_b)
        assert result == 3.5

    def test_no_positions_returns_none(self):
        from app.modules.sts_detector import _dark_dark_proximity

        t0 = datetime(2025, 6, 1, tzinfo=timezone.utc)
        gap_a = _make_gap(1, t0, t0 + timedelta(hours=6))
        gap_b = _make_gap(2, t0, t0 + timedelta(hours=6))

        result = _dark_dark_proximity(gap_a, gap_b)
        assert result is None


# ── Tests: Model creation ────────────────────────────────────────────────────

class TestModels:
    """Verify the new models can be instantiated."""

    def test_corridor_gap_baseline_instantiation(self):
        from app.models.corridor_gap_baseline import CorridorGapBaseline

        baseline = CorridorGapBaseline(
            corridor_id=1,
            window_start=datetime(2025, 6, 1),
            window_end=datetime(2025, 6, 8),
            gap_count=5,
            mean_gap_count=3.2,
            p95_threshold=8.0,
        )
        assert baseline.corridor_id == 1
        assert baseline.gap_count == 5
        assert baseline.mean_gap_count == 3.2
        assert baseline.p95_threshold == 8.0

    def test_satellite_tasking_candidate_instantiation(self):
        from app.models.satellite_tasking_candidate import SatelliteTaskingCandidate

        candidate = SatelliteTaskingCandidate(
            corridor_id=1,
            vessel_a_id=10,
            vessel_b_id=20,
            gap_overlap_hours=6.0,
            proximity_nm=3.5,
            confidence_level="high",
            risk_score_component=30,
        )
        assert candidate.vessel_a_id == 10
        assert candidate.vessel_b_id == 20
        assert candidate.confidence_level == "high"
        assert candidate.risk_score_component == 30
