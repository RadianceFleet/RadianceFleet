"""STS Transfer Hotspot endpoints — standalone router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detect", tags=["sts-hotspots"])


@router.post("/sts-hotspots")
def run_sts_hotspot_detection(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run STS transfer hotspot detection using DBSCAN clustering."""
    from app.modules.sts_hotspot_detector import run_hotspot_detection

    return run_hotspot_detection(db)


@router.get("/sts-hotspots/geojson")
def get_sts_hotspots_geojson(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Return all STS hotspots as a GeoJSON FeatureCollection."""
    from app.modules.sts_hotspot_detector import get_hotspots_geojson

    return get_hotspots_geojson(db)


@router.get("/sts-hotspots/{hotspot_id}")
def get_sts_hotspot(
    hotspot_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get a single STS hotspot by ID."""
    from app.modules.sts_hotspot_detector import get_hotspot

    result = get_hotspot(db, hotspot_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Hotspot not found")
    return result


@router.get("/sts-hotspots")
def list_sts_hotspots(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """List all detected STS hotspots."""
    from app.modules.sts_hotspot_detector import get_hotspots

    return get_hotspots(db)
