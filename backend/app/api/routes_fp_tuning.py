"""FP tuning API routes — per-corridor false-positive rate analysis and scoring overrides."""

from __future__ import annotations

import contextlib
import json
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
from app.modules.scoring_config import validate_signal_override_keys
from app.schemas.fp_tuning import (
    CalibrationEventResponse,
    CalibrationSuggestionSchema,
    CorridorFPRateSchema,
    ScoringOverrideCreate,
    ScoringOverrideResponse,
)
from app.schemas.regions import (
    RegionCreate,
    RegionResponse,
    RegionUpdate,
    ShadowScoreRequest,
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


def _serialize_override_response(
    override: CorridorScoringOverride, corridor_name: str
) -> ScoringOverrideResponse:
    """Build a ScoringOverrideResponse from an ORM object."""
    signal_overrides = None
    if override.signal_overrides_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            signal_overrides = json.loads(override.signal_overrides_json)
    return ScoringOverrideResponse(
        override_id=override.override_id,
        corridor_id=override.corridor_id,
        corridor_name=corridor_name,
        corridor_multiplier_override=override.corridor_multiplier_override,
        gap_duration_multiplier=override.gap_duration_multiplier,
        description=override.description,
        created_by=override.created_by,
        created_at=override.created_at,
        updated_at=override.updated_at,
        is_active=override.is_active,
        signal_overrides=signal_overrides,
        region_id=override.region_id,
    )


def _record_calibration_event(
    db: Session,
    *,
    corridor_id: int | None,
    event_type: str,
    before_values: dict | None = None,
    after_values: dict | None = None,
    analyst_id: int | None = None,
    reason: str | None = None,
) -> CalibrationEvent:
    """Record a calibration audit trail event."""
    evt = CalibrationEvent(
        corridor_id=corridor_id,
        event_type=event_type,
        before_values_json=json.dumps(before_values) if before_values else None,
        after_values_json=json.dumps(after_values) if after_values else None,
        analyst_id=analyst_id,
        reason=reason,
    )
    db.add(evt)
    return evt


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
    corridor = _get_corridor_or_404(db, corridor_id)
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
    return _serialize_override_response(override, corridor.name)


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


@router.get("/{corridor_id}/calibration-history", response_model=list[CalibrationEventResponse])
def get_calibration_history(
    corridor_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """List calibration events for a corridor."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)
    events = (
        db.query(CalibrationEvent)
        .filter(CalibrationEvent.corridor_id == corridor_id)
        .order_by(CalibrationEvent.created_at.desc())
        .all()
    )
    result = []
    for evt in events:
        before_values = None
        after_values = None
        impact_summary = None
        if evt.before_values_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                before_values = json.loads(evt.before_values_json)
        if evt.after_values_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                after_values = json.loads(evt.after_values_json)
        if evt.impact_summary_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                impact_summary = json.loads(evt.impact_summary_json)
        result.append(
            CalibrationEventResponse(
                event_id=evt.event_id,
                corridor_id=evt.corridor_id,
                region_id=evt.region_id,
                event_type=evt.event_type,
                before_values=before_values,
                after_values=after_values,
                impact_summary=impact_summary,
                analyst_id=evt.analyst_id,
                reason=evt.reason,
                created_at=evt.created_at,
            )
        )
    return result


@router.get("/{corridor_id}/calibration-impact")
def preview_calibration_impact(
    corridor_id: int,
    signal_overrides: str = Query(None, description="JSON-encoded signal overrides to preview"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Preview retroactive impact of proposed overrides on recent alerts."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)

    if not signal_overrides:
        return {"corridor_id": corridor_id, "affected_alerts": 0, "score_changes": []}

    try:
        overrides_dict = json.loads(signal_overrides)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON in signal_overrides") from exc

    # Validate override keys
    invalid_keys = validate_signal_override_keys(overrides_dict)
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid override keys: {', '.join(invalid_keys)}",
        )

    # Fetch recent scored alerts for this corridor
    from app.models.gap_event import AISGapEvent

    alerts = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.corridor_id == corridor_id,
            AISGapEvent.risk_score > 0,
        )
        .order_by(AISGapEvent.gap_start_utc.desc())
        .limit(50)
        .all()
    )

    from app.modules.scoring_config import load_scoring_config

    load_scoring_config()  # ensure config is loaded

    score_changes = []
    for alert in alerts:
        old_score = alert.risk_score
        # We report what sections would change, not re-score (avoids side effects)
        score_changes.append(
            {
                "gap_event_id": alert.gap_event_id,
                "current_score": old_score,
                "overrides_applied": list(overrides_dict.keys()),
            }
        )

    return {
        "corridor_id": corridor_id,
        "affected_alerts": len(alerts),
        "score_changes": score_changes,
    }


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

    # Validate signal override keys if provided
    if body.signal_overrides:
        invalid_keys = validate_signal_override_keys(body.signal_overrides)
        if invalid_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid signal override keys: {', '.join(invalid_keys)}",
            )

    signal_overrides_json = json.dumps(body.signal_overrides) if body.signal_overrides else None

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
        # Capture before values for audit trail
        before_values = {
            "corridor_multiplier_override": existing.corridor_multiplier_override,
            "gap_duration_multiplier": existing.gap_duration_multiplier,
            "signal_overrides_json": existing.signal_overrides_json,
        }

        existing.corridor_multiplier_override = body.corridor_multiplier_override
        existing.gap_duration_multiplier = body.gap_duration_multiplier
        existing.description = body.description
        existing.signal_overrides_json = signal_overrides_json
        db.flush()
        override = existing

        # Record calibration event
        after_values = {
            "corridor_multiplier_override": body.corridor_multiplier_override,
            "gap_duration_multiplier": body.gap_duration_multiplier,
            "signal_overrides": body.signal_overrides,
        }
        _record_calibration_event(
            db,
            corridor_id=corridor_id,
            event_type="override_updated",
            before_values=before_values,
            after_values=after_values,
            analyst_id=auth.get("analyst_id"),
            reason=body.description,
        )
    else:
        override = CorridorScoringOverride(
            corridor_id=corridor_id,
            corridor_multiplier_override=body.corridor_multiplier_override,
            gap_duration_multiplier=body.gap_duration_multiplier,
            description=body.description,
            signal_overrides_json=signal_overrides_json,
            created_by=auth.get("analyst_id"),
        )
        db.add(override)
        db.flush()

        # Record calibration event
        after_values = {
            "corridor_multiplier_override": body.corridor_multiplier_override,
            "gap_duration_multiplier": body.gap_duration_multiplier,
            "signal_overrides": body.signal_overrides,
        }
        _record_calibration_event(
            db,
            corridor_id=corridor_id,
            event_type="override_created",
            after_values=after_values,
            analyst_id=auth.get("analyst_id"),
            reason=body.description,
        )

    db.commit()
    db.refresh(override)

    return _serialize_override_response(override, corridor.name)


@router.delete("/{corridor_id}/scoring-override")
def deactivate_scoring_override(
    corridor_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
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

    # Capture before values for audit trail
    before_values = {
        "corridor_multiplier_override": override.corridor_multiplier_override,
        "gap_duration_multiplier": override.gap_duration_multiplier,
        "signal_overrides_json": override.signal_overrides_json,
        "is_active": True,
    }

    override.is_active = False

    _record_calibration_event(
        db,
        corridor_id=corridor_id,
        event_type="override_deactivated",
        before_values=before_values,
        after_values={"is_active": False},
        analyst_id=auth.get("analyst_id"),
    )

    db.commit()
    return {"detail": f"Override for corridor {corridor_id} deactivated"}


# ---------------------------------------------------------------------------
# Shadow scoring endpoint
# ---------------------------------------------------------------------------


@router.post("/{corridor_id}/shadow-score")
def run_shadow_score(
    corridor_id: int,
    body: ShadowScoreRequest,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Run shadow scoring with proposed overrides — read-only, does not persist changes."""
    _check_enabled()
    _get_corridor_or_404(db, corridor_id)
    from app.modules.shadow_scorer import shadow_score

    result = shadow_score(db, corridor_id, body.model_dump(), limit=body.limit)
    return result


# ---------------------------------------------------------------------------
# Region endpoints
# ---------------------------------------------------------------------------


def _get_region_or_404(db: Session, region_id: int):
    from app.models.scoring_region import ScoringRegion

    region = db.query(ScoringRegion).filter(ScoringRegion.region_id == region_id).first()
    if region is None:
        raise HTTPException(status_code=404, detail=f"Region {region_id} not found")
    return region


def _region_to_response(region, fp_rate: float | None = None) -> RegionResponse:
    import contextlib
    import json

    corridor_ids: list[int] = []
    if region.corridor_ids_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            corridor_ids = json.loads(region.corridor_ids_json)

    signal_overrides: dict[str, float] | None = None
    if region.signal_overrides_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            signal_overrides = json.loads(region.signal_overrides_json)

    return RegionResponse(
        region_id=region.region_id,
        name=region.name,
        description=region.description,
        corridor_ids=corridor_ids,
        signal_overrides=signal_overrides,
        corridor_multiplier_override=region.corridor_multiplier_override,
        gap_duration_multiplier=region.gap_duration_multiplier,
        is_active=region.is_active,
        created_by=region.created_by,
        created_at=region.created_at,
        updated_at=region.updated_at,
        fp_rate=fp_rate,
    )


@router.post("/regions", response_model=RegionResponse, tags=["regions"])
def create_region(
    body: RegionCreate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Create a new scoring region grouping corridors."""
    import json

    _check_enabled()

    from app.models.scoring_region import ScoringRegion

    # Check uniqueness
    existing = db.query(ScoringRegion).filter(ScoringRegion.name == body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Region name '{body.name}' already exists")

    region = ScoringRegion(
        name=body.name,
        description=body.description,
        corridor_ids_json=json.dumps(body.corridor_ids) if body.corridor_ids else None,
        signal_overrides_json=json.dumps(body.signal_overrides) if body.signal_overrides else None,
        corridor_multiplier_override=body.corridor_multiplier_override,
        gap_duration_multiplier=body.gap_duration_multiplier,
        created_by=auth.get("analyst_id"),
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return _region_to_response(region)


@router.get("/regions", response_model=list[RegionResponse], tags=["regions"])
def list_regions(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """List all scoring regions."""
    _check_enabled()

    from app.models.scoring_region import ScoringRegion

    regions = db.query(ScoringRegion).order_by(ScoringRegion.name).all()
    return [_region_to_response(r) for r in regions]


@router.get("/regions/{region_id}", response_model=RegionResponse, tags=["regions"])
def get_region(
    region_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get a single scoring region with computed FP rate."""
    _check_enabled()
    region = _get_region_or_404(db, region_id)

    from app.modules.fp_rate_tracker import compute_region_fp_rate

    rate_result = compute_region_fp_rate(db, region_id)
    fp_rate = rate_result.fp_rate if rate_result else None
    return _region_to_response(region, fp_rate=fp_rate)


@router.put("/regions/{region_id}", response_model=RegionResponse, tags=["regions"])
def update_region(
    region_id: int,
    body: RegionUpdate,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Update a scoring region."""
    import json

    _check_enabled()
    region = _get_region_or_404(db, region_id)

    from app.models.scoring_region import ScoringRegion

    if body.name is not None:
        existing = (
            db.query(ScoringRegion)
            .filter(ScoringRegion.name == body.name, ScoringRegion.region_id != region_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409, detail=f"Region name '{body.name}' already exists"
            )
        region.name = body.name
    if body.description is not None:
        region.description = body.description
    if body.corridor_ids is not None:
        region.corridor_ids_json = json.dumps(body.corridor_ids)
    if body.signal_overrides is not None:
        region.signal_overrides_json = json.dumps(body.signal_overrides)
    if body.corridor_multiplier_override is not None:
        region.corridor_multiplier_override = body.corridor_multiplier_override
    if body.gap_duration_multiplier is not None:
        region.gap_duration_multiplier = body.gap_duration_multiplier
    if body.is_active is not None:
        region.is_active = body.is_active

    db.commit()
    db.refresh(region)
    return _region_to_response(region)


@router.delete("/regions/{region_id}", tags=["regions"])
def delete_region(
    region_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Delete a scoring region."""
    _check_enabled()
    region = _get_region_or_404(db, region_id)
    db.delete(region)
    db.commit()
    return {"detail": f"Region {region_id} deleted"}


@router.post("/regions/{region_id}/corridors", tags=["regions"])
def add_corridor_to_region(
    region_id: int,
    body: dict,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Add a corridor to a scoring region."""
    import contextlib
    import json

    _check_enabled()
    region = _get_region_or_404(db, region_id)

    corridor_id = body.get("corridor_id")
    if corridor_id is None:
        raise HTTPException(status_code=422, detail="corridor_id is required")

    # Verify corridor exists
    _get_corridor_or_404(db, corridor_id)

    corridor_ids: list[int] = []
    if region.corridor_ids_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            corridor_ids = json.loads(region.corridor_ids_json)

    if corridor_id not in corridor_ids:
        corridor_ids.append(corridor_id)
        region.corridor_ids_json = json.dumps(corridor_ids)
        db.commit()
        db.refresh(region)

    return _region_to_response(region)


@router.delete("/regions/{region_id}/corridors/{corridor_id}", tags=["regions"])
def remove_corridor_from_region(
    region_id: int,
    corridor_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Remove a corridor from a scoring region."""
    import contextlib
    import json

    _check_enabled()
    region = _get_region_or_404(db, region_id)

    corridor_ids: list[int] = []
    if region.corridor_ids_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            corridor_ids = json.loads(region.corridor_ids_json)

    if corridor_id in corridor_ids:
        corridor_ids.remove(corridor_id)
        region.corridor_ids_json = json.dumps(corridor_ids) if corridor_ids else None
        db.commit()
        db.refresh(region)

    return _region_to_response(region)
