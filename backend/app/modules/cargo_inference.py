"""Cargo inference — draught-based laden/ballast state detection.

Provides:
  - infer_cargo_state()  — determine laden vs ballast from AIS draught data
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

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
        "timestamp_utc": latest_point.timestamp_utc.isoformat() if latest_point.timestamp_utc else None,
    }

    # Context scoring: laden from Russian terminal + STS -> +15
    if state == "laden":
        try:
            from app.models.port_call import PortCall
            from app.models.port import Port

            # Check if recent port call was to a Russian terminal
            recent_call = (
                db.query(PortCall)
                .join(Port, PortCall.port_id == Port.port_id)
                .filter(
                    PortCall.vessel_id == vessel_id,
                    Port.is_russian_oil_terminal == True,
                )
                .order_by(PortCall.arrival_utc.desc())
                .first()
            )

            if recent_call is not None:
                # Check for STS events
                from app.models.sts_transfer import StsTransferEvent
                from sqlalchemy import or_

                sts_event = db.query(StsTransferEvent).filter(
                    or_(
                        StsTransferEvent.vessel_1_id == vessel_id,
                        StsTransferEvent.vessel_2_id == vessel_id,
                    )
                ).first()

                if sts_event is not None:
                    result["risk_score"] = 15
                    result["russian_terminal_sts"] = True
        except Exception as exc:
            logger.warning("Cargo context scoring failed for vessel %d: %s", vessel_id, exc)

    return result
