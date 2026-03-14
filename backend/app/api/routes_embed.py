"""Embeddable widget endpoints — API-key gated, compact JSON for iframe widgets."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])


# ---------------------------------------------------------------------------
# CORS middleware for embed endpoints
# ---------------------------------------------------------------------------


def _add_embed_cors_headers(response: Response, request: Request) -> None:
    """Add CORS headers for configured embed origins."""
    origins = getattr(settings, "EMBED_CORS_ORIGINS", "")
    origin = request.headers.get("origin", "")
    if origins == "*":
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origins:
        allowed = [o.strip() for o in origins.split(",") if o.strip()]
        if origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "X-API-Key, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"


# ---------------------------------------------------------------------------
# Auth dependency — API-key only (no JWT)
# ---------------------------------------------------------------------------


def _require_embed_api_key(request: Request, db: Session = Depends(get_db)) -> dict:
    """Validate X-API-Key header for embed endpoints."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    from app.auth import verify_api_key

    result = verify_api_key(api_key, db)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return result


# ---------------------------------------------------------------------------
# Helper: risk tier from score
# ---------------------------------------------------------------------------


def _risk_tier(score: int | None) -> str:
    """Map numeric risk score to human-readable tier."""
    if score is None:
        return "unknown"
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "minimal"


# ---------------------------------------------------------------------------
# GET /embed/vessel/{vessel_id}/summary
# ---------------------------------------------------------------------------


@router.get("/vessel/{vessel_id}/summary")
def embed_vessel_summary(
    vessel_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _auth: dict = Depends(_require_embed_api_key),
):
    """Compact vessel summary for widget embedding."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    _add_embed_cors_headers(response, request)

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    # Best available risk score
    last_gap = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.vessel_id == vessel_id)
        .order_by(AISGapEvent.risk_score.desc())
        .first()
    )
    risk_score = last_gap.risk_score if last_gap else getattr(vessel, "watchlist_stub_score", None)

    on_watchlist = (
        db.query(VesselWatchlist)
        .filter(VesselWatchlist.vessel_id == vessel_id, VesselWatchlist.is_active)
        .first()
    ) is not None

    return {
        "vessel_id": vessel.vessel_id,
        "name": vessel.name,
        "mmsi": vessel.mmsi,
        "imo": vessel.imo,
        "flag": vessel.flag,
        "vessel_type": vessel.vessel_type,
        "risk_score": risk_score,
        "risk_tier": _risk_tier(risk_score),
        "on_watchlist": on_watchlist,
    }


# ---------------------------------------------------------------------------
# GET /embed/vessel/{vessel_id}/timeline
# ---------------------------------------------------------------------------


@router.get("/vessel/{vessel_id}/timeline")
def embed_vessel_timeline(
    vessel_id: int,
    request: Request,
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _auth: dict = Depends(_require_embed_api_key),
):
    """Recent alert timeline for widget embedding (last 30 days)."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    _add_embed_cors_headers(response, request)

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    cutoff = datetime.now(UTC) - timedelta(days=30)
    gaps = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= cutoff,
        )
        .order_by(AISGapEvent.gap_start_utc.desc())
        .limit(limit)
        .all()
    )

    items = []
    for g in gaps:
        score = g.risk_score if g.risk_score is not None else 0
        items.append(
            {
                "gap_event_id": g.gap_event_id,
                "date": g.gap_start_utc.isoformat() if g.gap_start_utc else None,
                "duration_minutes": g.duration_minutes,
                "risk_score": score,
                "risk_tier": _risk_tier(score),
                "status": str(g.status.value) if hasattr(g.status, "value") else str(g.status),
            }
        )

    return {"vessel_id": vessel_id, "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# GET /embed/vessel/{vessel_id}/risk
# ---------------------------------------------------------------------------


@router.get("/vessel/{vessel_id}/risk")
def embed_vessel_risk(
    vessel_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _auth: dict = Depends(_require_embed_api_key),
):
    """Simplified risk breakdown for widget embedding."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    _add_embed_cors_headers(response, request)

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    # Get highest-scoring gap event for breakdown
    last_gap = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.vessel_id == vessel_id)
        .order_by(AISGapEvent.risk_score.desc())
        .first()
    )

    risk_score = None
    signals = []

    if last_gap:
        risk_score = last_gap.risk_score
        breakdown = getattr(last_gap, "risk_breakdown_json", None) or {}
        if isinstance(breakdown, dict):
            # Extract top 5 contributing signals
            scored = [
                {"signal": k, "value": v}
                for k, v in breakdown.items()
                if isinstance(v, (int, float)) and v > 0
            ]
            scored.sort(key=lambda x: x["value"], reverse=True)
            signals = scored[:5]
    else:
        # Fall back to stub score
        risk_score = getattr(vessel, "watchlist_stub_score", None)
        stub_breakdown = getattr(vessel, "watchlist_stub_breakdown", None) or {}
        if isinstance(stub_breakdown, dict):
            scored = [
                {"signal": k, "value": v}
                for k, v in stub_breakdown.items()
                if isinstance(v, (int, float)) and v > 0
            ]
            scored.sort(key=lambda x: x["value"], reverse=True)
            signals = scored[:5]

    return {
        "vessel_id": vessel_id,
        "risk_score": risk_score,
        "risk_tier": _risk_tier(risk_score),
        "top_signals": signals,
    }


# ---------------------------------------------------------------------------
# GET /embed/vessel/{vessel_id}/position
# ---------------------------------------------------------------------------


@router.get("/vessel/{vessel_id}/position")
def embed_vessel_position(
    vessel_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _auth: dict = Depends(_require_embed_api_key),
):
    """Latest vessel position for mini-map widget."""
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel

    _add_embed_cors_headers(response, request)

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    latest = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel_id)
        .order_by(AISPoint.timestamp_utc.desc())
        .first()
    )

    if not latest:
        return {
            "vessel_id": vessel_id,
            "lat": None,
            "lon": None,
            "timestamp": None,
            "sog": None,
            "cog": None,
        }

    return {
        "vessel_id": vessel_id,
        "lat": latest.lat,
        "lon": latest.lon,
        "timestamp": latest.timestamp_utc.isoformat() if latest.timestamp_utc else None,
        "sog": getattr(latest, "sog", None),
        "cog": getattr(latest, "cog", None),
    }
