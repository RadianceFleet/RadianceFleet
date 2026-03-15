"""Jamming Zone detection and query endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detect", tags=["jamming-zones"])


@router.post("/jamming-zones")
def run_detection(
    window_hours: int = Query(168, ge=1, le=8760, description="Lookback window in hours"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run GPS jamming zone detection over the specified time window."""
    if not getattr(settings, "JAMMING_DETECTION_ENABLED", False):
        raise HTTPException(status_code=400, detail="Jamming zone detection is disabled")

    from app.modules.jamming_zone_detector import run_jamming_detection

    return run_jamming_detection(db, window_hours=window_hours)


@router.get("/jamming-zones")
def list_zones(
    status: str | None = Query(None, description="Filter by status: active, decaying, expired"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """List all detected jamming zones, optionally filtered by status."""
    from app.modules.jamming_zone_detector import get_jamming_zones

    return get_jamming_zones(db, status=status)


@router.get("/jamming-zones/geojson")
def zones_geojson(
    status: str | None = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Return jamming zones as a GeoJSON FeatureCollection."""
    from app.modules.jamming_zone_detector import get_jamming_zones_geojson

    return get_jamming_zones_geojson(db, status=status)


@router.get("/jamming-zones/{zone_id}")
def get_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get a single jamming zone by ID."""
    from app.modules.jamming_zone_detector import get_jamming_zone

    zone = get_jamming_zone(db, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Jamming zone {zone_id} not found")
    return zone
