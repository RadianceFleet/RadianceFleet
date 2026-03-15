"""Trajectory PCA anomaly detection endpoints."""

from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/detect", tags=["trajectory-pca"])


@router.post("/trajectory-pca")
def run_trajectory_pca(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run PCA-based trajectory anomaly detection.

    Analyzes trajectory segments using Principal Component Analysis to detect
    anomalous vessel movements via reconstruction error in the minor-component
    subspace.
    """
    enabled = getattr(settings, "TRAJECTORY_PCA_ENABLED", False)
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail="Trajectory PCA detection is disabled (TRAJECTORY_PCA_ENABLED=False)",
        )

    from app.modules.trajectory_pca_detector import run_pca_detection

    dt_from = datetime(date_from.year, date_from.month, date_from.day) if date_from else None
    dt_to = datetime(date_to.year, date_to.month, date_to.day) if date_to else None

    return run_pca_detection(db, date_from=dt_from, date_to=dt_to)


@router.get("/trajectory-pca/{vessel_id}")
def get_trajectory_pca_anomalies(
    vessel_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Get PCA trajectory anomaly results for a specific vessel."""
    enabled = getattr(settings, "TRAJECTORY_PCA_ENABLED", False)
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail="Trajectory PCA detection is disabled (TRAJECTORY_PCA_ENABLED=False)",
        )

    from app.modules.trajectory_pca_detector import get_vessel_pca_anomalies

    results = get_vessel_pca_anomalies(db, vessel_id)
    if not results:
        raise HTTPException(status_code=404, detail="No PCA anomalies found for this vessel")

    return results
