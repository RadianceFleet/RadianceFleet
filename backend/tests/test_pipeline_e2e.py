"""End-to-end pipeline test: Ingest AIS → detect gaps → score → verify output shape.

Uses mock objects (no database) following patterns from test_risk_scoring_complete.py.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.models.ais_point import AISPoint
from app.modules.gap_detector import detect_gaps_for_vessel
from app.modules.risk_scoring import compute_gap_score, load_scoring_config


def test_pipeline_detect_then_score():
    """Full pipeline: AIS points with 6h gap → detect_gaps → score → verify."""
    base = datetime(2026, 1, 10, 0, 0)

    # ── Step 1: Create mock AIS points for a vessel with a 6h gap ──────────
    p1 = MagicMock()
    p1.ais_point_id = 1
    p1.vessel_id = 42
    p1.lat = 55.0
    p1.lon = 25.0
    p1.sog = 14.0
    p1.cog = 180.0
    p1.heading = None
    p1.timestamp_utc = base
    p1.nav_status = None

    p2 = MagicMock()
    p2.ais_point_id = 2
    p2.vessel_id = 42
    p2.lat = 56.0
    p2.lon = 25.5
    p2.sog = 0.0
    p2.cog = 0.0
    p2.heading = None
    p2.timestamp_utc = base + timedelta(hours=6)
    p2.nav_status = None

    vessel = MagicMock()
    vessel.vessel_id = 42
    vessel.deadweight = 80_000
    vessel.vessel_type = "Crude Oil Tanker"

    def query_side_effect(model):
        mock_chain = MagicMock()
        if model is AISPoint:
            mock_chain.filter.return_value.order_by.return_value.all.return_value = [p1, p2]
        else:
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect
    mock_db.get = MagicMock(return_value=None)

    # ── Step 2: Call detect_gaps_for_vessel ─────────────────────────────────
    gap_count = detect_gaps_for_vessel(mock_db, vessel)
    assert gap_count >= 1, "Expected at least one gap detected"

    # ── Step 3: Verify gap event attributes ────────────────────────────────
    added_objects = [call.args[0] for call in mock_db.add.call_args_list]
    # AISGapEvent is the one with duration_minutes and vessel_id as real attrs
    from app.models.gap_event import AISGapEvent
    gap_events = [obj for obj in added_objects if isinstance(obj, AISGapEvent)]

    assert len(gap_events) >= 1, "Expected at least one AISGapEvent created"
    gap = gap_events[0]
    assert gap.vessel_id == 42
    assert gap.duration_minutes == 360  # 6h = 360 min
    assert gap.pre_gap_sog == 14.0
    assert gap.status == "new"
    assert gap.risk_score == 0  # scoring runs separately

    # ── Step 4: Score the detected gap ─────────────────────────────────────
    config = load_scoring_config()

    # Build a mock gap with vessel relationship (compute_gap_score reads gap.vessel)
    mock_gap = MagicMock()
    mock_gap.gap_event_id = 1
    mock_gap.vessel_id = 42
    mock_gap.duration_minutes = gap.duration_minutes
    mock_gap.impossible_speed_flag = gap.impossible_speed_flag
    mock_gap.velocity_plausibility_ratio = gap.velocity_plausibility_ratio
    mock_gap.in_dark_zone = False
    mock_gap.dark_zone_id = None
    mock_gap.gap_start_utc = p1.timestamp_utc
    mock_gap.gap_end_utc = p2.timestamp_utc

    mock_vessel = MagicMock()
    mock_vessel.deadweight = 80_000
    mock_vessel.flag_risk_category = "unknown"
    mock_vessel.year_built = None
    mock_vessel.ais_class = "unknown"
    mock_vessel.flag = None
    mock_vessel.mmsi_first_seen_utc = None
    mock_vessel.vessel_laid_up_30d = False
    mock_vessel.vessel_laid_up_60d = False
    mock_vessel.vessel_laid_up_in_sts_zone = False
    mock_vessel.vessel_id = 42

    mock_gap.vessel = mock_vessel
    mock_gap.corridor = None

    score, breakdown = compute_gap_score(
        mock_gap, config, pre_gap_sog=gap.pre_gap_sog,
    )

    # ── Step 5: Verify score > 0 and breakdown has expected keys ───────────
    assert score > 0, f"Expected positive score for 6h gap, got {score}"
    assert isinstance(breakdown, dict)
    assert "_final_score" in breakdown
    assert "_additive_subtotal" in breakdown
    assert "_corridor_multiplier" in breakdown
    assert "_vessel_size_multiplier" in breakdown
    assert breakdown["_final_score"] == score
