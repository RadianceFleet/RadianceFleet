"""Tests for OSINT-informed scoring and detection improvements.

Covers:
- Phase 1b: Sanctioned port database + scoring
- Phase 1c: Time-decay multiplier for temporal signals
- Phase 2a: KSE shadow fleet archetype score
- Phase 2b: Distance-to-EEZ-boundary gap signal
- Phase 3a: Class-median DWT fallback
- Phase 3b: Vessel name quality signals
- Phase 3c: MID table completeness
- Phase 4a: Per-vessel behavioral baseline (Z-score)
- Phase 4b: Pillar score separation
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1c: Temporal Recency Factor
# ─────────────────────────────────────────────────────────────────────────────


class TestTemporalRecencyFactor:
    def _get_fn(self):
        from app.modules.risk_scoring import _temporal_recency_factor

        return _temporal_recency_factor

    def test_none_signal_returns_1(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5, 12, 0)
        assert fn(None, gap_dt) == 1.0

    def test_within_7d_returns_2x(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5, 12, 0)
        signal_dt = gap_dt - timedelta(days=5)
        assert fn(signal_dt, gap_dt) == 2.0

    def test_within_30d_returns_1_5x(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5, 12, 0)
        signal_dt = gap_dt - timedelta(days=20)
        assert fn(signal_dt, gap_dt) == 1.5

    def test_within_90d_returns_1x(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5, 12, 0)
        signal_dt = gap_dt - timedelta(days=60)
        assert fn(signal_dt, gap_dt) == 1.0

    def test_older_than_90d_returns_decay(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5, 12, 0)
        signal_dt = gap_dt - timedelta(days=120)
        assert fn(signal_dt, gap_dt) == 0.8

    def test_exactly_7d_is_recent(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5)
        signal_dt = gap_dt - timedelta(days=7)
        assert fn(signal_dt, gap_dt) == 2.0

    def test_exactly_30d_is_recent(self):
        fn = self._get_fn()
        gap_dt = datetime(2026, 3, 5)
        signal_dt = gap_dt - timedelta(days=30)
        assert fn(signal_dt, gap_dt) == 1.5


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2b: EEZ Boundary Distance Utility
# ─────────────────────────────────────────────────────────────────────────────


class TestEEZBoundaryDistance:
    def test_returns_tuple_distance_name(self):
        from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

        dist, name = distance_to_nearest_eez_boundary_nm(59.5, 27.0)
        assert isinstance(dist, float)
        assert isinstance(name, str)
        assert dist >= 0

    def test_point_very_close_to_russian_baltic_eez(self):
        from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

        # Point near Gulf of Finland boundary
        dist, name = distance_to_nearest_eez_boundary_nm(59.5, 27.0)
        assert dist < 50  # Should be well under 50nm from the boundary point

    def test_point_far_from_all_boundaries(self):
        from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

        # Mid-Atlantic, far from all listed boundaries
        dist, name = distance_to_nearest_eez_boundary_nm(0.0, -30.0)
        # No boundary segment is nearby mid-Atlantic
        assert dist > 100

    def test_non_negative_distance(self):
        from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

        for lat, lon in [(0, 0), (45, 45), (-30, 120), (70, 20)]:
            dist, _ = distance_to_nearest_eez_boundary_nm(lat, lon)
            assert dist >= 0

    def test_iranian_gulf_proximity(self):
        from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

        # Point near Iranian EEZ boundary in Persian Gulf
        dist, name = distance_to_nearest_eez_boundary_nm(26.0, 57.5)
        assert dist < 100  # Should be near the boundary

    def test_segment_distance_not_haversine_only(self):
        """Ensure segment projection works (not just endpoint-to-endpoint)."""
        from app.utils.eez_boundaries import _point_to_segment_distance_nm

        # Point perpendicular to a horizontal segment
        d = _point_to_segment_distance_nm(0, 1, 0, 0, 0, 2)  # lon, lat, ax, ay, bx, by
        assert d < 60  # 1 degree lat ≈ 60 NM


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3a: Class-Median DWT Fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestClassMedianDWT:
    def test_fills_null_dwt_for_crude_tanker(self):
        from app.modules.vessel_enrichment import _VESSEL_TYPE_MEDIAN_DWT, apply_class_median_dwt

        mock_vessel = MagicMock()
        mock_vessel.deadweight = None
        mock_vessel.vessel_type = "Crude Oil Tanker"
        mock_vessel.is_heuristic_dwt = False

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vessel]

        apply_class_median_dwt(mock_db)

        assert mock_vessel.deadweight == float(_VESSEL_TYPE_MEDIAN_DWT["Crude Oil Tanker"])
        assert mock_vessel.is_heuristic_dwt is True

    def test_skips_vessel_with_existing_dwt(self):
        """Vessels with existing DWT should NOT be returned by the query (filter handles it)."""
        from app.modules.vessel_enrichment import apply_class_median_dwt

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []  # No null-DWT vessels

        result = apply_class_median_dwt(mock_db)
        assert result["filled"] == 0

    def test_skips_unknown_vessel_type(self):
        from app.modules.vessel_enrichment import apply_class_median_dwt

        mock_vessel = MagicMock()
        mock_vessel.deadweight = None
        mock_vessel.vessel_type = "Unknown Vessel Type XYZ"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vessel]

        result = apply_class_median_dwt(mock_db)
        assert result["filled"] == 0
        assert result["skipped"] == 1

    def test_median_values_are_plausible(self):
        from app.modules.vessel_enrichment import _VESSEL_TYPE_MEDIAN_DWT

        assert _VESSEL_TYPE_MEDIAN_DWT["Crude Oil Tanker"] > 100_000
        assert _VESSEL_TYPE_MEDIAN_DWT["Product Tanker"] < 80_000
        assert _VESSEL_TYPE_MEDIAN_DWT["Bulk Carrier"] > 50_000


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3c: MID Table Completeness
# ─────────────────────────────────────────────────────────────────────────────


class TestMIDTableCompleteness:
    def test_belize_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("312") == "BZ"

    def test_iraq_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("401") == "IQ"

    def test_qatar_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("466") == "QA"

    def test_bangladesh_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("405") == "BD"

    def test_myanmar_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("506") == "MM"

    def test_nigeria_coverage(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("657") == "NG"

    def test_ghana_mid(self):
        from app.utils.vessel_identity import MID_TO_FLAG

        assert MID_TO_FLAG.get("627") == "GH"

    def test_mmsi_to_flag_uses_new_mids(self):
        from app.utils.vessel_identity import mmsi_to_flag

        # BZ MID 312
        assert mmsi_to_flag("312001234") == "BZ"


# ─────────────────────────────────────────────────────────────────────────────
# Shared test fixture builder for compute_gap_score tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_test_gap(
    name="TEST",
    flag="KM",
    flag_risk="high_risk",
    vtype="Crude Oil Tanker",
    dwt=100_000,
    year_built=2000,
    mmsi="620123456",
):
    """Create a gap mock with vessel attached via gap.vessel, matching actual ORM pattern."""
    from app.modules.risk_scoring import load_scoring_config

    config = load_scoring_config()

    gap = MagicMock()
    gap.gap_id = 1
    gap.vessel_id = 1
    gap.gap_start_utc = datetime(2026, 3, 1, 0, 0)
    gap.gap_end_utc = datetime(2026, 3, 1, 12, 0)
    gap.gap_duration_hours = 12.0
    gap.duration_minutes = 720  # 12 hours — must be a real int for duration_h computation
    gap.in_dark_zone = False
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    gap.gap_on_lat = None
    gap.gap_on_lon = None
    gap.corridor = None
    gap.corridor_id = None
    gap.start_point = None
    gap.max_plausible_distance_nm = 200.0
    gap.imo_at_gap_start = None
    gap.source = "ais"
    gap.velocity_plausibility_ratio = None
    gap.is_feed_outage = False
    gap.coverage_quality = None

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.name = name
    vessel.flag = flag
    vessel.flag_risk_category = MagicMock()
    vessel.flag_risk_category.value = flag_risk
    vessel.vessel_type = vtype
    vessel.deadweight = dwt
    vessel.year_built = year_built
    vessel.imo = "9123456"
    vessel.mmsi = mmsi
    vessel.pi_coverage_status = MagicMock()
    vessel.pi_coverage_status.value = "active"
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.ais_class = "A"
    vessel.length = 250
    vessel.merged_into_vessel_id = None

    gap.vessel = vessel
    return gap, config


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3b: Vessel Name Quality Signals
# ─────────────────────────────────────────────────────────────────────────────


class TestNameQualitySignals:
    def test_empty_name_triggers_no_name_at_all(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(name="")
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "no_name_at_all" in breakdown
        assert breakdown["no_name_at_all"] == 20

    def test_generic_name_triggers_invalid_metadata(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(name="TANKER")
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "invalid_metadata_generic_name" in breakdown
        assert breakdown["invalid_metadata_generic_name"] == 15  # boosted from 10

    def test_all_caps_number_pattern(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(name="SHIP 22")
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "name_all_caps_numbers" in breakdown

    def test_normal_name_no_penalty(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(name="CRUDE OCEAN STAR")
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "no_name_at_all" not in breakdown
        assert "invalid_metadata_generic_name" not in breakdown


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2a: KSE Shadow Fleet Archetype
# ─────────────────────────────────────────────────────────────────────────────


class TestKSEShadowFleetArchetype:
    def test_strong_archetype_match_4_plus(self):
        """Vessel with 4+ KSE dimensions: high-risk flag, old age, tanker type, large DWT."""
        from app.modules.risk_scoring import compute_gap_score

        # PW=Palau (high_risk), >15y old, crude tanker, >80k DWT, high_risk flag → 5/5 hits
        gap, config = _make_test_gap(
            flag="PW",
            flag_risk="high_risk",
            vtype="Crude Oil Tanker",
            dwt=100_000,
            year_built=2000,
            mmsi="511123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "kse_shadow_profile_strong" in breakdown
        assert breakdown["kse_shadow_profile_strong"] == 35

    def test_moderate_archetype_match_3(self):
        """Vessel with 3 KSE dimensions (open registry, old, large tanker)."""
        from app.modules.risk_scoring import compute_gap_score

        # PA=Panama (medium risk, open registry), old tanker, large DWT
        gap, config = _make_test_gap(
            flag="PA",
            flag_risk="medium_risk",
            vtype="Crude Oil Tanker",
            dwt=100_000,
            year_built=2000,
            mmsi="351123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        # 3 hits minimum: age≥15, type=tanker, DWT>80k → profile_match
        assert "kse_shadow_profile_match" in breakdown or "kse_shadow_profile_strong" in breakdown

    def test_young_small_low_risk_vessel_no_kse(self):
        """Young, small, low-risk flag vessel should not trigger KSE archetype."""
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(
            flag="NO",
            flag_risk="low_risk",
            vtype="Bulk Carrier",
            dwt=5000,
            year_built=2020,
            mmsi="257123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "kse_shadow_profile_strong" not in breakdown
        assert "kse_shadow_profile_match" not in breakdown


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4b: Pillar Score Separation
# ─────────────────────────────────────────────────────────────────────────────


class TestPillarScoreSeparation:
    def test_pillar_keys_present_in_breakdown(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(
            flag="NO",
            flag_risk="low_risk",
            vtype="Bulk Carrier",
            dwt=50_000,
            year_built=2015,
            mmsi="257123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert "_pillar_vessel" in breakdown
        assert "_pillar_position" in breakdown
        assert "_pillar_voyage" in breakdown

    def test_pillar_values_are_numeric(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(
            flag="NO",
            flag_risk="low_risk",
            vtype="Bulk Carrier",
            dwt=50_000,
            year_built=2015,
            mmsi="257123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert isinstance(breakdown["_pillar_vessel"], (int, float))
        assert isinstance(breakdown["_pillar_position"], (int, float))
        assert isinstance(breakdown["_pillar_voyage"], (int, float))

    def test_pillar_values_non_negative(self):
        from app.modules.risk_scoring import compute_gap_score

        gap, config = _make_test_gap(
            flag="NO",
            flag_risk="low_risk",
            vtype="Bulk Carrier",
            dwt=50_000,
            year_built=2015,
            mmsi="257123456",
        )
        score, breakdown = compute_gap_score(gap, config=config, db=None)
        assert breakdown["_pillar_vessel"] >= 0
        assert breakdown["_pillar_position"] >= 0
        assert breakdown["_pillar_voyage"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1b: Sanctioned port model
# ─────────────────────────────────────────────────────────────────────────────


class TestSanctionedPortModel:
    def test_port_has_is_sanctioned_attribute(self):
        from app.models.port import Port

        p = Port(name="Test", country="RU", major_port=True)
        assert hasattr(p, "is_sanctioned")

    def test_is_sanctioned_column_exists_on_model(self):
        """Verify is_sanctioned column is declared on the ORM model."""
        from sqlalchemy import inspect as sa_inspect

        from app.models.port import Port

        cols = {c.key for c in sa_inspect(Port).columns}
        assert "is_sanctioned" in cols

    def test_sanctioned_terminals_set_is_defined(self):
        from scripts.seed_ports import _SANCTIONED_TERMINALS

        assert "Primorsk" in _SANCTIONED_TERMINALS
        assert "Novorossiysk" in _SANCTIONED_TERMINALS
        assert "Nakhodka/Kozmino" in _SANCTIONED_TERMINALS
        assert len(_SANCTIONED_TERMINALS) >= 5
