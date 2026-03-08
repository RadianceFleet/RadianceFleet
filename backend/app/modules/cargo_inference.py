"""Cargo inference — draught-based laden/ballast state detection.

Provides:
  - infer_cargo_state()  — determine laden vs ballast from AIS draught data
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# AIS ship_type code -> cargo type label (ITU-R M.1371-5, Table 50)
# First digit: vessel category, second digit: DG/HP/MP cargo category
AIS_SHIP_TYPE_CARGO: dict[int, str] = {
    70: "cargo_general",
    71: "cargo_dg_a",
    72: "cargo_dg_b",
    73: "cargo_dg_c",
    74: "cargo_dg_d",
    75: "cargo_reserved_5",
    76: "cargo_reserved_6",
    77: "cargo_reserved_7",
    78: "cargo_reserved_8",
    79: "cargo_no_additional",
    80: "tanker_general",
    81: "tanker_dg_a",
    82: "tanker_dg_b",
    83: "tanker_dg_c",
    84: "tanker_dg_d",
    85: "tanker_reserved_5",
    86: "tanker_reserved_6",
    87: "tanker_reserved_7",
    88: "tanker_reserved_8",
    89: "tanker_no_additional",
}

# Categories for mismatch detection
_TANKER_CARGO_TYPES = {v for k, v in AIS_SHIP_TYPE_CARGO.items() if k >= 80}
_CARGO_CARGO_TYPES = {v for k, v in AIS_SHIP_TYPE_CARGO.items() if 70 <= k < 80}


def parse_ais_cargo_type(ship_type_code: int | None) -> str | None:
    """Parse AIS ship_type code into a cargo type label.

    Only codes 70-89 (cargo and tanker) are relevant for cargo inference.
    Returns None for unknown/irrelevant codes.
    """
    if ship_type_code is None:
        return None
    try:
        code = int(ship_type_code)
    except (TypeError, ValueError):
        return None
    return AIS_SHIP_TYPE_CARGO.get(code)


def score_cargo_type_mismatch(
    ais_cargo_type: str | None,
    cargo_state: str | None,
    port_types: list[str] | None = None,
) -> dict:
    """Score mismatch between declared AIS cargo type and observed behavior.

    Args:
        ais_cargo_type: Parsed cargo type from AIS (e.g. "tanker_general").
        cargo_state: Inferred state from draught ("laden" / "ballast" / None).
        port_types: List of recent port type strings (e.g. ["oil_terminal", "dry_bulk"]).

    Returns:
        Dict with score, reason, and mismatch details. Empty dict if no mismatch.
    """
    if not ais_cargo_type:
        return {}

    result: dict = {}
    is_tanker = ais_cargo_type in _TANKER_CARGO_TYPES
    is_cargo = ais_cargo_type in _CARGO_CARGO_TYPES

    # Tanker declared but never laden (always light draught) -> suspicious
    if is_tanker and cargo_state == "ballast":
        result = {
            "mismatch": "tanker_always_ballast",
            "ais_cargo_type": ais_cargo_type,
            "observed_state": cargo_state,
            "score": 10,
            "reason": "Tanker type declared but vessel consistently in ballast — possible cargo concealment",
        }

    # Cargo vessel visiting oil terminals -> type mismatch
    if is_cargo and port_types:
        oil_visits = [p for p in port_types if "oil" in p.lower() or "tanker" in p.lower()]
        if oil_visits:
            result = {
                "mismatch": "cargo_at_oil_terminal",
                "ais_cargo_type": ais_cargo_type,
                "oil_terminal_visits": len(oil_visits),
                "score": 10,
                "reason": "Cargo-type vessel visiting oil terminals — possible type misrepresentation",
            }

    return result


# Max expected draught by vessel type (meters).
# Laden threshold is 60% of max.
_MAX_DRAUGHT_BY_TYPE: dict[str, float] = {
    "vlcc": 22.0,
    "suezmax": 17.0,
    "aframax": 15.0,
    "panamax": 14.0,
    "crude_oil_tanker": 18.0,
    "oil_chemical_tanker": 14.0,
    "tanker": 16.0,
}

# Draught threshold fraction for laden determination
_LADEN_THRESHOLD = 0.60


def _get_max_draught(vessel_type: str | None, deadweight: float | None) -> float:
    """Determine max expected draught for vessel type.

    Falls back to DWT-based estimate if vessel type unknown.
    """
    if vessel_type:
        vtype_lower = vessel_type.lower()
        for key, max_d in _MAX_DRAUGHT_BY_TYPE.items():
            if key in vtype_lower:
                return max_d

    # DWT-based estimate: rough formula from IMO guidelines
    if deadweight is not None and isinstance(deadweight, (int, float)):
        if deadweight >= 200_000:
            return 22.0  # VLCC
        elif deadweight >= 120_000:
            return 17.0  # Suezmax
        elif deadweight >= 80_000:
            return 15.0  # Aframax
        elif deadweight >= 60_000:
            return 14.0  # Panamax
        elif deadweight >= 10_000:
            return 12.0  # General tanker
    return 15.0  # Default fallback


def infer_cargo_state(db: Session, vessel_id: int) -> dict:
    """Infer laden/ballast state from latest AIS draught data.

    1. Get latest draught from AIS points
    2. Get vessel DWT and vessel_type
    3. If draught > 60% of max expected -> "laden", else "ballast"
    4. Context: laden from Russian terminal + STS -> +15

    Returns dict with state, draught, max_draught, laden_ratio, risk_score.
    Returns empty dict if no draught data available.
    """
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel

    result: dict[str, Any] = {}

    # Get vessel info
    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if vessel is None:
        return result

    # Get latest draught from AIS points
    latest_point = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.draught.isnot(None),
            AISPoint.draught > 0,
        )
        .order_by(AISPoint.timestamp_utc.desc())
        .first()
    )

    if latest_point is None:
        return result

    draught = latest_point.draught
    if not isinstance(draught, (int, float)):
        return result

    # Determine max expected draught
    max_draught = _get_max_draught(vessel.vessel_type, vessel.deadweight)
    laden_ratio = draught / max_draught if max_draught > 0 else 0.0

    state = "laden" if laden_ratio > _LADEN_THRESHOLD else "ballast"

    result = {
        "vessel_id": vessel_id,
        "state": state,
        "draught_m": draught,
        "max_draught_m": max_draught,
        "laden_ratio": round(laden_ratio, 3),
        "risk_score": 0,
        "timestamp_utc": latest_point.timestamp_utc.isoformat()
        if latest_point.timestamp_utc
        else None,
    }

    # Context scoring: laden from Russian terminal + STS -> +15
    if state == "laden":
        try:
            from app.models.port import Port
            from app.models.port_call import PortCall

            # Check if recent port call was to a Russian terminal
            recent_call = (
                db.query(PortCall)
                .join(Port, PortCall.port_id == Port.port_id)
                .filter(
                    PortCall.vessel_id == vessel_id,
                    Port.is_russian_oil_terminal,
                )
                .order_by(PortCall.arrival_utc.desc())
                .first()
            )

            if recent_call is not None:
                # Check for STS events
                from sqlalchemy import or_

                from app.models.sts_transfer import StsTransferEvent

                sts_event = (
                    db.query(StsTransferEvent)
                    .filter(
                        or_(
                            StsTransferEvent.vessel_1_id == vessel_id,
                            StsTransferEvent.vessel_2_id == vessel_id,
                        )
                    )
                    .first()
                )

                if sts_event is not None:
                    result["risk_score"] = 15
                    result["russian_terminal_sts"] = True
        except Exception as exc:
            logger.warning("Cargo context scoring failed for vessel %d: %s", vessel_id, exc)

    return result
