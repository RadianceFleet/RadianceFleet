"""Explainability API routes — standalone router for alert score explanations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.models.gap_event import AISGapEvent
from app.modules.signal_explainer import explain_alert
from app.schemas.explainability import ExplainabilityResponse

router = APIRouter(tags=["explainability"])


@router.get(
    "/alerts/{alert_id}/explain",
    response_model=ExplainabilityResponse,
    summary="Explain an alert's risk score",
    description=(
        "Returns a human-readable breakdown of all signals contributing to "
        "the alert's risk score, grouped by category with waterfall data."
    ),
)
def get_alert_explanation(
    alert_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
) -> ExplainabilityResponse:
    """Generate an explainability report for the given alert."""
    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return explain_alert(alert, db)
