"""Watchlist stub scoring — scores vessels with no AIS history.

Extracted from risk_scoring.py to reduce module size.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models.gap_event import AISGapEvent
from app.modules.scoring_config import (
    _WATCHLIST_DEFAULTS,
    _WATCHLIST_KEY_MAP,
    load_scoring_config,
)

logger = logging.getLogger(__name__)


def score_watchlist_stubs(db: Session, config: dict | None = None) -> dict:
    """Score watchlist-only stubs that have never appeared on AIS.

    Runs alongside score_all_alerts() in the pipeline. Vessels are selected
    that have active watchlist entries but NO AIS observations AND no gap events.
    Returns {scored: int, cleared: int}.

    NOTE: Sequencing caveat: this runs before vessel merging (Step 10). If a stub
    is merged in Step 10 of the same run, its watchlist_stub_score is only cleared
    on the NEXT run's Phase 1 cleanup. This is acceptable: a merged vessel's score
    is superseded by its absorbing vessel's last_risk_score via merged_into_vessel_id,
    and the API absorbed branch already returns watchlist_stub_score=None.
    """
    if not settings.WATCHLIST_STUB_SCORING_ENABLED:
        return {"scored": 0, "cleared": 0}

    config = config or load_scoring_config()
    stub_cfg = config.get("watchlist_stub_scoring", {})
    watchlist_cfg = config.get("watchlist", {})
    current_year = datetime.utcnow().year

    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner
    from app.models.vessel_watchlist import VesselWatchlist

    # Import helpers from the main module (avoid circular by importing at call time)
    from app.modules.risk_scoring import _vessel_age_points, _vessel_size_multiplier

    # Use AISPoint and AISGapEvent existence checks, not risk_score value
    # (risk_score defaults to 0 for unscored gaps — cannot use > 0 as proxy)
    vessels_with_ais = db.query(AISPoint.vessel_id).distinct()
    vessels_with_gaps = db.query(AISGapEvent.vessel_id).distinct()
    active_watchlist_ids = (
        db.query(VesselWatchlist.vessel_id).filter(VesselWatchlist.is_active == True).distinct()  # noqa: E712
    )

    # Phase 1: Clear stale scores for vessels that no longer qualify as stubs
    stale = (
        db.query(Vessel)
        .filter(Vessel.watchlist_stub_score.isnot(None))
        .filter(
            ~Vessel.vessel_id.in_(active_watchlist_ids)
            | Vessel.vessel_id.in_(vessels_with_ais)
            | Vessel.vessel_id.in_(vessels_with_gaps)
            | Vessel.merged_into_vessel_id.isnot(None)
        )
        .all()
    )
    for v in stale:
        v.watchlist_stub_score = None
        v.watchlist_stub_breakdown = None
    cleared = len(stale)

    # Phase 2: Score current stubs
    stub_vessels = (
        db.query(Vessel)
        .join(VesselWatchlist, VesselWatchlist.vessel_id == Vessel.vessel_id)
        .filter(VesselWatchlist.is_active == True)  # noqa: E712
        .filter(Vessel.vessel_id.notin_(vessels_with_ais))
        .filter(Vessel.vessel_id.notin_(vessels_with_gaps))
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .all()
    )
    stub_ids = [v.vessel_id for v in stub_vessels]

    # Batch-load watchlist entries (avoid N+1 queries)
    watchlist_by_vessel: dict[int, list] = {}
    for w in (
        db.query(VesselWatchlist)
        .filter(
            VesselWatchlist.vessel_id.in_(stub_ids),
            VesselWatchlist.is_active == True,  # noqa: E712
        )
        .all()
    ):
        watchlist_by_vessel.setdefault(w.vessel_id, []).append(w)

    verified_owner_ids: set[int] = (
        {
            row.vessel_id
            for row in db.query(VesselOwner.vessel_id)
            .filter(
                VesselOwner.vessel_id.in_(stub_ids),
                VesselOwner.verified_at.isnot(None),
            )
            .all()
        }
        if stub_ids
        else set()
    )

    for vessel in stub_vessels:
        breakdown: dict[str, int] = {}

        # Watchlist source signals
        for w in watchlist_by_vessel.get(vessel.vessel_id, []):
            yaml_key = _WATCHLIST_KEY_MAP.get(w.watchlist_source)
            if yaml_key:
                breakdown[f"watchlist_{w.watchlist_source}"] = watchlist_cfg.get(
                    yaml_key, _WATCHLIST_DEFAULTS.get(w.watchlist_source, 20)
                )
            else:
                breakdown[f"watchlist_{w.watchlist_source}"] = watchlist_cfg.get(
                    w.watchlist_source, 20
                )

        # Flag risk
        flag_risk = str(getattr(vessel, "flag_risk_category", "") or "").lower()
        if hasattr(getattr(vessel, "flag_risk_category", None), "value"):
            flag_risk = str(vessel.flag_risk_category.value).lower()
        if "high_risk" in flag_risk:
            breakdown["high_risk_flag"] = stub_cfg.get("high_risk_flag", 15)

        # Vessel age
        age_result = _vessel_age_points(vessel, config, current_year)
        if age_result:
            breakdown[age_result[0]] = age_result[1]

        # Missing metadata penalties
        if vessel.deadweight is None:
            breakdown["missing_dwt_stub"] = stub_cfg.get("missing_dwt_stub", 8)
        if vessel.vessel_type is None:
            breakdown["missing_type_stub"] = stub_cfg.get("missing_type_stub", 5)

        # No AIS history: always true for stubs selected by this query
        breakdown["no_ais_history"] = stub_cfg.get("no_ais_history", 10)

        # Unverified ownership
        if vessel.vessel_id not in verified_owner_ids:
            breakdown["unverified_ownership"] = stub_cfg.get("unverified_ownership", 8)

        # Apply vessel size multiplier
        size_mult, _ = _vessel_size_multiplier(vessel, config)
        risk_total = sum(v for v in breakdown.values() if v > 0)
        vessel.watchlist_stub_score = min(200, round(risk_total * size_mult))
        vessel.watchlist_stub_breakdown = breakdown

    if stub_vessels:
        db.commit()

    return {"scored": len(stub_vessels), "cleared": cleared}
