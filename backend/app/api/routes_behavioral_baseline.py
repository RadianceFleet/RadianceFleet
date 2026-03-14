"""Behavioral Baseline Per-Vessel Profiling API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detect", tags=["behavioral-baseline"])


@router.post("/behavioral-baseline")
def run_behavioral_baseline_detection(
    db: Session = Depends(get_db),
):
    """Run behavioral baseline computation for all vessels.

    Gated by BEHAVIORAL_BASELINE_ENABLED feature flag.
    """
    if not getattr(settings, "BEHAVIORAL_BASELINE_ENABLED", False):
        raise HTTPException(status_code=404, detail="Behavioral baseline detection is disabled")

    from app.modules.behavioral_baseline_detector import run_behavioral_baseline

    return run_behavioral_baseline(db)


@router.get("/behavioral-baseline/{vessel_id}")
def get_behavioral_baseline_profile(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Get the behavioral baseline profile for a vessel."""
    if not getattr(settings, "BEHAVIORAL_BASELINE_ENABLED", False):
        raise HTTPException(status_code=404, detail="Behavioral baseline detection is disabled")

    from app.modules.behavioral_baseline_detector import get_vessel_profile

    result = get_vessel_profile(db, vessel_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No behavioral profile found for vessel {vessel_id}")
    return result


@router.post("/behavioral-baseline/{vessel_id}/refresh")
def refresh_behavioral_baseline_profile(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Refresh the behavioral baseline profile for a single vessel."""
    if not getattr(settings, "BEHAVIORAL_BASELINE_ENABLED", False):
        raise HTTPException(status_code=404, detail="Behavioral baseline detection is disabled")

    from app.modules.behavioral_baseline_detector import refresh_vessel_profile

    result = refresh_vessel_profile(db, vessel_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient data to build behavioral profile for vessel {vessel_id}",
        )
    return result
