"""Incremental scoring pipeline — only rescore vessels with new data since last run.

Provides ~10x speedup over full rescore by tracking per-vessel dirty flags.
Vessels are marked dirty when new gap events are created, merges occur, or
the scoring config changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import update as sa_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.models.vessel_scoring_state import VesselScoringState

logger = logging.getLogger(__name__)


def mark_vessel_dirty(db: Session, vessel_id: int) -> None:
    """Upsert VesselScoringState with dirty=True for a single vessel."""
    existing = db.query(VesselScoringState).filter(
        VesselScoringState.vessel_id == vessel_id
    ).first()
    if existing:
        existing.dirty = True
    else:
        db.add(VesselScoringState(vessel_id=vessel_id, dirty=True))
    db.flush()


def mark_vessels_dirty_bulk(db: Session, vessel_ids: set[int]) -> None:
    """Bulk mark vessels as dirty — single UPDATE for existing, bulk INSERT for new.

    Uses SQLite upsert (INSERT ... ON CONFLICT ... DO UPDATE) for efficiency.
    """
    if not vessel_ids:
        return

    ids_list = list(vessel_ids)

    # Use SQLite INSERT ... ON CONFLICT for efficient upsert
    stmt = sqlite_insert(VesselScoringState).values(
        [{"vessel_id": vid, "dirty": True} for vid in ids_list]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["vessel_id"],
        set_={"dirty": True},
    )
    db.execute(stmt)
    db.flush()


def get_dirty_vessels(db: Session, batch_size: int = 500) -> list[int]:
    """Return vessel_ids where dirty=True, limited by batch_size."""
    rows = (
        db.query(VesselScoringState.vessel_id)
        .filter(VesselScoringState.dirty.is_(True))
        .limit(batch_size)
        .all()
    )
    return [r.vessel_id for r in rows]


def compute_config_hash() -> str:
    """Load scoring YAML and compute SHA-256 hash of canonical JSON representation."""
    from app.modules.scoring_config import load_scoring_config

    config = load_scoring_config()
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _mark_all_vessels_dirty(db: Session) -> int:
    """Mark every active vessel as dirty. Returns count of vessels marked."""
    vessel_ids = {
        r.vessel_id
        for r in db.query(Vessel.vessel_id)
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .all()
    }
    if vessel_ids:
        mark_vessels_dirty_bulk(db, vessel_ids)
    return len(vessel_ids)


def score_vessel_alerts(
    db: Session, vessel_id: int, config: dict, scoring_date: datetime | None = None
) -> int:
    """Rescore all gap events for a single vessel. Returns count of alerts scored."""
    from app.modules.risk_scoring import (
        _count_gaps_in_window,
        _load_corridor_overrides,
        _merge_config_with_overrides,
        compute_gap_score,
    )

    corridor_overrides = _load_corridor_overrides(db)
    alerts = db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id).all()
    scored = 0

    for alert in alerts:
        if getattr(alert, "is_feed_outage", False):
            continue
        gaps_7d = _count_gaps_in_window(db, alert, 7)
        gaps_14d = _count_gaps_in_window(db, alert, 14)
        gaps_30d = _count_gaps_in_window(db, alert, 30)

        merged_config = _merge_config_with_overrides(
            config, alert.corridor_id, corridor_overrides
        )
        score, breakdown = compute_gap_score(
            alert,
            merged_config,
            gaps_in_7d=gaps_7d,
            gaps_in_14d=gaps_14d,
            gaps_in_30d=gaps_30d,
            scoring_date=scoring_date,
            db=db,
            pre_gap_sog=getattr(alert, "pre_gap_sog", None),
        )
        alert.risk_score = score
        alert.risk_breakdown_json = breakdown
        scored += 1

    # After rescoring a vessel's alerts, update group max_score if Task 43 is integrated
    try:
        from app.modules.alert_dedup_engine import update_group_max_score
        # update_group_max_score would be called here for affected groups
    except ImportError:
        pass  # Task 43 not yet integrated

    return scored


def incremental_score_alerts(
    db: Session, scoring_date: datetime | None = None
) -> dict:
    """Main entry point for incremental scoring pipeline.

    1. Compute config hash; if changed from last run, mark ALL vessels dirty.
    2. Get dirty vessels in batches.
    3. For each batch, rescore using compute_gap_score().
    4. Update VesselScoringState (clear dirty, update hash, scored_at).
    5. Return stats: {scored, skipped, config_changed}.
    """
    from app.modules.scoring_config import load_scoring_config

    config = load_scoring_config()
    current_hash = compute_config_hash()
    config_changed = False

    # Check if config has changed since any vessel's last scoring
    last_version = (
        db.query(VesselScoringState.scoring_version)
        .filter(VesselScoringState.scoring_version.isnot(None))
        .limit(1)
        .scalar()
    )
    if last_version is not None and last_version != current_hash:
        config_changed = True
        count = _mark_all_vessels_dirty(db)
        logger.info(
            "Scoring config changed (%s -> %s), marked %d vessels dirty",
            last_version[:8],
            current_hash[:8],
            count,
        )

    batch_size = settings.INCREMENTAL_SCORING_BATCH_SIZE
    total_scored = 0
    total_alerts = 0
    now = datetime.now(UTC)

    while True:
        dirty_ids = get_dirty_vessels(db, batch_size=batch_size)
        if not dirty_ids:
            break

        for vessel_id in dirty_ids:
            alerts_scored = score_vessel_alerts(db, vessel_id, config, scoring_date)
            total_alerts += alerts_scored

            # Update scoring state
            state = db.query(VesselScoringState).filter(
                VesselScoringState.vessel_id == vessel_id
            ).first()
            if state:
                state.dirty = False
                state.last_scored_at = now
                state.scoring_version = current_hash
            else:
                db.add(VesselScoringState(
                    vessel_id=vessel_id,
                    dirty=False,
                    last_scored_at=now,
                    scoring_version=current_hash,
                ))

        total_scored += len(dirty_ids)
        db.commit()

    # Count vessels that were not dirty (skipped)
    total_vessels = (
        db.query(Vessel.vessel_id)
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .count()
    )
    skipped = total_vessels - total_scored

    logger.info(
        "Incremental scoring complete: scored=%d, skipped=%d, alerts=%d, config_changed=%s",
        total_scored,
        skipped,
        total_alerts,
        config_changed,
    )

    return {
        "scored": total_scored,
        "skipped": skipped,
        "alerts_scored": total_alerts,
        "config_changed": config_changed,
        "config_hash": current_hash[:8],
    }
