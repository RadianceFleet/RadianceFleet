"""Explainability engine for AIS gap alert risk scores.

Parses ``risk_breakdown_json`` from :class:`AISGapEvent` and produces
human-readable explanations using a 3-tier template system:

- **Tier 1**: Hand-written templates for ~40 well-known signal keys.
- **Tier 2**: Pattern-generated explanations for prefix/suffix matches.
- **Tier 3**: Generic fallback for any unrecognised key.

Also computes a waterfall (cumulative contribution chart) and groups
signals into six analyst-facing categories.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.schemas.explainability import (
    ExplainabilityResponse,
    SignalExplanation,
    WaterfallEntry,
)

logger = logging.getLogger(__name__)

# ── Category mapping (duplicated from confidence_classifier — private API) ──

_AIS_GAP_PREFIXES = (
    "gap_duration",
    "gap_frequency",
    "impossible_reappear",
    "near_impossible_reappear",
    "dark_zone",
    "selective_dark_zone",
    "movement_envelope",
    "speed_impossible",
    "speed_spike",
    "speed_spoof",
    "feed_outage",
    "gap_reactivation",
    "at_sea_no_port_call",
    "transmission_frequency",
)
_SPOOFING_PREFIXES = (
    "spoofing_",
    "track_naturalness",
    "stale_ais",
    "stateless_mmsi",
    "imo_fraud",
    "imo_fabricated",
    "cross_receiver",
    "identity_swap",
    "fake_",
    "scrapped_imo",
    "track_replay",
)
_STS_PREFIXES = (
    "sts_event",
    "sts_",
    "gap_in_sts_tagged_corridor",
    "repeat_sts",
    "dark_dark_sts",
    "draught_",
    "russian_port",
    "voyage_cycle",
    "laden_from_russian",
)
_IDENTITY_PREFIXES = (
    "flag_change",
    "flag_AND_name",
    "callsign_change",
    "class_switching",
    "flag_hopping",
    "rename_velocity",
    "invalid_metadata",
    "ais_class_mismatch",
    "fraudulent_registry",
    "pi_known_fraudulent",
    "pi_unknown_insurer",
    "pi_no_insurer",
)
_LOITERING_PREFIXES = (
    "loiter_",
    "vessel_laid_up",
)
_FLEET_PREFIXES = (
    "fleet_",
    "owner_cluster",
    "shared_manager",
    "shared_pi",
    "convoy_",
    "ownership_",
)
_WATCHLIST_PREFIXES = (
    "watchlist_",
    "owner_or_manager_on_sanctions",
)

# Map internal category names to analyst-facing category names
_INTERNAL_TO_ANALYST: dict[str, str] = {
    "AIS_GAP": "behavioral",
    "SPOOFING": "identity",
    "STS_TRANSFER": "spatial",
    "IDENTITY_CHANGE": "identity",
    "LOITERING": "behavioral",
    "FLEET_PATTERN": "environmental",
    "WATCHLIST": "sanctions",
}


def _categorize_key(key: str) -> str:
    """Map a risk breakdown key to its internal evidence category."""
    for prefix in _WATCHLIST_PREFIXES:
        if key.startswith(prefix):
            return "WATCHLIST"
    for prefix in _SPOOFING_PREFIXES:
        if key.startswith(prefix):
            return "SPOOFING"
    for prefix in _STS_PREFIXES:
        if key.startswith(prefix):
            return "STS_TRANSFER"
    for prefix in _IDENTITY_PREFIXES:
        if key.startswith(prefix):
            return "IDENTITY_CHANGE"
    for prefix in _LOITERING_PREFIXES:
        if key.startswith(prefix):
            return "LOITERING"
    for prefix in _FLEET_PREFIXES:
        if key.startswith(prefix):
            return "FLEET_PATTERN"
    for prefix in _AIS_GAP_PREFIXES:
        if key.startswith(prefix):
            return "AIS_GAP"
    return "AIS_GAP"


def _analyst_category(key: str) -> str:
    """Map a risk breakdown key to an analyst-facing category."""
    internal = _categorize_key(key)
    # Temporal signals: gap duration/frequency, reactivation
    if key.startswith(("gap_duration", "gap_frequency", "gap_reactivation")):
        return "temporal"
    # Spatial signals: corridor, zone, port, STS
    if internal == "STS_TRANSFER":
        return "spatial"
    if key.startswith(("dark_zone", "selective_dark_zone", "russian_port", "gap_in_sts")):
        return "spatial"
    return _INTERNAL_TO_ANALYST.get(internal, "behavioral")


# ── Tier 1: Hand-written templates for known signal keys ─────────────────────

TIER1_TEMPLATES: dict[str, str] = {
    # AIS gaps
    "gap_duration_12h": "Vessel went dark for over 12 hours, a significant gap warranting investigation ({value} pts).",
    "gap_duration_24h": "AIS transmission was absent for over 24 hours, strongly suggesting deliberate shutdown ({value} pts).",
    "gap_duration_48h": "AIS gap exceeding 48 hours — an extreme duration seen almost exclusively in sanctions evasion ({value} pts).",
    "gap_duration_6h": "A 6-hour AIS gap was detected ({value} pts).",
    "gap_duration_3h": "A 3-hour AIS gap was recorded ({value} pts).",
    "gap_frequency_high": "Unusually high frequency of AIS gaps indicates a pattern of deliberate signal suppression ({value} pts).",
    "gap_frequency_moderate": "Multiple AIS gaps suggest a recurring concealment pattern ({value} pts).",
    "gap_frequency_repeat_corridor": "Repeated AIS gaps in the same corridor indicate deliberate route-specific concealment ({value} pts).",
    # Speed
    "speed_impossible": "Reappearance position requires physically impossible speeds, indicating data manipulation ({value} pts).",
    "speed_spike_before_gap": "Unusual speed spike just before the AIS gap began ({value} pts).",
    "speed_spoof_constant": "Suspiciously constant speed through changing conditions, consistent with spoofed AIS ({value} pts).",
    # Spoofing
    "spoofing_circle": "Circular AIS track detected — a known GPS spoofing signature ({value} pts).",
    "spoofing_identity_swap": "Evidence of AIS identity swapping between vessels ({value} pts).",
    "spoofing_position_jump": "Impossible position jumps suggest deliberate track manipulation ({value} pts).",
    "spoofing_track_replay": "AIS track appears to replay a previously recorded route ({value} pts).",
    "spoofing_stale_data": "Stale or recycled AIS position reports detected ({value} pts).",
    "spoofing_deliberate_circle": "Deliberate circular spoofing to mask true position ({value} pts).",
    "spoofing_equipment_circle": "Circular AIS pattern from possible equipment malfunction ({value} pts).",
    "spoofing_stationary_circle": "Stationary circular pattern from GPS interference or equipment fault ({value} pts).",
    # Watchlist
    "watchlist_ofac_sdn": "Vessel appears on the OFAC SDN sanctions list ({value} pts).",
    "watchlist_eu_sanctions": "Vessel listed under EU sanctions ({value} pts).",
    "watchlist_kse_shadow_fleet": "Vessel identified in the KSE shadow fleet database ({value} pts).",
    "watchlist_un_sanctions": "Vessel appears on UN sanctions list ({value} pts).",
    "watchlist_match": "Vessel matched against one or more sanctions/watchlists ({value} pts).",
    # Flag changes
    "flag_change_to_high_risk": "Recent change to a high-risk flag state — common sanctions evasion tactic ({value} pts).",
    "flag_change_rapid": "Rapid flag changes detected, suggesting flag-hopping to evade detection ({value} pts).",
    "flag_AND_name_change": "Simultaneous flag and name change — a strong identity-laundering indicator ({value} pts).",
    "flag_hopping_3plus": "Three or more flag changes in a short period ({value} pts).",
    # STS
    "sts_event_detected": "Ship-to-ship transfer event detected near the gap period ({value} pts).",
    "sts_event_in_corridor": "STS transfer in a known smuggling corridor ({value} pts).",
    "sts_dark_dark": "Both vessels had AIS off during STS, indicating a covert operation ({value} pts).",
    # Loitering
    "loiter_pre_gap": "Vessel was loitering before the gap, consistent with a covert rendezvous ({value} pts).",
    "loiter_in_sts_zone": "Loitering detected within an STS transfer zone ({value} pts).",
    "loiter_repeated": "Repeated loitering at the same location ({value} pts).",
    # Movement
    "impossible_reappear_speed": "Reappearance requires physically impossible transit speed ({value} pts).",
    "near_impossible_reappear": "Reappearance at the extreme edge of what is physically possible ({value} pts).",
    "dark_zone_gap": "AIS gap occurred in a known GPS jamming/dark zone ({value} pts).",
    "at_sea_no_port_call": "Extended time at sea with no port calls recorded ({value} pts).",
}

# ── Tier 2: Pattern-based prefix templates ───────────────────────────────────

TIER2_PATTERNS: list[tuple[str, str]] = [
    # Multiplier patterns
    ("_multiplier", "Applied {key_label} multiplier of {value}x."),
    ("_factor", "Applied {key_label} factor of {value}."),
    ("_bonus", "{key_label} bonus applied ({value} pts)."),
    ("_penalty", "{key_label} penalty applied ({value} pts)."),
    # Prefix patterns
    ("gap_duration_", "AIS gap duration signal: {key_label} ({value} pts)."),
    ("gap_frequency_", "AIS gap frequency signal: {key_label} ({value} pts)."),
    ("speed_", "Speed anomaly: {key_label} ({value} pts)."),
    ("spoofing_", "AIS spoofing indicator: {key_label} ({value} pts)."),
    ("watchlist_", "Watchlist match: {key_label} ({value} pts)."),
    ("flag_change_", "Flag state change: {key_label} ({value} pts)."),
    ("flag_hopping_", "Flag-hopping pattern: {key_label} ({value} pts)."),
    ("sts_", "Ship-to-ship transfer signal: {key_label} ({value} pts)."),
    ("loiter_", "Loitering behaviour: {key_label} ({value} pts)."),
    ("fleet_", "Fleet-level pattern: {key_label} ({value} pts)."),
    ("owner_cluster_", "Ownership cluster signal: {key_label} ({value} pts)."),
    ("convoy_", "Convoy movement: {key_label} ({value} pts)."),
    ("ownership_", "Ownership concern: {key_label} ({value} pts)."),
    ("draught_", "Draught anomaly: {key_label} ({value} pts)."),
    ("russian_port_", "Russian port linkage: {key_label} ({value} pts)."),
    ("voyage_cycle_", "Voyage cycle pattern: {key_label} ({value} pts)."),
    ("imo_", "IMO identity concern: {key_label} ({value} pts)."),
    ("callsign_", "Callsign anomaly: {key_label} ({value} pts)."),
    ("rename_", "Vessel renaming: {key_label} ({value} pts)."),
    ("pi_", "P&I insurance concern: {key_label} ({value} pts)."),
    ("psc_", "Port state control: {key_label} ({value} pts)."),
    ("vessel_age_", "Vessel age risk: {key_label} ({value} pts)."),
    ("flag_state_", "Flag state risk: {key_label} ({value} pts)."),
    ("track_", "Track analysis: {key_label} ({value} pts)."),
    ("fake_", "Fabricated data indicator: {key_label} ({value} pts)."),
    ("dark_", "Dark activity signal: {key_label} ({value} pts)."),
    ("cross_receiver_", "Cross-receiver inconsistency: {key_label} ({value} pts)."),
    ("identity_swap_", "Identity swap: {key_label} ({value} pts)."),
    ("shared_", "Shared entity concern: {key_label} ({value} pts)."),
    ("laden_", "Laden status concern: {key_label} ({value} pts)."),
    ("viirs_", "VIIRS nighttime correlation: {key_label} ({value} pts)."),
    ("gap_sar_", "Gap-SAR cross-correlation: {key_label} ({value} pts)."),
    ("mmsi_zombie", "MMSI zombie reuse: {key_label} ({value} pts)."),
]


def _key_to_label(key: str) -> str:
    """Convert underscore_key to 'Underscore key' display label."""
    return key.replace("_", " ").capitalize()


def _is_multiplier_key(key: str) -> bool:
    """Return True if the key represents a multiplier effect."""
    return key.endswith(("_multiplier", "_factor", "_coefficient"))


def _explain_signal(key: str, value: float) -> tuple[str, int]:
    """Return (explanation_text, tier) for a single signal key/value.

    Tier 1 = exact match, Tier 2 = pattern, Tier 3 = fallback.
    """
    # Tier 1: exact match
    if key in TIER1_TEMPLATES:
        return TIER1_TEMPLATES[key].format(value=value), 1

    key_label = _key_to_label(key)

    # Tier 2: pattern match (suffix first, then prefix)
    for pattern, template in TIER2_PATTERNS:
        if pattern.startswith("_"):
            # suffix match
            if key.endswith(pattern):
                return template.format(key_label=key_label, value=value), 2
        else:
            # prefix match
            if key.startswith(pattern):
                return template.format(key_label=key_label, value=value), 2

    # Tier 3: fallback
    return f"Signal '{key}' contributed {value} points.", 3


# ── Waterfall computation ────────────────────────────────────────────────────


def _compute_waterfall(
    signals: list[tuple[str, float]],
) -> list[WaterfallEntry]:
    """Build waterfall entries sorted by absolute contribution descending.

    Additive signals are accumulated first, then multipliers are appended.
    """
    additive = [(k, v) for k, v in signals if not _is_multiplier_key(k)]
    multipliers = [(k, v) for k, v in signals if _is_multiplier_key(k)]

    # Sort additive by absolute value descending
    additive.sort(key=lambda x: abs(x[1]), reverse=True)

    entries: list[WaterfallEntry] = []
    cumulative = 0.0

    for key, value in additive:
        cumulative += value
        entries.append(
            WaterfallEntry(
                label=_key_to_label(key),
                value=round(value, 2),
                cumulative=round(cumulative, 2),
                is_multiplier=False,
            )
        )

    # Multiplier effects: show the multiplicative impact
    for key, value in multipliers:
        # Multipliers are stored as raw multiplier values (e.g. 1.5)
        # The "effect" is how much the cumulative total changes
        if cumulative != 0 and value != 0:
            effect = cumulative * (value - 1)
            cumulative += effect
        else:
            effect = value
            cumulative += effect
        entries.append(
            WaterfallEntry(
                label=_key_to_label(key),
                value=round(effect, 2),
                cumulative=round(cumulative, 2),
                is_multiplier=True,
            )
        )

    return entries


# ── Summary generation ───────────────────────────────────────────────────────


def _generate_summary(
    vessel: Vessel | None,
    alert: AISGapEvent,
    signals: list[SignalExplanation],
    total_score: float,
) -> str:
    """Generate a one-paragraph executive summary."""
    name = vessel.name if vessel and vessel.name else "Unknown vessel"
    mmsi = vessel.mmsi if vessel else "N/A"
    duration_h = alert.duration_minutes / 60 if alert.duration_minutes else 0

    if not signals:
        return (
            f"{name} (MMSI {mmsi}) triggered an alert with a {duration_h:.1f}-hour "
            f"AIS gap but no contributing risk signals were found."
        )

    # Top 3 signals by value
    top = sorted(signals, key=lambda s: abs(s.value), reverse=True)[:3]
    signal_names = ", ".join(s.key.replace("_", " ") for s in top)

    # Count categories
    cats = {s.category for s in signals}
    cat_count = len(cats)

    return (
        f"{name} (MMSI {mmsi}) scored {total_score:.0f} points across "
        f"{len(signals)} signals in {cat_count} categories during a "
        f"{duration_h:.1f}-hour AIS gap. Top contributing factors: {signal_names}."
    )


# ── Main entry point ─────────────────────────────────────────────────────────


def explain_alert(alert: AISGapEvent, db: Session) -> ExplainabilityResponse:
    """Generate a full explainability response for the given alert.

    Args:
        alert: The AISGapEvent to explain.
        db: SQLAlchemy session (used to fetch related vessel).

    Returns:
        ExplainabilityResponse with signals, waterfall, categories, and summary.
    """
    breakdown: dict[str, Any] = alert.risk_breakdown_json or {}
    total_score = float(alert.risk_score or 0)

    # Fetch vessel for summary
    vessel: Vessel | None = None
    if alert.vessel_id:
        vessel = db.query(Vessel).filter(Vessel.vessel_id == alert.vessel_id).first()

    # Build signal explanations
    signals: list[SignalExplanation] = []
    raw_pairs: list[tuple[str, float]] = []

    for key, value in breakdown.items():
        if not isinstance(value, (int, float)):
            continue
        fval = float(value)
        explanation, tier = _explain_signal(key, fval)
        category = _analyst_category(key)
        signals.append(
            SignalExplanation(
                key=key,
                value=round(fval, 2),
                explanation=explanation,
                category=category,
                tier=tier,
            )
        )
        raw_pairs.append((key, fval))

    # Sort signals by absolute value descending
    signals.sort(key=lambda s: abs(s.value), reverse=True)

    # Waterfall
    waterfall = _compute_waterfall(raw_pairs)

    # Group by category
    categories: dict[str, list[SignalExplanation]] = {}
    for sig in signals:
        categories.setdefault(sig.category, []).append(sig)

    # Summary
    summary = _generate_summary(vessel, alert, signals, total_score)

    return ExplainabilityResponse(
        alert_id=alert.gap_event_id,
        total_score=round(total_score, 2),
        signals=signals,
        waterfall=waterfall,
        categories=categories,
        summary=summary,
    )
