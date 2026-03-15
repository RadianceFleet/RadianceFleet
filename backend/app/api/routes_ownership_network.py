"""Ownership network graph API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detect", tags=["ownership-network"])


@router.get("/ownership-network/{vessel_id}")
def get_vessel_ownership_network(
    vessel_id: int,
    depth: int = Query(default=3, ge=1, le=10, description="Max BFS depth"),
    limit: int = Query(default=100, ge=1, le=500, description="Max nodes"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get ownership network graph centered on a specific vessel."""
    from app.models.vessel import Vessel
    from app.modules.network_graph_builder import build_ownership_network

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    return build_ownership_network(
        db,
        vessel_id=vessel_id,
        depth=depth,
        limit=limit,
    )


@router.get("/ownership-network")
def get_fleet_ownership_network(
    sanctioned_only: bool = Query(default=False, description="Only sanctioned nodes"),
    spv_only: bool = Query(default=False, description="Only SPV nodes"),
    jurisdiction: str | None = Query(default=None, description="Filter by jurisdiction"),
    depth: int = Query(default=3, ge=1, le=10, description="Max BFS depth"),
    limit: int = Query(default=100, ge=1, le=500, description="Max nodes"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get fleet-wide ownership network graph with optional filters."""
    from app.modules.network_graph_builder import build_ownership_network

    return build_ownership_network(
        db,
        vessel_id=None,
        depth=depth,
        limit=limit,
        sanctioned_only=sanctioned_only,
        spv_only=spv_only,
        jurisdiction=jurisdiction,
    )
