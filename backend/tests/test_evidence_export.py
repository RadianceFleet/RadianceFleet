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
