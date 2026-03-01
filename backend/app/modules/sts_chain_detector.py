"""STS relay chain detector (Stage 3-B).

Reconstructs multi-hop ship-to-ship transfer relay chains by building a
directed graph from STS events and walking connected components
chronologically.

Scoring:
  - 3-hop chain: +20 per vessel involved
  - 4+-hop chain: +40 per vessel involved
  - Intermediary vessels (appear in middle of chains): flagged for extra +15

Results are stored as FleetAlert records with alert_type='sts_relay_chain'.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, date, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def detect_sts_chains(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Detect multi-hop STS relay chains.

    Args:
        db: SQLAlchemy session.
        date_from: Start of window (default: 30 days ago).
        date_to: End of window (default: today).

    Returns:
        Dict with chains_found, alerts_created, vessels_flagged.
    """
    if not settings.STS_CHAIN_DETECTION_ENABLED:
        return {"status": "disabled", "chains_found": 0, "alerts_created": 0}

    from app.models.sts_transfer import StsTransferEvent
    from app.models.fleet_alert import FleetAlert

    # Default window: last 30 days
    if date_to is None:
        date_to = datetime.now(timezone.utc).date()
    if date_from is None:
        date_from = date_to - timedelta(days=30)

    # Convert dates to datetime for comparison
    dt_from = datetime.combine(date_from, datetime.min.time())
    dt_to = datetime.combine(date_to, datetime.max.time())

    # Query STS events in window
    sts_events = (
        db.query(StsTransferEvent)
        .filter(
            StsTransferEvent.start_time_utc >= dt_from,
            StsTransferEvent.start_time_utc <= dt_to,
        )
        .order_by(StsTransferEvent.start_time_utc)
        .all()
    )

    if not sts_events:
        return {"status": "ok", "chains_found": 0, "alerts_created": 0, "vessels_flagged": 0}

    # Build adjacency list (directed graph: vessel_1 -> vessel_2)
    adjacency: dict[int, list[tuple[int, Any]]] = defaultdict(list)
    all_vessel_ids: set[int] = set()

    for ev in sts_events:
        v1 = ev.vessel_1_id
        v2 = ev.vessel_2_id
        adjacency[v1].append((v2, ev))
        all_vessel_ids.add(v1)
        all_vessel_ids.add(v2)

    # Find connected components using BFS (undirected for component discovery)
    visited: set[int] = set()
    components: list[set[int]] = []

    for vid in all_vessel_ids:
        if vid in visited:
            continue
        component: set[int] = set()
        queue: deque[int] = deque([vid])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            # Follow edges in both directions for component discovery
            for neighbor, _ev in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
            # Also check reverse edges
            for src, edges in adjacency.items():
                for neighbor, _ev in edges:
                    if neighbor == current and src not in visited:
                        queue.append(src)
        if len(component) >= 3:
            components.append(component)

    stats: dict[str, Any] = {
        "status": "ok",
        "chains_found": 0,
        "alerts_created": 0,
        "vessels_flagged": 0,
    }

    # Walk each component chronologically to find relay chains
    for component in components:
        # Get all STS events involving vessels in this component
        component_events = []
        for ev in sts_events:
            if ev.vessel_1_id in component or ev.vessel_2_id in component:
                component_events.append(ev)

        # Sort by start time
        component_events.sort(key=lambda e: e.start_time_utc)

        # Build chronological chain by following transfers
        chain = _build_chain(component_events, component)

        if len(chain) < 3:
            continue

        chain_length = len(chain)
        stats["chains_found"] += 1

        # Identify intermediary vessels (not first or last in chain)
        intermediary_ids = chain[1:-1]

        # Score: +20 for 3-hop, +40 for 4+
        if chain_length >= 4:
            score = 40
        else:
            score = 20

        # Build evidence
        hops = []
        for ev in component_events:
            hops.append({
                "from_vessel_id": ev.vessel_1_id,
                "to_vessel_id": ev.vessel_2_id,
                "start_time": ev.start_time_utc.isoformat() if ev.start_time_utc else None,
                "end_time": ev.end_time_utc.isoformat() if ev.end_time_utc else None,
            })

        evidence = {
            "subtype": "sts_relay_chain",
            "chain_length": chain_length,
            "chain_vessel_ids": chain,
            "intermediary_vessel_ids": intermediary_ids,
            "hops": hops,
        }

        # Deduplicate: check if a chain alert already exists for these vessels
        vessel_ids_sorted = sorted(chain)
        existing = (
            db.query(FleetAlert)
            .filter(
                FleetAlert.alert_type == "sts_relay_chain",
            )
            .all()
        )

        already_exists = False
        for ex in existing:
            ex_vessels = sorted(ex.vessel_ids_json or [])
            if ex_vessels == vessel_ids_sorted:
                already_exists = True
                break

        if already_exists:
            continue

        alert = FleetAlert(
            owner_cluster_id=None,
            alert_type="sts_relay_chain",
            vessel_ids_json=vessel_ids_sorted,
            evidence_json=evidence,
            risk_score_component=score,
        )
        db.add(alert)
        stats["alerts_created"] += 1
        stats["vessels_flagged"] += len(chain)

    db.commit()
    logger.info(
        "STS chain detection complete: %d chains, %d alerts",
        stats["chains_found"],
        stats["alerts_created"],
    )
    return stats


def _build_chain(events: list, component: set[int]) -> list[int]:
    """Build a chronological relay chain from sorted STS events.

    Walks events in time order, extending the chain when a new vessel
    appears connected to the current chain endpoint.

    Returns list of vessel IDs in chain order.
    """
    if not events:
        return []

    # Start chain with the first event
    first = events[0]
    chain = [first.vessel_1_id, first.vessel_2_id]
    used_events = {0}

    # Extend chain by looking for subsequent events
    changed = True
    while changed:
        changed = False
        for i, ev in enumerate(events):
            if i in used_events:
                continue
            # Can extend from the end of the chain
            if ev.vessel_1_id == chain[-1] and ev.vessel_2_id not in chain:
                chain.append(ev.vessel_2_id)
                used_events.add(i)
                changed = True
            elif ev.vessel_2_id == chain[-1] and ev.vessel_1_id not in chain:
                chain.append(ev.vessel_1_id)
                used_events.add(i)
                changed = True
            # Can extend from the start of the chain
            elif ev.vessel_2_id == chain[0] and ev.vessel_1_id not in chain:
                chain.insert(0, ev.vessel_1_id)
                used_events.add(i)
                changed = True
            elif ev.vessel_1_id == chain[0] and ev.vessel_2_id not in chain:
                chain.insert(0, ev.vessel_2_id)
                used_events.add(i)
                changed = True

    return chain
