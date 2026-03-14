"""FP tuning API routes — per-corridor false-positive rate analysis and scoring overrides."""

from __future__ import annotations

import json as _json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_auth, require_senior_or_admin
from app.config import settings
from app.database import get_db
from app.models.calibration_event import CalibrationEvent
from app.models.corridor import Corridor
from app.models.corridor_scoring_override import CorridorScoringOverride
from app.modules.fp_rate_tracker import (
    compute_fp_rate,
    compute_fp_rates,
    generate_calibration_suggestions,
)
from app.schemas.fp_tuning import (
    CalibrationSuggestionSchema,
    CorridorFPRateSchema,
    ScoringOverrideCreate,
    ScoringOverrideResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/corridors", tags=["fp-tuning"])


def _check_enabled():
    if not getattr(settings, "FP_TUNING_ENABLED", False):
        raise HTTPException(status_code=404, detail="FP tuning feature is not enabled")


def _get_corridor_or_404(db: Session, corridor_id: int) -> Corridor:
    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if corridor is None:
        raise HTTPException(status_code=404, detail=f"Corridor {corridor_id} not found")
    return corridor


# ---------------------------------------------------------------------------
# Read endpoints (any authenticated user)
# ---------------------------------------------------------------------------


@router.get("/{corridor_id}/fp-rate", response_model=CorridorFPRateSchema)
def get_corridor_fp_rate(
    corridor_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get FP rate statistics for a single corridor."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)
    result = compute_fp_rate(db, corridor_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Corridor not found")
    return CorridorFPRateSchema(
        corridor_id=result.corridor_id,
        corridor_name=result.corridor_name,
        total_alerts=result.total_alerts,
        false_positives=result.false_positives,
        fp_rate=result.fp_rate,
        fp_rate_30d=result.fp_rate_30d,
        fp_rate_90d=result.fp_rate_90d,
        trend=result.trend,
    )


@router.get("/fp-rates", response_model=list[CorridorFPRateSchema])
def get_all_fp_rates(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get FP rates for all corridors with reviewed alerts."""
    _check_enabled()
    results = compute_fp_rates(db)
    return [
        CorridorFPRateSchema(
            corridor_id=r.corridor_id,
            corridor_name=r.corridor_name,
            total_alerts=r.total_alerts,
            false_positives=r.false_positives,
            fp_rate=r.fp_rate,
            fp_rate_30d=r.fp_rate_30d,
            fp_rate_90d=r.fp_rate_90d,
            trend=r.trend,
        )
        for r in results
    ]


@router.get("/{corridor_id}/scoring-override", response_model=ScoringOverrideResponse)
def get_scoring_override(
    corridor_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get the active scoring override for a corridor."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)
    override = (
        db.query(CorridorScoringOverride)
        .filter(
            CorridorScoringOverride.corridor_id == corridor_id,
            CorridorScoringOverride.is_active.is_(True),
        )
        .first()
    )
    if override is None:
        raise HTTPException(
            status_code=404, detail=f"No active override for corridor {corridor_id}"
        )
    corridor = _get_corridor_or_404(db, corridor_id)
    return ScoringOverrideResponse(
        override_id=override.override_id,
        corridor_id=override.corridor_id,
        corridor_name=corridor.name,
        corridor_multiplier_override=override.corridor_multiplier_override,
        gap_duration_multiplier=override.gap_duration_multiplier,
        description=override.description,
        created_by=override.created_by,
        created_at=override.created_at,
        updated_at=override.updated_at,
        is_active=override.is_active,
    )


@router.get("/calibration-suggestions", response_model=list[CalibrationSuggestionSchema])
def get_calibration_suggestions(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get auto-generated calibration suggestions based on FP rates."""
    _check_enabled()
    results = generate_calibration_suggestions(db)
    return [
        CalibrationSuggestionSchema(
            corridor_id=s.corridor_id,
            corridor_name=s.corridor_name,
            current_multiplier=s.current_multiplier,
            suggested_multiplier=s.suggested_multiplier,
            reason=s.reason,
            fp_rate=s.fp_rate,
        )
        for s in results
    ]


# ---------------------------------------------------------------------------
# Write endpoints (senior/admin only)
# ---------------------------------------------------------------------------


@router.post("/{corridor_id}/scoring-override", response_model=ScoringOverrideResponse)
def create_or_update_scoring_override(
    corridor_id: int,
    body: ScoringOverrideCreate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Create or update a scoring override for a corridor. Requires senior/admin role."""
    _check_enabled()
    corridor = _get_corridor_or_404(db, corridor_id)

    # Check for existing active override
    existing = (
        db.query(CorridorScoringOverride)
        .filter(
            CorridorScoringOverride.corridor_id == corridor_id,
            CorridorScoringOverride.is_active.is_(True),
        )
        .first()
    )

    if existing:
        existing.corridor_multiplier_override = body.corridor_multiplier_override
        existing.gap_duration_multiplier = body.gap_duration_multiplier
        existing.description = body.description
        db.flush()
        override = existing
    else:
        override = CorridorScoringOverride(
            corridor_id=corridor_id,
            corridor_multiplier_override=body.corridor_multiplier_override,
            gap_duration_multiplier=body.gap_duration_multiplier,
            description=body.description,
            created_by=auth.get("analyst_id"),
        )
        db.add(override)
        db.flush()

    db.commit()
    db.refresh(override)

    return ScoringOverrideResponse(
        override_id=override.override_id,
        corridor_id=override.corridor_id,
        corridor_name=corridor.name,
        corridor_multiplier_override=override.corridor_multiplier_override,
        gap_duration_multiplier=override.gap_duration_multiplier,
        description=override.description,
        created_by=override.created_by,
        created_at=override.created_at,
        updated_at=override.updated_at,
        is_active=override.is_active,
    )


@router.delete("/{corridor_id}/scoring-override")
def deactivate_scoring_override(
    corridor_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Deactivate a scoring override for a corridor. Requires senior/admin role."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)

    override = (
        db.query(CorridorScoringOverride)
        .filter(
            CorridorScoringOverride.corridor_id == corridor_id,
            CorridorScoringOverride.is_active.is_(True),
        )
        .first()
    )
    if override is None:
        raise HTTPException(
            status_code=404, detail=f"No active override for corridor {corridor_id}"
        )

    override.is_active = False
    db.commit()
    return {"detail": f"Override for corridor {corridor_id} deactivated"}


# ---------------------------------------------------------------------------
# Auto-calibration endpoints
# ---------------------------------------------------------------------------


@router.get("/calibration-suggestions/per-signal")
def get_per_signal_suggestions(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get auto-generated per-signal calibration suggestions."""
    _check_enabled()
    from app.modules.fp_rate_tracker import generate_per_signal_suggestions

    return generate_per_signal_suggestions(db)


@router.post("/{corridor_id}/apply-suggestion")
def apply_calibration_suggestion(
    corridor_id: int,
    preview: bool = Query(False),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Apply a calibration suggestion. If preview=True, runs shadow scoring first."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)

    # Get the suggestion for this corridor
    from app.modules.fp_rate_tracker import generate_per_signal_suggestions

    suggestions = generate_per_signal_suggestions(db)
    suggestion = next((s for s in suggestions if s["corridor_id"] == corridor_id), None)
    if suggestion is None:
        raise HTTPException(
            status_code=404, detail=f"No pending suggestion for corridor {corridor_id}"
        )

    # Build proposed overrides from suggestion
    proposed_overrides: dict[str, float] = {}
    for key, info in suggestion["signal_suggestions"].items():
        proposed_overrides[key] = info["proposed"]

    if preview:
        return {"preview": True, "suggestion": suggestion, "proposed_overrides": proposed_overrides}

    # Apply the suggestion: create/update CorridorScoringOverride
    existing = (
        db.query(CorridorScoringOverride)
        .filter(
            CorridorScoringOverride.corridor_id == corridor_id,
            CorridorScoringOverride.is_active.is_(True),
        )
        .first()
    )

    before_values: dict = {}
    if existing:
        before_values = {
            "corridor_multiplier_override": existing.corridor_multiplier_override,
            "gap_duration_multiplier": existing.gap_duration_multiplier,
        }
        # Apply gap_duration multiplier from suggestion if present
        for key, val in proposed_overrides.items():
            if key.startswith("gap_duration."):
                existing.gap_duration_multiplier = val
                break
        existing.description = f"Auto-calibration: {suggestion['reason']}"
    else:
        gap_dur_mult = 1.0
        for key, val in proposed_overrides.items():
            if key.startswith("gap_duration."):
                gap_dur_mult = val
                break
        override = CorridorScoringOverride(
            corridor_id=corridor_id,
            gap_duration_multiplier=gap_dur_mult,
            description=f"Auto-calibration: {suggestion['reason']}",
            created_by=auth.get("analyst_id"),
        )
        db.add(override)

    # Record calibration event
    cal_event = CalibrationEvent(
        corridor_id=corridor_id,
        event_type="suggestion_accepted",
        before_values_json=_json.dumps(before_values),
        after_values_json=_json.dumps(proposed_overrides),
        analyst_id=auth.get("analyst_id"),
        reason=suggestion["reason"],
    )
    db.add(cal_event)
    db.commit()

    return {"applied": True, "corridor_id": corridor_id, "overrides": proposed_overrides}


@router.post("/auto-calibration/run")
def run_auto_calibration(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Run scheduled calibration to generate proposals."""
    _check_enabled()
    from app.modules.fp_rate_tracker import run_scheduled_calibration

    return run_scheduled_calibration(db)
