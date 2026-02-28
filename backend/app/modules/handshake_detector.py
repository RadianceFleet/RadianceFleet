"""AIS handshake (identity swap) detector.

Detects the most sophisticated shadow fleet technique: two vessels meet at sea,
exchange identities (name/callsign/vessel_type), and sail away under each other's
identity. This is done to defeat vessel tracking.

Detection algorithm:
1. Find pairs of vessels within 1nm of each other
2. Check VesselHistory for identity attribute swaps within 1h after proximity
3. If vessel A's old attributes match vessel B's new attributes, flag as handshake
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.base import SpoofingTypeEnum
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

_PROXIMITY_NM = 1.0
_SWAP_WINDOW_HOURS = 1


def detect_handshakes(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect AIS identity swaps (handshakes) between vessel pairs.

    Returns:
        {"handshakes_detected": N, "pairs_checked": M}
    """
    # 1. Find proximity events -- vessels within 1nm
    # Get latest points per vessel within date range
    query = db.query(AISPoint).order_by(AISPoint.timestamp_utc)
    if date_from:
        query = query.filter(
            AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        query = query.filter(
            AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time())
        )

    points = query.all()
    if not points:
        return {"handshakes_detected": 0, "pairs_checked": 0}

    # Index points by 1-degree grid cell and time bucket (1h)
    grid: dict[tuple[int, int, int], list[AISPoint]] = defaultdict(list)
    for pt in points:
        cell_lat = int(math.floor(pt.lat))
        cell_lon = int(math.floor(pt.lon))
        hour_bucket = int(pt.timestamp_utc.timestamp() // 3600)
        grid[(cell_lat, cell_lon, hour_bucket)].append(pt)

    # Find close pairs
    proximity_pairs: list[tuple[int, int, datetime]] = []  # (vessel_id_a, vessel_id_b, time)
    seen_pairs: set[tuple[int, int]] = set()

    for cell_key, cell_points in grid.items():
        if len(cell_points) < 2:
            continue
        for i in range(len(cell_points)):
            for j in range(i + 1, len(cell_points)):
                pa, pb = cell_points[i], cell_points[j]
                if pa.vessel_id == pb.vessel_id:
                    continue

                pair = (min(pa.vessel_id, pb.vessel_id), max(pa.vessel_id, pb.vessel_id))
                if pair in seen_pairs:
                    continue

                dist = haversine_nm(pa.lat, pa.lon, pb.lat, pb.lon)
                if dist <= _PROXIMITY_NM:
                    seen_pairs.add(pair)
                    meet_time = max(pa.timestamp_utc, pb.timestamp_utc)
                    proximity_pairs.append((pair[0], pair[1], meet_time))

    # 2. Check for identity swaps after proximity
    handshakes = 0
    swap_window = timedelta(hours=_SWAP_WINDOW_HOURS)

    for vid_a, vid_b, meet_time in proximity_pairs:
        # Get identity changes for both vessels within swap window after meeting
        changes_a = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vid_a,
            VesselHistory.field_changed.in_(["name", "callsign", "vessel_type"]),
            VesselHistory.observed_at >= meet_time,
            VesselHistory.observed_at <= meet_time + swap_window,
        ).all()

        changes_b = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vid_b,
            VesselHistory.field_changed.in_(["name", "callsign", "vessel_type"]),
            VesselHistory.observed_at >= meet_time,
            VesselHistory.observed_at <= meet_time + swap_window,
        ).all()

        if not changes_a or not changes_b:
            continue

        # Check for cross-swap: A's old value == B's new value
        is_swap = False
        for ca in changes_a:
            for cb in changes_b:
                if ca.field_changed != cb.field_changed:
                    continue
                # A's old == B's new AND B's old == A's new
                if (ca.old_value and cb.new_value and
                    ca.old_value.strip().upper() == cb.new_value.strip().upper()):
                    is_swap = True
                    break
            if is_swap:
                break

        if not is_swap:
            continue

        # Check if already flagged
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IDENTITY_SWAP,
            SpoofingAnomaly.start_time_utc == meet_time,
        ).first()
        if existing:
            continue

        # Create anomaly for both vessels
        for vid in [vid_a, vid_b]:
            anomaly = SpoofingAnomaly(
                vessel_id=vid,
                anomaly_type=SpoofingTypeEnum.IDENTITY_SWAP,
                start_time_utc=meet_time,
                end_time_utc=meet_time + swap_window,
                risk_score_component=50,
                evidence_json={
                    "description": (
                        f"Identity swap detected: vessels {vid_a} and {vid_b} "
                        f"exchanged attributes after proximity"
                    ),
                    "vessel_a_id": vid_a,
                    "vessel_b_id": vid_b,
                },
            )
            db.add(anomaly)
        handshakes += 1

    db.commit()
    logger.info("Handshake detection: %d swaps from %d pairs", handshakes, len(proximity_pairs))
    return {"handshakes_detected": handshakes, "pairs_checked": len(proximity_pairs)}
