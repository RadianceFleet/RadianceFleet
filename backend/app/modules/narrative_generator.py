"""Investigation narrative generator for AIS gap alerts.

Generates human-readable investigation narratives from risk breakdown signals,
vessel enrichment, and linked anomalies. Supports text, Markdown, and HTML output.

Narrative sections:
  1. Executive Summary
  2. Timeline
  3. Evidence Pillars (per active category)
  4. Vessel Background
  5. Confidence Assessment
  6. Recommended Actions
  7. Caveats
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.modules.confidence_classifier import _categorize_key

logger = logging.getLogger(__name__)

# ── Evidence category display names and order ────────────────────────────────

CATEGORY_ORDER = [
    "WATCHLIST",
    "AIS_GAP",
    "SPOOFING",
    "STS_TRANSFER",
    "IDENTITY_CHANGE",
    "LOITERING",
    "FLEET_PATTERN",
]

CATEGORY_DISPLAY = {
    "WATCHLIST": "Sanctions & Watchlist Matches",
    "AIS_GAP": "AIS Transmission Gaps",
    "SPOOFING": "AIS Spoofing & Track Manipulation",
    "STS_TRANSFER": "Ship-to-Ship Transfers",
    "IDENTITY_CHANGE": "Identity & Flag Changes",
    "LOITERING": "Loitering Behavior",
    "FLEET_PATTERN": "Fleet-Level Patterns",
}

# ── Disclaimer ───────────────────────────────────────────────────────────────

DISCLAIMER = (
    "DISCLAIMER: This is investigative triage, not a legal determination. "
    "This tool identifies patterns warranting further investigation. "
    "No conclusions about sanctions violations or criminal activity should be drawn "
    "from this output without independent expert verification."
)

# ── Tier 1: Hand-written prose templates (~40 keys) ──────────────────────────
# {value} is replaced with the signal point value.

TIER1_TEMPLATES: dict[str, str] = {
    # AIS gaps
    "gap_duration_12h": "AIS signal was lost for over 12 hours, a significant gap that warrants investigation (+{value} pts).",
    "gap_duration_24h": "The vessel went dark for over 24 hours, strongly suggesting deliberate AIS shutdown (+{value} pts).",
    "gap_duration_48h": "AIS transmission was absent for more than 48 hours, an extreme gap duration seen almost exclusively in sanctions evasion (+{value} pts).",
    "gap_duration_6h": "The vessel experienced an AIS gap exceeding 6 hours (+{value} pts).",
    "gap_duration_3h": "A 3-hour AIS gap was detected (+{value} pts).",
    "gap_frequency_high": "This vessel has an unusually high frequency of AIS gaps, indicating a pattern of deliberate signal suppression (+{value} pts).",
    "gap_frequency_moderate": "Multiple AIS gaps have been recorded for this vessel, suggesting a recurring pattern (+{value} pts).",
    "gap_frequency_repeat_corridor": "Repeated AIS gaps in the same corridor indicate deliberate route-specific concealment (+{value} pts).",
    # Speed anomalies
    "speed_impossible": "The vessel's reappearance position requires physically impossible speeds, indicating data manipulation or identity fraud (+{value} pts).",
    "speed_spike_before_gap": "An unusual speed spike was recorded just before the AIS gap began (+{value} pts).",
    "speed_spoof_constant": "Speed remained suspiciously constant through changing conditions, consistent with spoofed AIS data (+{value} pts).",
    # Spoofing
    "spoofing_circle": "Circular AIS track pattern detected, a known GPS spoofing signature (+{value} pts).",
    "spoofing_identity_swap": "Evidence of AIS identity swapping between vessels was detected (+{value} pts).",
    "spoofing_position_jump": "Impossible position jumps in the AIS track suggest deliberate manipulation (+{value} pts).",
    "spoofing_track_replay": "The vessel's AIS track appears to replay a previously recorded route (+{value} pts).",
    "spoofing_stale_data": "The AIS data contains stale or recycled position reports (+{value} pts).",
    "spoofing_deliberate_circle": "Deliberate circular spoofing pattern detected, commonly used to mask true position (+{value} pts).",
    "spoofing_equipment_circle": "Circular AIS pattern detected, possibly from equipment malfunction (+{value} pts).",
    "spoofing_stationary_circle": "Stationary circular pattern suggests either GPS interference or equipment fault (+{value} pts).",
    # Watchlist
    "watchlist_ofac_sdn": "Vessel appears on the OFAC SDN sanctions list (+{value} pts).",
    "watchlist_eu_sanctions": "Vessel is listed under EU sanctions (+{value} pts).",
    "watchlist_kse_shadow_fleet": "Vessel is identified in the KSE shadow fleet database (+{value} pts).",
    "watchlist_un_sanctions": "Vessel appears on UN sanctions list (+{value} pts).",
    "watchlist_match": "Vessel matched against one or more sanctions/watchlists (+{value} pts).",
    # Flag changes
    "flag_change_to_high_risk": "The vessel recently changed to a high-risk flag state, a common sanctions evasion tactic (+{value} pts).",
    "flag_change_rapid": "Rapid flag changes detected, suggesting flag-hopping to evade detection (+{value} pts).",
    "flag_AND_name_change": "Both flag and name were changed simultaneously, a strong indicator of identity laundering (+{value} pts).",
    "flag_hopping_3plus": "Three or more flag changes detected within a short period (+{value} pts).",
    # STS
    "sts_event_detected": "A ship-to-ship transfer event was detected near the gap period (+{value} pts).",
    "sts_event_in_corridor": "STS transfer occurred in a known smuggling corridor (+{value} pts).",
    "sts_dark_dark": "Both vessels in the STS transfer had their AIS turned off, indicating a covert operation (+{value} pts).",
    # Loitering
    "loiter_pre_gap": "The vessel was loitering before the AIS gap, consistent with waiting for a covert rendezvous (+{value} pts).",
    "loiter_in_sts_zone": "Loitering detected within an STS transfer zone (+{value} pts).",
    "loiter_repeated": "Repeated loitering events detected at the same location (+{value} pts).",
    # Movement
    "impossible_reappear_speed": "Reappearance requires physically impossible transit speed (+{value} pts).",
    "near_impossible_reappear": "Reappearance location is at the extreme edge of what is physically possible (+{value} pts).",
    "dark_zone_gap": "AIS gap occurred within a known GPS jamming/dark zone (+{value} pts).",
    "at_sea_no_port_call": "Vessel remained at sea for an extended period with no port calls recorded (+{value} pts).",
}

# ── Tier 2: Pattern-based prefix templates (~50 keys) ────────────────────────

TIER2_PREFIXES: dict[str, str] = {
    "gap_duration_": "AIS gap duration signal: {key_label} (+{value} pts).",
    "gap_frequency_": "AIS gap frequency signal: {key_label} (+{value} pts).",
    "speed_": "Speed anomaly detected: {key_label} (+{value} pts).",
    "spoofing_": "AIS spoofing indicator: {key_label} (+{value} pts).",
    "watchlist_": "Watchlist match: {key_label} (+{value} pts).",
    "flag_change_": "Flag state change: {key_label} (+{value} pts).",
    "flag_hopping_": "Flag-hopping pattern: {key_label} (+{value} pts).",
    "sts_": "Ship-to-ship transfer signal: {key_label} (+{value} pts).",
    "loiter_": "Loitering behavior: {key_label} (+{value} pts).",
    "fleet_": "Fleet-level pattern: {key_label} (+{value} pts).",
    "owner_cluster_": "Ownership cluster signal: {key_label} (+{value} pts).",
    "convoy_": "Convoy movement detected: {key_label} (+{value} pts).",
    "ownership_": "Ownership concern: {key_label} (+{value} pts).",
    "draught_": "Draught anomaly: {key_label} (+{value} pts).",
    "russian_port_": "Russian port linkage: {key_label} (+{value} pts).",
    "voyage_cycle_": "Voyage cycle pattern: {key_label} (+{value} pts).",
    "imo_": "IMO identity concern: {key_label} (+{value} pts).",
    "callsign_": "Callsign anomaly: {key_label} (+{value} pts).",
    "rename_": "Vessel renaming detected: {key_label} (+{value} pts).",
    "pi_": "P&I insurance concern: {key_label} (+{value} pts).",
    "psc_": "Port state control: {key_label} (+{value} pts).",
    "vessel_age_": "Vessel age risk: {key_label} (+{value} pts).",
    "flag_state_": "Flag state risk: {key_label} (+{value} pts).",
    "track_": "Track analysis: {key_label} (+{value} pts).",
    "fake_": "Fabricated data indicator: {key_label} (+{value} pts).",
    "dark_": "Dark activity signal: {key_label} (+{value} pts).",
    "cross_receiver_": "Cross-receiver inconsistency: {key_label} (+{value} pts).",
    "identity_swap_": "Identity swap detected: {key_label} (+{value} pts).",
    "shared_": "Shared entity concern: {key_label} (+{value} pts).",
    "laden_": "Laden status concern: {key_label} (+{value} pts).",
    "stale_ais_": "Stale AIS data: {key_label} (+{value} pts).",
    "stateless_mmsi": "Stateless MMSI detected: {key_label} (+{value} pts).",
    "transmission_": "Transmission anomaly: {key_label} (+{value} pts).",
    "feed_outage_": "Feed outage concern: {key_label} (+{value} pts).",
    "class_switching_": "AIS class switching: {key_label} (+{value} pts).",
    "invalid_metadata_": "Invalid metadata: {key_label} (+{value} pts).",
    "fraudulent_registry_": "Fraudulent registry: {key_label} (+{value} pts).",
    "vessel_laid_up_": "Laid-up vessel concern: {key_label} (+{value} pts).",
    "selective_dark_zone_": "Selective dark zone usage: {key_label} (+{value} pts).",
    "movement_envelope_": "Movement envelope anomaly: {key_label} (+{value} pts).",
    "gap_reactivation_": "Gap reactivation pattern: {key_label} (+{value} pts).",
    "repeat_sts_": "Repeat STS pattern: {key_label} (+{value} pts).",
    "dark_dark_sts": "Dark-dark STS transfer: {key_label} (+{value} pts).",
    "owner_or_manager_on_sanctions": "Owner/manager on sanctions: {key_label} (+{value} pts).",
    "scrapped_imo": "Scrapped IMO reuse detected: {key_label} (+{value} pts).",
    "viirs_": "VIIRS nighttime correlation: {key_label} (+{value} pts).",
    "gap_sar_": "Gap-SAR cross-correlation: {key_label} (+{value} pts).",
    "mmsi_zombie": "MMSI zombie reuse: {key_label} (+{value} pts).",
}


def _key_to_label(key: str) -> str:
    """Convert underscore_key to 'Underscore key' display label."""
    return key.replace("_", " ").capitalize()


def _render_signal(key: str, value: int | float) -> str:
    """Render a single signal key into a prose sentence."""
    # Tier 1: exact match
    if key in TIER1_TEMPLATES:
        return TIER1_TEMPLATES[key].format(value=value)

    # Tier 2: prefix match
    key_label = _key_to_label(key)
    for prefix, template in TIER2_PREFIXES.items():
        if key.startswith(prefix):
            return template.format(key_label=key_label, value=value)

    # Tier 3: auto-generated fallback
    return f"{key_label} (+{value} pts)."


# ── Linked anomaly queries ───────────────────────────────────────────────────


def _query_linked_anomalies(
    gap: AISGapEvent, db: Session
) -> dict[str, list[Any]]:
    """Query spoofing, loitering, and STS events linked to the gap window."""
    from sqlalchemy import or_

    from app.models.loitering_event import LoiteringEvent
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.sts_transfer import StsTransferEvent

    result: dict[str, list[Any]] = {"spoofing": [], "loitering": [], "sts": []}

    if not gap.vessel_id:
        return result

    result["spoofing"] = (
        db.query(SpoofingAnomaly)
        .filter(
            SpoofingAnomaly.vessel_id == gap.vessel_id,
            SpoofingAnomaly.start_time_utc >= gap.gap_start_utc - timedelta(days=1),
            SpoofingAnomaly.start_time_utc <= gap.gap_end_utc + timedelta(days=1),
        )
        .all()
    )

    result["loitering"] = (
        db.query(LoiteringEvent)
        .filter(
            LoiteringEvent.vessel_id == gap.vessel_id,
            LoiteringEvent.start_time_utc >= gap.gap_start_utc - timedelta(days=7),
            LoiteringEvent.start_time_utc <= gap.gap_end_utc + timedelta(days=7),
        )
        .all()
    )

    result["sts"] = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id == gap.vessel_id,
                StsTransferEvent.vessel_2_id == gap.vessel_id,
            ),
            StsTransferEvent.start_time_utc >= gap.gap_start_utc - timedelta(days=7),
            StsTransferEvent.start_time_utc <= gap.gap_end_utc + timedelta(days=7),
        )
        .all()
    )

    return result


# ── Core narrative data assembly ─────────────────────────────────────────────


def _group_signals_by_category(
    breakdown: dict[str, int | float],
) -> dict[str, list[tuple[str, int | float]]]:
    """Group risk breakdown signals by evidence category, filtering negatives."""
    groups: dict[str, list[tuple[str, int | float]]] = {}
    for key, value in breakdown.items():
        if not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        cat = _categorize_key(key)
        groups.setdefault(cat, []).append((key, value))
    # Sort each group by value descending
    for cat in groups:
        groups[cat].sort(key=lambda x: x[1], reverse=True)
    return groups


def _compute_narrative_strength(
    signal_count: int,
    category_count: int,
    enrichment_completeness: float,
) -> float:
    """Compute narrative strength: min(1.0, (signal_count/10)*0.5 + (category_count/5)*0.3 + enrichment_completeness*0.2)."""
    return min(
        1.0,
        (signal_count / 10) * 0.5
        + (category_count / 5) * 0.3
        + enrichment_completeness * 0.2,
    )


def _compute_enrichment_completeness(vessel: Vessel | None) -> float:
    """Compute how complete the vessel enrichment data is (0.0–1.0)."""
    if vessel is None:
        return 0.0
    checks = [
        vessel.imo is not None,
        vessel.name is not None,
        vessel.flag is not None,
        vessel.vessel_type is not None,
        vessel.year_built is not None,
        vessel.owner_name is not None,
        vessel.pi_coverage_status is not None
        and str(vessel.pi_coverage_status) != "UNKNOWN",
        vessel.flag_risk_category is not None
        and str(vessel.flag_risk_category) != "UNKNOWN",
    ]
    return sum(checks) / len(checks)


def _completeness_warnings(vessel: Vessel | None) -> list[str]:
    """Generate warnings about missing enrichment data."""
    warnings = []
    if vessel is None:
        warnings.append("No vessel record found — narrative may be incomplete.")
        return warnings
    if vessel.imo is None:
        warnings.append("IMO number is missing — ownership verification is limited.")
    if vessel.owner_name is None:
        warnings.append("Owner information is unavailable — beneficial ownership unknown.")
    if vessel.year_built is None:
        warnings.append("Year built is unknown — vessel age risk cannot be assessed.")
    if vessel.pi_coverage_status is None or str(vessel.pi_coverage_status) == "UNKNOWN":
        warnings.append("P&I insurance status is unknown.")
    return warnings


# ── Confidence explanation ───────────────────────────────────────────────────

_CONFIDENCE_EXPLANATIONS = {
    "CONFIRMED": "This vessel is CONFIRMED as a dark fleet participant based on sanctions list matches or analyst verification.",
    "HIGH": "HIGH confidence classification is based on a risk score at or above 76 with evidence spanning multiple categories.",
    "MEDIUM": "MEDIUM confidence indicates a risk score between 51-75 with at least one strongly signaling category.",
    "LOW": "LOW confidence reflects a risk score between 21-50 — further investigation is recommended before drawing conclusions.",
    "NONE": "Current evidence is insufficient for confident classification. The risk score is below 21.",
}


# ── Recommended actions ──────────────────────────────────────────────────────


def _recommended_actions(
    confidence: str | None, active_categories: set[str]
) -> list[str]:
    """Generate recommended next steps based on confidence and active categories."""
    actions = []

    if confidence in ("CONFIRMED", "HIGH"):
        actions.append("Escalate to senior analyst for priority review.")
        actions.append("Consider satellite imagery tasking to verify position during gap.")
    elif confidence == "MEDIUM":
        actions.append("Assign to analyst for further investigation.")
    else:
        actions.append("Monitor for additional signals before escalating.")

    if "WATCHLIST" in active_categories:
        actions.append("Cross-reference with latest sanctions designations and beneficial ownership records.")
    if "SPOOFING" in active_categories:
        actions.append("Review raw AIS data for evidence of track manipulation or identity fraud.")
    if "STS_TRANSFER" in active_categories:
        actions.append("Investigate STS partner vessel(s) and check for cargo documentation discrepancies.")
    if "IDENTITY_CHANGE" in active_categories:
        actions.append("Verify flag state and name change history through classification society records.")
    if "LOITERING" in active_categories:
        actions.append("Check loitering location against known STS hotspots and anchorage areas.")
    if "FLEET_PATTERN" in active_categories:
        actions.append("Investigate fleet-level ownership structures and shared management entities.")

    return actions


# ── Section builders ─────────────────────────────────────────────────────────


def _build_executive_summary(
    vessel: Vessel | None,
    gap: AISGapEvent,
    confidence: str | None,
    top_signals: list[tuple[str, int | float]],
) -> str:
    """Build executive summary: vessel name, MMSI, confidence, top 3 signals."""
    name = vessel.name if vessel and vessel.name else "Unknown vessel"
    mmsi = vessel.mmsi if vessel else "Unknown MMSI"
    conf = confidence or "UNCLASSIFIED"

    top_3 = top_signals[:3]
    signal_desc = ", ".join(_key_to_label(k) for k, _ in top_3)

    duration_h = gap.duration_minutes / 60
    return (
        f"{name} (MMSI {mmsi}) has been classified at {conf} confidence "
        f"following an AIS gap of {duration_h:.1f} hours. "
        f"Top signals: {signal_desc}."
    )


def _build_timeline(
    gap: AISGapEvent,
    linked: dict[str, list[Any]],
) -> list[tuple[datetime, str]]:
    """Build chronological timeline of events around the gap."""
    events: list[tuple[datetime, str]] = []
    events.append((gap.gap_start_utc, "AIS signal lost (gap start)."))
    events.append((gap.gap_end_utc, "AIS signal resumed (gap end)."))

    for s in linked.get("spoofing", []):
        ts = getattr(s, "start_time_utc", None)
        if ts:
            anomaly_type = str(
                s.anomaly_type.value
                if hasattr(s.anomaly_type, "value")
                else s.anomaly_type
            )
            events.append((ts, f"Spoofing anomaly detected: {anomaly_type.replace('_', ' ')}."))

    for le in linked.get("loitering", []):
        ts = getattr(le, "start_time_utc", None)
        if ts:
            dur = getattr(le, "duration_hours", None)
            desc = "Loitering event detected"
            if dur:
                desc += f" ({dur:.1f}h)"
            events.append((ts, f"{desc}."))

    for sts in linked.get("sts", []):
        ts = getattr(sts, "start_time_utc", None)
        if ts:
            events.append((ts, "Ship-to-ship transfer event detected."))

    events.sort(key=lambda x: x[0])
    return events


def _build_evidence_pillars(
    grouped: dict[str, list[tuple[str, int | float]]],
) -> list[tuple[str, str]]:
    """Build one paragraph per active category, in standard order."""
    pillars = []
    for cat in CATEGORY_ORDER:
        if cat not in grouped:
            continue
        signals = grouped[cat]
        display_name = CATEGORY_DISPLAY.get(cat, cat)
        lines = [_render_signal(k, v) for k, v in signals]
        pillars.append((display_name, " ".join(lines)))
    return pillars


def _build_vessel_background(vessel: Vessel | None) -> str:
    """Build vessel background paragraph."""
    if vessel is None:
        return "No vessel record available."

    parts = []
    if vessel.flag:
        risk = str(vessel.flag_risk_category) if vessel.flag_risk_category else "unknown"
        parts.append(f"Flagged under {vessel.flag} (risk: {risk}).")
    if vessel.year_built:
        age = datetime.now(UTC).year - vessel.year_built
        parts.append(f"Built in {vessel.year_built} ({age} years old).")
    if vessel.owner_name:
        parts.append(f"Registered owner: {vessel.owner_name}.")
    if vessel.psc_detained_last_12m:
        parts.append(f"PSC detention recorded in the last 12 months ({vessel.psc_major_deficiencies_last_12m} major deficiencies).")
    if vessel.pi_coverage_status:
        pi = str(vessel.pi_coverage_status)
        parts.append(f"P&I insurance status: {pi}.")

    return " ".join(parts) if parts else "Limited vessel background data available."


# ── Main narrative generation ────────────────────────────────────────────────


def generate_narrative(
    alert_id: int,
    db: Session,
    output_format: str = "md",
) -> dict[str, Any]:
    """Generate an investigation narrative for the given alert.

    Args:
        alert_id: The gap_event_id to generate a narrative for.
        db: SQLAlchemy session.
        output_format: One of 'text', 'md', 'html'.

    Returns:
        Dict with keys: narrative, format, strength, warnings, generated_at.
        On error: dict with 'error' key.
    """
    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        return {"error": "Alert not found"}

    vessel = (
        db.query(Vessel).filter(Vessel.vessel_id == gap.vessel_id).first()
        if gap.vessel_id
        else None
    )

    breakdown = gap.risk_breakdown_json or {}
    grouped = _group_signals_by_category(breakdown)
    active_categories = set(grouped.keys())

    # All signals sorted by value desc for top-N
    all_signals = []
    for signals in grouped.values():
        all_signals.extend(signals)
    all_signals.sort(key=lambda x: x[1], reverse=True)

    # Confidence
    confidence = vessel.dark_fleet_confidence if vessel else None

    # Linked anomalies
    linked = _query_linked_anomalies(gap, db)

    # Narrative sections
    executive_summary = _build_executive_summary(vessel, gap, confidence, all_signals)
    timeline = _build_timeline(gap, linked)
    evidence_pillars = _build_evidence_pillars(grouped)
    vessel_background = _build_vessel_background(vessel)
    confidence_text = _CONFIDENCE_EXPLANATIONS.get(
        confidence or "", "Confidence level has not been computed for this vessel."
    )
    actions = _recommended_actions(confidence, active_categories)
    warnings = _completeness_warnings(vessel)

    # Enrichment completeness and strength
    enrichment = _compute_enrichment_completeness(vessel)
    strength = _compute_narrative_strength(
        signal_count=len(all_signals),
        category_count=len(active_categories),
        enrichment_completeness=enrichment,
    )

    # Assemble sections
    sections = {
        "executive_summary": executive_summary,
        "timeline": timeline,
        "evidence_pillars": evidence_pillars,
        "vessel_background": vessel_background,
        "confidence_assessment": confidence_text,
        "recommended_actions": actions,
        "caveats": DISCLAIMER,
    }

    # Render
    if output_format == "md":
        narrative = _render_markdown_narrative(sections, warnings)
    elif output_format == "html":
        narrative = _render_html_narrative(sections, warnings)
    elif output_format == "text":
        narrative = _render_text_narrative(sections, warnings)
    else:
        return {"error": f"Unsupported format: {output_format}. Use 'text', 'md', or 'html'."}

    return {
        "narrative": narrative,
        "format": output_format,
        "strength": round(strength, 3),
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ── Renderers ────────────────────────────────────────────────────────────────


def _render_markdown_narrative(
    sections: dict[str, Any], warnings: list[str]
) -> str:
    """Render narrative as Markdown."""
    lines = ["# Investigation Narrative", ""]

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(sections["executive_summary"])
    lines.append("")

    # Timeline
    lines.append("## Timeline")
    lines.append("")
    for ts, desc in sections["timeline"]:
        lines.append(f"- **{ts.strftime('%Y-%m-%d %H:%M UTC')}**: {desc}")
    lines.append("")

    # Evidence Pillars
    lines.append("## Evidence Pillars")
    lines.append("")
    for cat_name, paragraph in sections["evidence_pillars"]:
        lines.append(f"### {cat_name}")
        lines.append("")
        lines.append(paragraph)
        lines.append("")

    # Vessel Background
    lines.append("## Vessel Background")
    lines.append("")
    lines.append(sections["vessel_background"])
    lines.append("")

    # Confidence Assessment
    lines.append("## Confidence Assessment")
    lines.append("")
    lines.append(sections["confidence_assessment"])
    lines.append("")

    # Recommended Actions
    lines.append("## Recommended Actions")
    lines.append("")
    for action in sections["recommended_actions"]:
        lines.append(f"- {action}")
    lines.append("")

    # Warnings
    if warnings:
        lines.append("## Completeness Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"> {w}")
        lines.append("")

    # Caveats
    lines.append("## Caveats")
    lines.append("")
    lines.append(f"> {sections['caveats']}")
    lines.append("")

    return "\n".join(lines)


def _render_text_narrative(
    sections: dict[str, Any], warnings: list[str]
) -> str:
    """Render narrative as plain text (stripped markdown)."""
    md = _render_markdown_narrative(sections, warnings)
    # Strip markdown: remove #, **, >, -
    text = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    text = text.replace("**", "")
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^- ", "  * ", text, flags=re.MULTILINE)
    return text


def _render_html_narrative(
    sections: dict[str, Any], warnings: list[str]
) -> str:
    """Render narrative as simple HTML."""
    parts = ["<h2>Investigation Narrative</h2>"]

    # Executive Summary
    parts.append("<h3>Executive Summary</h3>")
    parts.append(f"<p>{html.escape(sections['executive_summary'])}</p>")

    # Timeline
    parts.append("<h3>Timeline</h3>")
    parts.append("<ul>")
    for ts, desc in sections["timeline"]:
        parts.append(
            f"<li><strong>{html.escape(ts.strftime('%Y-%m-%d %H:%M UTC'))}</strong>: "
            f"{html.escape(desc)}</li>"
        )
    parts.append("</ul>")

    # Evidence Pillars
    parts.append("<h3>Evidence Pillars</h3>")
    for cat_name, paragraph in sections["evidence_pillars"]:
        parts.append(f"<h4>{html.escape(cat_name)}</h4>")
        parts.append(f"<p>{html.escape(paragraph)}</p>")

    # Vessel Background
    parts.append("<h3>Vessel Background</h3>")
    parts.append(f"<p>{html.escape(sections['vessel_background'])}</p>")

    # Confidence Assessment
    parts.append("<h3>Confidence Assessment</h3>")
    parts.append(f"<p>{html.escape(sections['confidence_assessment'])}</p>")

    # Recommended Actions
    parts.append("<h3>Recommended Actions</h3>")
    parts.append("<ul>")
    for action in sections["recommended_actions"]:
        parts.append(f"<li>{html.escape(action)}</li>")
    parts.append("</ul>")

    # Warnings
    if warnings:
        parts.append("<h3>Completeness Warnings</h3>")
        for w in warnings:
            parts.append(f"<p><strong>Warning:</strong> {html.escape(w)}</p>")

    # Caveats
    parts.append("<h3>Caveats</h3>")
    parts.append(f"<p><em>{html.escape(sections['caveats'])}</em></p>")

    return "\n".join(parts)
