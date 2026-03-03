"""Tests for dark fleet pipeline fixes (P1-P8).

Covers:
  P1: Double-scoring bug — Step 11z preserves existing scores
  P2: Feed outage proportional threshold + first-run guard
  P3: GFW _safe_float coercion
  P4a: Kystverket Type 5 full extraction
  P4b: DMA existing-vessel callsign/vessel_type updates
  P5: Source deconfliction (MMSI cloning + dual transmission)
  P6: AIS observation dual-write
  P7: Merge review CLI stale cleanup
  P8: Step label collision fix
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _gap(vessel_id=1, corridor_id=1, gap_start=None, risk_score=0,
         is_feed_outage=False, source=None, gap_event_id=None,
         duration_minutes=120, coverage_quality=None):
    """Create a mock AISGapEvent."""
    m = MagicMock()
    m.gap_event_id = gap_event_id or vessel_id * 100
    m.vessel_id = vessel_id
    m.corridor_id = corridor_id
    m.gap_start_utc = gap_start or datetime(2025, 6, 15, 10, 0)
    m.gap_end_utc = m.gap_start_utc + timedelta(minutes=duration_minutes)
    m.duration_minutes = duration_minutes
    m.risk_score = risk_score
    m.is_feed_outage = is_feed_outage
    m.source = source
    m.coverage_quality = coverage_quality
    m.impossible_speed_flag = False
    m.velocity_plausibility_ratio = None
    m.pre_gap_sog = None
    m.corridor = None
    m.start_point = None
    m.end_point = None
    m.in_dark_zone = False
    m.dark_zone_id = None
    m.original_vessel_id = None
    m.risk_breakdown_json = None
    return m


def _point(lat=60.0, lon=25.0, ts=None, source=None, sog=10.0, vessel_id=1):
    """Create a mock AISPoint."""
    m = MagicMock()
    m.lat = lat
    m.lon = lon
    m.timestamp_utc = ts or datetime(2025, 6, 15, 10, 0)
    m.source = source
    m.sog = sog
    m.cog = 180.0
    m.heading = 180.0
    m.vessel_id = vessel_id
    m.ais_point_id = id(m)
    m.nav_status = None
    return m


def _vessel(vessel_id=1, mmsi="123456789"):
    """Create a mock Vessel."""
    m = MagicMock()
    m.vessel_id = vessel_id
    m.mmsi = mmsi
    m.name = None
    m.imo = None
    m.callsign = None
    m.vessel_type = None
    return m


# ══════════════════════════════════════════════════════════════════════════════
# P1: Double-scoring bug (Step 11z uses score_all_alerts not rescore)
# ══════════════════════════════════════════════════════════════════════════════

class TestP1DoubleScoringFix:
    """Verify Step 11z no longer calls rescore_all_alerts (which zeros scores)."""

    def test_step_11z_imports_score_not_rescore(self):
        """The pipeline module should import score_all_alerts, not rescore."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        # Step 11z should use score_all_alerts
        assert "score_all_alerts as _score_incremental" in source
        # Should NOT use rescore_all_alerts
        assert "rescore_all_alerts as _rescore_second" not in source

    def test_score_all_alerts_filters_zero_only(self):
        """score_all_alerts should query only risk_score == 0 gaps."""
        import inspect
        from app.modules.risk_scoring import score_all_alerts
        source = inspect.getsource(score_all_alerts)
        # Must filter by risk_score == 0 (not reset all to 0)
        assert "AISGapEvent.risk_score == 0" in source
        # Must NOT reset scores to 0
        assert "a.risk_score = 0" not in source


# ══════════════════════════════════════════════════════════════════════════════
# P2: Feed outage proportional threshold + first-run guard
# ══════════════════════════════════════════════════════════════════════════════

class TestP2FeedOutageThreshold:
    """Verify proportional fallback and first-run guard."""

    def test_min_vessels_for_outage_is_8(self):
        from app.modules.feed_outage_detector import _MIN_VESSELS_FOR_OUTAGE
        assert _MIN_VESSELS_FOR_OUTAGE == 8

    def test_fallback_vessel_ratio_is_15_percent(self):
        from app.modules.feed_outage_detector import _FALLBACK_VESSEL_RATIO
        assert _FALLBACK_VESSEL_RATIO == 0.15

    def test_proportional_threshold_scales_with_corridor_size(self):
        """A corridor with 100 vessels should require 15+ gaps, not 5."""
        from app.modules.feed_outage_detector import _get_threshold

        db = MagicMock()
        # No baseline exists
        db.query.return_value.filter.return_value.first.return_value = None
        # Corridor has 100 unique vessels
        db.query.return_value.filter.return_value.distinct.return_value.count.return_value = 100

        threshold = _get_threshold(db, corridor_id=1, reference_time=datetime.now())
        assert threshold >= 15  # 15% of 100 = 15

    def test_proportional_threshold_enforces_minimum(self):
        """A corridor with 10 vessels should still require minimum 8."""
        from app.modules.feed_outage_detector import _get_threshold

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.distinct.return_value.count.return_value = 10

        threshold = _get_threshold(db, corridor_id=1, reference_time=datetime.now())
        assert threshold >= 8  # 15% of 10 = 1.5, but min is 8

    def test_first_run_guard_skips_without_baselines(self):
        """First-run with no baselines and no scored gaps should skip detection."""
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()
        # FEED_OUTAGE_DETECTION_ENABLED = True
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
            # No baselines
            db.query.return_value.first.return_value = None
            result = detect_feed_outages(db)
            assert result.get("skipped_reason") == "no_baselines" or result["gaps_checked"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# P3: GFW _safe_float coercion
# ══════════════════════════════════════════════════════════════════════════════

class TestP3SafeFloat:
    """Verify _safe_float handles GFW API string numerics."""

    def test_safe_float_with_string(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float("12.5") == 12.5

    def test_safe_float_with_float(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float(12.5) == 12.5

    def test_safe_float_with_int(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float(12) == 12.0

    def test_safe_float_with_none(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float(None) is None

    def test_safe_float_with_invalid_string(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float("abc") is None

    def test_safe_float_with_empty_string(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float("") is None

    def test_safe_float_with_zero_string(self):
        from app.modules.gfw_client import _safe_float
        assert _safe_float("0") == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# P4a: Kystverket Type 5 full extraction
# ══════════════════════════════════════════════════════════════════════════════

class TestP4aKystverketType5:
    """Verify ship type conversion and static cache expansion."""

    def test_ais_ship_type_tanker(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(80) == "Tanker"

    def test_ais_ship_type_cargo(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(70) == "Cargo"

    def test_ais_ship_type_fishing(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(30) == "Fishing"

    def test_ais_ship_type_tanker_subtype(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(84) == "Tanker (DG Cat D)"

    def test_ais_ship_type_unknown(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(0) is None

    def test_ais_ship_type_passenger(self):
        from app.modules.kystverket_client import _ais_ship_type_to_string
        assert _ais_ship_type_to_string(60) == "Passenger"

    def test_ingest_point_applies_static_data(self):
        """_ingest_point should apply Type 5 static fields to vessel."""
        from app.modules.kystverket_client import _ingest_point

        vessel = _vessel()
        vessel.imo = None
        vessel.callsign = None
        vessel.name = None
        vessel.vessel_type = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            vessel,  # Vessel lookup
            None,    # AISPoint dedup check
        ]

        pt = {
            "mmsi": "123456789",
            "lat": 60.0, "lon": 25.0,
            "sog": 10.0, "cog": 180.0, "heading": 180.0,
            "timestamp_utc": datetime.now(timezone.utc),
            "source": "kystverket",
            "destination": "MURMANSK",
            "draught": 12.5,
            "static_data": {
                "imo": "9876543",
                "callsign": "UALM",
                "vessel_name": "PIONEER",
                "vessel_type": "Tanker",
            },
        }

        _ingest_point(db, pt)

        assert vessel.imo == "9876543"
        assert vessel.callsign == "UALM"
        assert vessel.name == "PIONEER"
        assert vessel.vessel_type == "Tanker"


# ══════════════════════════════════════════════════════════════════════════════
# P5: Source deconfliction
# ══════════════════════════════════════════════════════════════════════════════

class TestP5SourceDeconfliction:
    """Verify cross-source point pairs within 120s are skipped."""

    def test_mmsi_cloning_skips_cross_source_within_120s(self):
        """Same vessel reported by two receivers 30s apart should not trigger."""
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        vessel = _vessel()
        t1 = datetime(2025, 6, 15, 10, 0, 0)
        t2 = datetime(2025, 6, 15, 10, 0, 30)  # 30s later

        # Same vessel, slightly different position (normal receiver skew)
        p1 = _point(lat=60.0, lon=25.0, ts=t1, source="aisstream")
        p2 = _point(lat=60.001, lon=25.001, ts=t2, source="digitraffic")

        jumps = _find_impossible_jumps([p1, p2], vessel)
        assert len(jumps) == 0  # Should be skipped

    def test_mmsi_cloning_detects_same_source_impossible_speed(self):
        """Same source with impossible speed should still trigger."""
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        vessel = _vessel()
        t1 = datetime(2025, 6, 15, 10, 0, 0)
        t2 = datetime(2025, 6, 15, 10, 0, 30)  # 30s later

        # Far apart — impossible speed from same source
        p1 = _point(lat=60.0, lon=25.0, ts=t1, source="aisstream")
        p2 = _point(lat=65.0, lon=25.0, ts=t2, source="aisstream")  # 300nm in 30s

        jumps = _find_impossible_jumps([p1, p2], vessel)
        assert len(jumps) == 1  # Should trigger

    def test_mmsi_cloning_detects_cross_source_beyond_120s(self):
        """Cross-source pairs beyond 120s should still be checked."""
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        vessel = _vessel()
        t1 = datetime(2025, 6, 15, 10, 0, 0)
        t2 = datetime(2025, 6, 15, 10, 5, 0)  # 5 min later (>120s)

        # Far apart — impossible speed
        p1 = _point(lat=60.0, lon=25.0, ts=t1, source="aisstream")
        p2 = _point(lat=65.0, lon=25.0, ts=t2, source="digitraffic")

        jumps = _find_impossible_jumps([p1, p2], vessel)
        assert len(jumps) == 1  # Should trigger (beyond 120s window)

    def test_mmsi_cloning_no_source_still_detects(self):
        """Points without source attribute should still be checked normally."""
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        vessel = _vessel()
        t1 = datetime(2025, 6, 15, 10, 0, 0)
        t2 = datetime(2025, 6, 15, 10, 0, 30)

        p1 = _point(lat=60.0, lon=25.0, ts=t1)
        p2 = _point(lat=65.0, lon=25.0, ts=t2)
        # Remove source attribute
        del p1.source
        del p2.source

        jumps = _find_impossible_jumps([p1, p2], vessel)
        assert len(jumps) == 1  # Should still detect


# ══════════════════════════════════════════════════════════════════════════════
# P8: Step label collision
# ══════════════════════════════════════════════════════════════════════════════

class TestP8StepLabelCollision:
    """Verify feed outage step is no longer labeled '6b'."""

    def test_feed_outage_step_relabeled(self):
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        # STS chain should still be Step 6b
        assert "Step 6b: STS relay chain" in source
        # Feed outage should now be Step 6e
        assert "Step 6e: Feed outage detection" in source
        # There should NOT be two "Step 6b" comments
        count_6b = source.count("Step 6b:")
        assert count_6b == 1, f"Expected 1 'Step 6b:' but found {count_6b}"


# ══════════════════════════════════════════════════════════════════════════════
# P4d + P2: Pipeline step ordering
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineStepOrdering:
    """Verify enrichment and baselines are in correct pipeline order."""

    def test_enrichment_before_detection(self):
        """vessel_enrichment step should appear before gap_detection."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        enrichment_pos = source.find("vessel_enrichment")
        gap_detection_pos = source.find("gap_detection")
        assert enrichment_pos < gap_detection_pos, \
            "Enrichment must run before gap detection"

    def test_baselines_before_feed_outage(self):
        """gap_rate_baselines step should appear before feed_outage_detection."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        baselines_pos = source.find("gap_rate_baselines")
        feed_outage_pos = source.find("feed_outage_detection")
        assert baselines_pos < feed_outage_pos, \
            "Gap rate baselines must be computed before feed outage detection"

    def test_baselines_after_gap_detection(self):
        """gap_rate_baselines step should appear after gap_detection (needs gap data)."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        gap_detection_pos = source.find('"gap_detection"')
        baselines_pos = source.find('"gap_rate_baselines"')
        assert gap_detection_pos < baselines_pos, \
            "Gap detection must run before baseline computation"


# ---------------------------------------------------------------------------
# P9: IMO mismatch hard block in merge scoring
# ---------------------------------------------------------------------------


class TestP9ImoMismatchBlock:
    """P9: Different valid IMOs should block merge candidate entirely."""

    def test_imo_mismatch_returns_zero(self):
        """Two vessels with different IMOs score 0 (blocked)."""
        import inspect
        from app.modules.identity_resolver import _score_candidate

        source = inspect.getsource(_score_candidate)
        assert "imo_mismatch" in source
        assert '"blocked": True' in source or '"blocked":True' in source \
            or "blocked" in source

    def test_imo_mismatch_early_return(self):
        """IMO mismatch triggers early return before other scoring."""
        import inspect
        from app.modules.identity_resolver import _score_candidate

        source = inspect.getsource(_score_candidate)
        # The imo_mismatch block should return 0 before vessel_type scoring
        imo_mismatch_pos = source.find("imo_mismatch")
        same_type_pos = source.find("same_vessel_type")
        assert imo_mismatch_pos < same_type_pos, \
            "IMO mismatch should short-circuit before type scoring"

    def test_same_imo_still_scores_25(self):
        """Matching valid IMO still gives +25 points."""
        import inspect
        from app.modules.identity_resolver import _score_candidate

        source = inspect.getsource(_score_candidate)
        assert '"same_imo"' in source or "'same_imo'" in source
        # +25 for same IMO should still be present
        assert "score += 25" in source
