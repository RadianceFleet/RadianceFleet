"""Tests for the investigation narrative generator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.narrative_generator import (
    DISCLAIMER,
    _build_evidence_pillars,
    _build_executive_summary,
    _build_timeline,
    _build_vessel_background,
    _compute_enrichment_completeness,
    _compute_narrative_strength,
    _completeness_warnings,
    _group_signals_by_category,
    _key_to_label,
    _recommended_actions,
    _render_signal,
    generate_narrative,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_gap(**overrides):
    gap = MagicMock()
    gap.gap_event_id = overrides.get("gap_event_id", 1)
    gap.vessel_id = overrides.get("vessel_id", 10)
    gap.gap_start_utc = overrides.get("gap_start_utc", datetime(2025, 6, 1, 12, 0, tzinfo=UTC))
    gap.gap_end_utc = overrides.get("gap_end_utc", datetime(2025, 6, 2, 0, 0, tzinfo=UTC))
    gap.duration_minutes = overrides.get("duration_minutes", 720)
    gap.risk_score = overrides.get("risk_score", 85)
    gap.risk_breakdown_json = overrides.get(
        "risk_breakdown_json",
        {
            "gap_duration_12h": 15,
            "watchlist_ofac_sdn": 30,
            "spoofing_circle": 20,
            "flag_change_to_high_risk": 10,
            "sts_event_detected": 10,
        },
    )
    gap.status = overrides.get("status", "under_review")
    gap.analyst_notes = overrides.get("analyst_notes", None)
    gap.corridor_id = overrides.get("corridor_id", None)
    return gap


def _make_vessel(**overrides):
    vessel = MagicMock()
    vessel.vessel_id = overrides.get("vessel_id", 10)
    vessel.mmsi = overrides.get("mmsi", "123456789")
    vessel.imo = overrides.get("imo", "9876543")
    vessel.name = overrides.get("name", "DARK SHADOW")
    vessel.flag = overrides.get("flag", "Cameroon")
    vessel.vessel_type = overrides.get("vessel_type", "Crude Oil Tanker")
    vessel.year_built = overrides.get("year_built", 2001)
    vessel.owner_name = overrides.get("owner_name", "Shadow Maritime LLC")
    vessel.pi_coverage_status = overrides.get("pi_coverage_status", "UNKNOWN")
    vessel.flag_risk_category = overrides.get("flag_risk_category", "high")
    vessel.psc_detained_last_12m = overrides.get("psc_detained_last_12m", False)
    vessel.psc_major_deficiencies_last_12m = overrides.get("psc_major_deficiencies_last_12m", 0)
    vessel.dark_fleet_confidence = overrides.get("dark_fleet_confidence", "HIGH")
    vessel.callsign = overrides.get("callsign", None)
    return vessel


# ── Signal template tests (6) ───────────────────────────────────────────────


class TestSignalTemplates:
    def test_gap_duration_tier1(self):
        result = _render_signal("gap_duration_12h", 15)
        assert "12 hours" in result
        assert "+15 pts" in result

    def test_watchlist_tier1(self):
        result = _render_signal("watchlist_ofac_sdn", 30)
        assert "OFAC SDN" in result
        assert "+30 pts" in result

    def test_spoofing_tier1(self):
        result = _render_signal("spoofing_circle", 20)
        assert "spoofing" in result.lower()
        assert "+20 pts" in result

    def test_unknown_key_fallback_tier3(self):
        result = _render_signal("some_completely_unknown_key", 5)
        assert "Some completely unknown key" in result
        assert "+5 pts" in result

    def test_negative_signals_excluded_from_groups(self):
        breakdown = {"gap_duration_12h": 15, "feed_outage_deduction": -5}
        grouped = _group_signals_by_category(breakdown)
        all_keys = []
        for signals in grouped.values():
            all_keys.extend([k for k, _ in signals])
        assert "feed_outage_deduction" not in all_keys

    def test_format_params_in_template(self):
        result = _render_signal("gap_duration_24h", 25)
        assert "+25 pts" in result
        assert "24 hours" in result


# ── Category grouping tests (4) ─────────────────────────────────────────────


class TestCategoryGrouping:
    def test_correct_grouping(self):
        breakdown = {
            "gap_duration_12h": 15,
            "watchlist_ofac_sdn": 30,
            "spoofing_circle": 20,
        }
        grouped = _group_signals_by_category(breakdown)
        assert "AIS_GAP" in grouped
        assert "WATCHLIST" in grouped
        assert "SPOOFING" in grouped

    def test_empty_categories_omitted(self):
        breakdown = {"gap_duration_12h": 15}
        grouped = _group_signals_by_category(breakdown)
        assert "WATCHLIST" not in grouped
        assert "STS_TRANSFER" not in grouped

    def test_order_respected_in_pillars(self):
        breakdown = {
            "spoofing_circle": 20,
            "watchlist_ofac_sdn": 30,
            "gap_duration_12h": 15,
        }
        grouped = _group_signals_by_category(breakdown)
        pillars = _build_evidence_pillars(grouped)
        names = [name for name, _ in pillars]
        # WATCHLIST before AIS_GAP before SPOOFING in CATEGORY_ORDER
        assert names.index("Sanctions & Watchlist Matches") < names.index(
            "AIS Transmission Gaps"
        )
        assert names.index("AIS Transmission Gaps") < names.index(
            "AIS Spoofing & Track Manipulation"
        )

    def test_all_seven_categories(self):
        breakdown = {
            "watchlist_ofac_sdn": 10,
            "gap_duration_12h": 10,
            "spoofing_circle": 10,
            "sts_event_detected": 10,
            "flag_change_to_high_risk": 10,
            "loiter_pre_gap": 10,
            "fleet_correlation": 10,
        }
        grouped = _group_signals_by_category(breakdown)
        assert len(grouped) == 7


# ── Edge case tests (5) ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_signal(self):
        breakdown = {"gap_duration_12h": 15}
        grouped = _group_signals_by_category(breakdown)
        assert len(grouped) == 1
        pillars = _build_evidence_pillars(grouped)
        assert len(pillars) == 1

    def test_no_signals(self):
        breakdown = {}
        grouped = _group_signals_by_category(breakdown)
        assert len(grouped) == 0
        pillars = _build_evidence_pillars(grouped)
        assert len(pillars) == 0

    def test_no_vessel(self):
        gap = _make_gap()
        summary = _build_executive_summary(None, gap, None, [("gap_duration_12h", 15)])
        assert "Unknown vessel" in summary
        assert "Unknown MMSI" in summary

    def test_missing_enrichment(self):
        vessel = _make_vessel(imo=None, owner_name=None, year_built=None)
        warnings = _completeness_warnings(vessel)
        assert any("IMO" in w for w in warnings)
        assert any("Owner" in w or "owner" in w.lower() for w in warnings)

    def test_alert_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = generate_narrative(9999, db)
        assert "error" in result
        assert "not found" in result["error"]


# ── Output format tests (3) ─────────────────────────────────────────────────


class TestOutputFormats:
    def _make_sections(self):
        return {
            "executive_summary": "Test summary.",
            "timeline": [
                (datetime(2025, 6, 1, 12, 0, tzinfo=UTC), "Gap start."),
                (datetime(2025, 6, 2, 0, 0, tzinfo=UTC), "Gap end."),
            ],
            "evidence_pillars": [
                ("AIS Transmission Gaps", "Gap detected."),
            ],
            "vessel_background": "Vessel info.",
            "confidence_assessment": "HIGH confidence.",
            "recommended_actions": ["Escalate.", "Review."],
            "caveats": DISCLAIMER,
        }

    def test_text_strips_markdown(self):
        from app.modules.narrative_generator import _render_text_narrative

        sections = self._make_sections()
        text = _render_text_narrative(sections, [])
        assert "#" not in text
        assert "**" not in text

    def test_markdown_has_headers(self):
        from app.modules.narrative_generator import _render_markdown_narrative

        sections = self._make_sections()
        md = _render_markdown_narrative(sections, [])
        assert "## Executive Summary" in md
        assert "## Timeline" in md
        assert "## Evidence Pillars" in md

    def test_html_has_tags(self):
        from app.modules.narrative_generator import _render_html_narrative

        sections = self._make_sections()
        html_out = _render_html_narrative(sections, [])
        assert "<h2>" in html_out
        assert "<h3>" in html_out
        assert "<p>" in html_out
        assert "<ul>" in html_out


# ── Narrative quality tests (4) ──────────────────────────────────────────────


class TestNarrativeQuality:
    def test_summary_has_vessel_name(self):
        vessel = _make_vessel(name="OCEAN SPIRIT")
        gap = _make_gap()
        summary = _build_executive_summary(
            vessel, gap, "HIGH", [("gap_duration_12h", 15)]
        )
        assert "OCEAN SPIRIT" in summary

    def test_timeline_chronological(self):
        gap = _make_gap(
            gap_start_utc=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
            gap_end_utc=datetime(2025, 6, 2, 0, 0, tzinfo=UTC),
        )
        linked = {"spoofing": [], "loitering": [], "sts": []}
        timeline = _build_timeline(gap, linked)
        timestamps = [ts for ts, _ in timeline]
        assert timestamps == sorted(timestamps)

    def test_strength_high(self):
        # 10 signals, 5 categories, full enrichment -> 1.0
        strength = _compute_narrative_strength(10, 5, 1.0)
        assert strength == 1.0

    def test_strength_low(self):
        # 1 signal, 1 category, no enrichment -> low
        strength = _compute_narrative_strength(1, 1, 0.0)
        assert strength < 0.2


# ── Integration test (1) ────────────────────────────────────────────────────


class TestIntegration:
    def test_full_flow_with_rich_data(self):
        gap = _make_gap(
            risk_breakdown_json={
                "gap_duration_24h": 25,
                "watchlist_ofac_sdn": 30,
                "spoofing_circle": 20,
                "flag_change_to_high_risk": 10,
                "sts_event_detected": 10,
                "loiter_pre_gap": 8,
                "fleet_correlation": 5,
            }
        )
        vessel = _make_vessel(dark_fleet_confidence="HIGH")

        db = MagicMock()
        # gap query
        gap_query = MagicMock()
        gap_query.filter.return_value.first.return_value = gap

        vessel_query = MagicMock()
        vessel_query.filter.return_value.first.return_value = vessel

        # Linked anomaly queries return empty
        empty_query = MagicMock()
        empty_query.filter.return_value.all.return_value = []

        def side_effect(model):
            from app.models.gap_event import AISGapEvent
            from app.models.vessel import Vessel

            if model is AISGapEvent:
                return gap_query
            if model is Vessel:
                return vessel_query
            return empty_query

        db.query.side_effect = side_effect

        result = generate_narrative(1, db, output_format="md")

        assert "error" not in result
        assert "narrative" in result
        assert result["format"] == "md"
        assert result["strength"] > 0
        assert "DARK SHADOW" in result["narrative"]
        assert "## Executive Summary" in result["narrative"]
        assert "## Timeline" in result["narrative"]
        assert "## Evidence Pillars" in result["narrative"]
        assert "DISCLAIMER" in result["narrative"] or "disclaimer" in result["narrative"].lower()


# ── Completeness test (1) ───────────────────────────────────────────────────


class TestCompleteness:
    def test_missing_imo_triggers_warning(self):
        vessel = _make_vessel(imo=None)
        warnings = _completeness_warnings(vessel)
        assert any("IMO" in w for w in warnings)


# ── Additional helper tests ─────────────────────────────────────────────────


class TestHelpers:
    def test_key_to_label(self):
        assert _key_to_label("gap_duration_12h") == "Gap duration 12h"
        assert _key_to_label("watchlist_ofac_sdn") == "Watchlist ofac sdn"

    def test_enrichment_completeness_full(self):
        vessel = _make_vessel(
            imo="1234567",
            name="TEST",
            flag="Panama",
            vessel_type="Tanker",
            year_built=2010,
            owner_name="Test Inc",
            pi_coverage_status="COVERED",
            flag_risk_category="high",
        )
        assert _compute_enrichment_completeness(vessel) == 1.0

    def test_enrichment_completeness_none(self):
        assert _compute_enrichment_completeness(None) == 0.0

    def test_recommended_actions_confirmed(self):
        actions = _recommended_actions("CONFIRMED", {"WATCHLIST"})
        assert any("Escalate" in a or "senior" in a.lower() for a in actions)
        assert any("sanctions" in a.lower() for a in actions)

    def test_recommended_actions_low(self):
        actions = _recommended_actions("LOW", set())
        assert any("Monitor" in a or "monitor" in a.lower() for a in actions)

    def test_vessel_background_with_data(self):
        vessel = _make_vessel(
            psc_detained_last_12m=True,
            psc_major_deficiencies_last_12m=3,
        )
        bg = _build_vessel_background(vessel)
        assert "Cameroon" in bg
        assert "PSC" in bg or "detention" in bg.lower()

    def test_vessel_background_none(self):
        bg = _build_vessel_background(None)
        assert "No vessel record" in bg
