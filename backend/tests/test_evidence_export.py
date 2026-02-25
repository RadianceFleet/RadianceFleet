"""Tests for evidence card export with regional coverage metadata."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


def _mock_db_for_export(corridor_name="Mediterranean STS Zone"):
    gap = MagicMock()
    gap.gap_event_id = 42
    gap.status = "under_review"
    gap.gap_start_utc = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    gap.gap_end_utc = datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc)
    gap.duration_minutes = 720
    gap.risk_score = 65
    gap.risk_breakdown_json = {}
    gap.impossible_speed_flag = False
    gap.in_dark_zone = False
    gap.analyst_notes = None
    gap.corridor_id = 1
    gap.start_point_id = None
    gap.end_point_id = None
    gap.velocity_plausibility_ratio = None
    gap.max_plausible_distance_nm = None
    gap.actual_gap_distance_nm = None
    gap.vessel_id = 1

    vessel = MagicMock()
    vessel.name = "TEST VESSEL"
    vessel.mmsi = "123456789"
    vessel.imo = None
    vessel.flag = "PA"
    vessel.vessel_type = "tanker"

    corridor = MagicMock()
    corridor.name = corridor_name

    db = MagicMock()

    # Sequence: gap, vessel, corridor, last_point (None), first_point (None), sat_check (None)
    db.query.return_value.filter.return_value.first.side_effect = [
        gap, vessel, corridor, None, None, None
    ]
    db.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
        None, None, None
    ]
    db.add = MagicMock()
    db.commit = MagicMock()

    # EvidenceCard insert
    card_mock = MagicMock()
    card_mock.evidence_card_id = 1
    db.add = MagicMock()

    return db, gap


def test_evidence_card_markdown_contains_coverage_section():
    from app.modules.evidence_export import export_evidence_card
    db, _ = _mock_db_for_export("Mediterranean STS Zone")
    result = export_evidence_card(42, "md", db)
    assert "error" not in result
    content = result["content"]
    assert "coverage" in content.lower() or "Coverage" in content


def test_evidence_card_coverage_identifies_black_sea():
    from app.modules.evidence_export import _corridor_coverage
    quality, desc = _corridor_coverage("Black Sea Export Route")
    assert quality == "POOR"
    assert "falsified" in desc.lower() or "russian" in desc.lower() or "no adequate" in desc.lower()


def test_evidence_card_coverage_unknown_region():
    from app.modules.evidence_export import _corridor_coverage
    quality, desc = _corridor_coverage("Unknown Region XYZ")
    assert quality == "UNKNOWN"


def test_evidence_card_coverage_none_corridor():
    from app.modules.evidence_export import _corridor_coverage
    quality, desc = _corridor_coverage(None)
    assert quality == "UNKNOWN"


def test_evidence_export_blocked_when_status_new():
    """Evidence card export must be blocked when gap.status == 'new' (NFR7 analyst review gate)."""
    from app.modules.evidence_export import export_evidence_card
    gap = MagicMock()
    gap.gap_event_id = 99
    gap.status = "new"

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = gap

    result = export_evidence_card(99, "md", db)
    assert "error" in result
    assert "analyst review" in result["error"].lower() or "status" in result["error"].lower()


def test_evidence_card_score_snapshot_populated():
    """After export, the EvidenceCard record should have score_snapshot and breakdown_snapshot."""
    from app.modules.evidence_export import export_evidence_card

    db, gap = _mock_db_for_export("Mediterranean STS Zone")
    gap.risk_score = 72
    gap.risk_breakdown_json = {"gap_duration_12h": 30, "corridor_sts_zone": 20}

    result = export_evidence_card(42, "json", db)
    assert "error" not in result

    # Verify db.add was called with an EvidenceCard that has snapshots
    add_calls = db.add.call_args_list
    assert len(add_calls) > 0, "Expected db.add to be called with EvidenceCard"
    card = add_calls[-1][0][0]  # last add() call, first positional arg
    assert card.score_snapshot == 72
    assert card.breakdown_snapshot == {"gap_duration_12h": 30, "corridor_sts_zone": 20}
