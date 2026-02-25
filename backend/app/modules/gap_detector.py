"""AIS gap detection engine.

Implements the core gap detection algorithm from PRD §7.4.
Gap is defined as a time delta between consecutive AIS points exceeding GAP_MIN_HOURS.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)


# (min_dwt, max_dwt or None): (max_kn, spoof_threshold_kn)
CLASS_SPEEDS: list[tuple[tuple[float, float | None], tuple[float, float]]] = [
    ((200_000, None),    (18, 22)),   # VLCC
    ((120_000, 200_000), (19, 23)),   # Suezmax
    ((80_000, 120_000),  (20, 24)),   # Aframax
    ((60_000, 80_000),   (20, 24)),   # Panamax
    ((0, 60_000),        (17, 22)),   # Default / small
]


def _class_speed(dwt: float | None) -> tuple[float, float]:
    """Return (max_speed_kn, spoof_threshold_kn) for given DWT."""
    if dwt is None:
        return 17.0, 22.0
    for (min_dw, max_dw), speeds in CLASS_SPEEDS:
        if max_dw is None and dwt >= min_dw:
            return speeds
        if max_dw is not None and min_dw <= dwt < max_dw:
            return speeds
    return 17.0, 22.0


def compute_max_distance_nm(vessel_dwt: float | None, elapsed_hours: float) -> float:
    """Maximum plausible drift distance for a vessel class over elapsed time.

    Reused by: gap detection, vessel hunt drift ellipse calculation.
    """
    max_speed_kn, _ = _class_speed(vessel_dwt)
    return max_speed_kn * elapsed_hours


def _is_near_port(db: Session, lat: float, lon: float, radius_deg: float = 0.1) -> bool:
    """Check if a position is within ~6nm of any known major port."""
    try:
        from app.models.port import Port
        from sqlalchemy import func
        count = db.query(Port).filter(
            Port.major_port == True,
            func.abs(func.ST_Y(Port.geometry) - lat) < radius_deg,
            func.abs(func.ST_X(Port.geometry) - lon) < radius_deg,
        ).first()
        return count is not None
    except Exception:
        return False


def _is_in_anchorage_corridor(db: Session, lat: float, lon: float, tolerance: float = 0.05) -> bool:
    """Check if a position falls within any anchorage_holding corridor bbox.

    Designated waiting anchorages (e.g. Laconian Gulf STS anchorage) are modeled
    as CorridorTypeEnum.ANCHORAGE_HOLDING corridors, not as Port records.  A vessel
    with nav_status=1 for 72h in such a corridor should NOT fire ANCHOR_SPOOF.
    """
    try:
        import re
        from app.models.corridor import Corridor
        corridors = db.query(Corridor).all()
        for c in corridors:
            ct = str(c.corridor_type.value if hasattr(c.corridor_type, "value") else c.corridor_type)
            if ct != "anchorage_holding":
                continue
            if c.geometry is None:
                continue
            raw = str(c.geometry)
            pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", raw)
            if not pairs:
                continue
            lons_c = [float(p[0]) for p in pairs]
            lats_c = [float(p[1]) for p in pairs]
            min_lon, max_lon = min(lons_c) - tolerance, max(lons_c) + tolerance
            min_lat, max_lat = min(lats_c) - tolerance, max(lats_c) + tolerance
            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                return True
        return False
    except Exception:
        return False


def run_gap_detection(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """
    Run gap detection across all vessels in the specified date range.

    Returns a summary dict with count of gaps detected.
    """
    vessels = db.query(Vessel).all()
    total_gaps = 0

    for vessel in vessels:
        gaps = detect_gaps_for_vessel(db, vessel, date_from=date_from, date_to=date_to)
        total_gaps += gaps

    logger.info("Gap detection complete: %d gaps found across %d vessels", total_gaps, len(vessels))
    return {"gaps_detected": total_gaps, "vessels_processed": len(vessels)}


def detect_gaps_for_vessel(
    db: Session,
    vessel: Vessel,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> int:
    """Detect AIS gaps for a single vessel. Returns count of new gaps created."""
    query = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel.vessel_id)
        .order_by(AISPoint.timestamp_utc)
    )
    if date_from:
        query = query.filter(AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time()))

    points = query.all()
    if len(points) < 2:
        return 0

    gap_count = 0
    min_gap_seconds = settings.GAP_MIN_HOURS * 3600

    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        delta_seconds = (p2.timestamp_utc - p1.timestamp_utc).total_seconds()

        # Class B noise filter — skip artifact-level intervals
        if delta_seconds < 180:
            continue

        if delta_seconds < min_gap_seconds:
            continue

        # Check if gap already recorded
        existing = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.vessel_id == vessel.vessel_id,
                AISGapEvent.gap_start_utc == p1.timestamp_utc,
                AISGapEvent.gap_end_utc == p2.timestamp_utc,
            )
            .first()
        )
        if existing:
            continue

        duration_minutes = int(delta_seconds / 60)
        actual_distance = _haversine_nm(p1.lat, p1.lon, p2.lat, p2.lon)
        duration_h = delta_seconds / 3600
        max_distance = compute_max_distance_nm(vessel.deadweight, duration_h)
        ratio = actual_distance / max_distance if max_distance > 0 else 0.0

        gap = AISGapEvent(
            vessel_id=vessel.vessel_id,
            start_point_id=p1.ais_point_id,
            end_point_id=p2.ais_point_id,
            gap_start_utc=p1.timestamp_utc,
            gap_end_utc=p2.timestamp_utc,
            duration_minutes=duration_minutes,
            risk_score=0,  # scoring runs separately
            status="new",
            impossible_speed_flag=(ratio > 1.1),  # was 1.0, now 1.1 for timestamp tolerance
            velocity_plausibility_ratio=ratio,
            max_plausible_distance_nm=max_distance,
            actual_gap_distance_nm=actual_distance,
            pre_gap_sog=p1.sog,  # captured at detection time for deterministic scoring
        )
        db.add(gap)
        db.flush()  # get gap_event_id
        try:
            from app.modules.corridor_correlator import find_corridor_for_gap, find_dark_zone_for_gap
            corridor = find_corridor_for_gap(db, gap)
            if corridor:
                gap.corridor_id = corridor.corridor_id
                if corridor.is_jamming_zone:
                    gap.in_dark_zone = True
            dark_zone = find_dark_zone_for_gap(db, gap)
            if dark_zone:
                gap.dark_zone_id = dark_zone.zone_id
                gap.in_dark_zone = True
        except Exception as e:
            logger.debug("Corridor correlation skipped: %s", e)

        _create_movement_envelope(db, gap, vessel)
        gap_count += 1

    db.commit()
    return gap_count


def _create_movement_envelope(db: Session, gap: AISGapEvent, vessel: Vessel) -> None:
    """Create rotated ellipse envelope centered at gap_start point."""
    import math
    from app.models.movement_envelope import MovementEnvelope
    from app.models.base import EstimatedMethodEnum

    duration_h = gap.duration_minutes / 60
    max_dist_nm = compute_max_distance_nm(vessel.deadweight if vessel else None, duration_h)

    semi_major = 0.7 * max_dist_nm
    semi_minor = 0.3 * max_dist_nm

    # Determine heading from start point
    heading = None
    if gap.start_point_id:
        start_pt = db.get(AISPoint, gap.start_point_id)
        if start_pt:
            heading = start_pt.cog or start_pt.heading

    # Estimate method based on duration
    if duration_h <= 2:
        method = EstimatedMethodEnum.LINEAR
    elif duration_h <= 6:
        method = EstimatedMethodEnum.SPLINE
    else:
        method = EstimatedMethodEnum.KALMAN

    envelope = MovementEnvelope(
        gap_event_id=gap.gap_event_id,
        max_plausible_distance_nm=max_dist_nm,
        actual_gap_distance_nm=gap.actual_gap_distance_nm,
        velocity_plausibility_ratio=gap.velocity_plausibility_ratio,
        envelope_semi_major_nm=semi_major,
        envelope_semi_minor_nm=semi_minor,
        envelope_heading_degrees=heading,
        estimated_method=method,
    )
    db.add(envelope)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in nautical miles (Haversine formula)."""
    import math
    R_nm = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def run_spoofing_detection(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """
    Detect AIS spoofing anomalies.

    Typologies:
    - anchor_spoof: nav_status=1 for >=72h, SOG<0.1, NOT near major port
    - circle_spoof: SOG>3kn but positions cluster tightly (std_dev<0.05 deg)
    - slow_roll: 0.5<=SOG<=2.0 for >=12h, tanker type
    - mmsi_reuse: implied speed >30kn between consecutive points
    - nav_status_mismatch: nav_status=1 AND SOG>2kn
    """
    from app.models.vessel import Vessel
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.base import SpoofingTypeEnum
    from app.models.port import Port

    vessels = db.query(Vessel).all()
    anomalies_created = 0

    for vessel in vessels:
        q = db.query(AISPoint).filter(AISPoint.vessel_id == vessel.vessel_id).order_by(AISPoint.timestamp_utc)
        if date_from:
            q = q.filter(AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            q = q.filter(AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time()))
        points = q.all()
        if len(points) < 2:
            continue

        # --- Type 4: MMSI Reuse (implied speed) ---
        # Check between consecutive points for impossible implied speeds
        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            dt_h = (p2.timestamp_utc - p1.timestamp_utc).total_seconds() / 3600
            if dt_h <= 0:
                continue
            dist_nm = _haversine_nm(p1.lat, p1.lon, p2.lat, p2.lon)
            implied_speed = dist_nm / dt_h
            if implied_speed > 30:
                # Check dedup
                existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.MMSI_REUSE,
                    SpoofingAnomaly.start_time_utc == p1.timestamp_utc,
                ).first()
                if existing:
                    continue
                score = 55 if implied_speed > 100 else 40
                db.add(SpoofingAnomaly(
                    vessel_id=vessel.vessel_id,
                    anomaly_type=SpoofingTypeEnum.MMSI_REUSE,
                    start_time_utc=p1.timestamp_utc,
                    end_time_utc=p2.timestamp_utc,
                    implied_speed_kn=implied_speed,
                    risk_score_component=score,
                    evidence_json={"implied_speed_kn": implied_speed, "dist_nm": dist_nm},
                ))
                anomalies_created += 1

        # --- Type 5: Nav Status Mismatch ---
        for p in points:
            if p.nav_status == 1 and p.sog is not None and p.sog > 2.0:
                existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.NAV_STATUS_MISMATCH,
                    SpoofingAnomaly.start_time_utc == p.timestamp_utc,
                ).first()
                if not existing:
                    db.add(SpoofingAnomaly(
                        vessel_id=vessel.vessel_id,
                        anomaly_type=SpoofingTypeEnum.NAV_STATUS_MISMATCH,
                        start_time_utc=p.timestamp_utc,
                        end_time_utc=p.timestamp_utc,
                        risk_score_component=15,
                        evidence_json={"nav_status": p.nav_status, "sog": p.sog},
                    ))
                    anomalies_created += 1

        # --- Type 1: Anchor Spoof ---
        # Find runs where nav_status=1 for >=72h AND SOG<0.1 AND NOT near any port
        anchor_run = []
        for p in points:
            if p.nav_status == 1 and (p.sog is None or p.sog < 0.1):
                anchor_run.append(p)
            else:
                if len(anchor_run) >= 2:
                    run_hours = (anchor_run[-1].timestamp_utc - anchor_run[0].timestamp_utc).total_seconds() / 3600
                    if run_hours >= 72:
                        mean_lat = sum(pt.lat for pt in anchor_run) / len(anchor_run)
                        mean_lon = sum(pt.lon for pt in anchor_run) / len(anchor_run)
                        near_port = _is_near_port(db, mean_lat, mean_lon)
                        in_anchorage = _is_in_anchorage_corridor(db, mean_lat, mean_lon)
                        if not near_port and not in_anchorage:
                            existing = db.query(SpoofingAnomaly).filter(
                                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ANCHOR_SPOOF,
                                SpoofingAnomaly.start_time_utc == anchor_run[0].timestamp_utc,
                            ).first()
                            if not existing:
                                db.add(SpoofingAnomaly(
                                    vessel_id=vessel.vessel_id,
                                    anomaly_type=SpoofingTypeEnum.ANCHOR_SPOOF,
                                    start_time_utc=anchor_run[0].timestamp_utc,
                                    end_time_utc=anchor_run[-1].timestamp_utc,
                                    risk_score_component=10,
                                    evidence_json={"run_hours": run_hours, "mean_lat": mean_lat, "mean_lon": mean_lon},
                                ))
                                anomalies_created += 1
                anchor_run = []

        # --- Type 2: Circle Spoof ---
        # 6h rolling windows, SOG>3kn but tight cluster
        window_size = 6  # 6 points minimum in 6h window (roughly)
        if len(points) >= window_size:
            for i in range(len(points) - window_size + 1):
                window = points[i:i + window_size]
                window_hours = (window[-1].timestamp_utc - window[0].timestamp_utc).total_seconds() / 3600
                if window_hours < 4 or window_hours > 8:
                    continue
                sogs = [p.sog for p in window if p.sog is not None]
                if not sogs or statistics.median(sogs) <= 3.0:
                    continue
                lats = [p.lat for p in window]
                lons = [p.lon for p in window]
                if len(lats) < 2:
                    continue
                std_lat = statistics.stdev(lats)
                std_lon = statistics.stdev(lons)
                # Correct for latitude
                import math
                mean_lat = statistics.mean(lats)
                std_lon_corrected = std_lon * math.cos(math.radians(mean_lat))
                if std_lat < 0.05 and std_lon_corrected < 0.05:
                    if not _is_near_port(db, mean_lat, statistics.mean(lons)):
                        existing = db.query(SpoofingAnomaly).filter(
                            SpoofingAnomaly.vessel_id == vessel.vessel_id,
                            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.CIRCLE_SPOOF,
                            SpoofingAnomaly.start_time_utc == window[0].timestamp_utc,
                        ).first()
                        if not existing:
                            db.add(SpoofingAnomaly(
                                vessel_id=vessel.vessel_id,
                                anomaly_type=SpoofingTypeEnum.CIRCLE_SPOOF,
                                start_time_utc=window[0].timestamp_utc,
                                end_time_utc=window[-1].timestamp_utc,
                                risk_score_component=35,
                                evidence_json={"std_lat": std_lat, "std_lon_corrected": std_lon_corrected, "median_sog": statistics.median(sogs)},
                            ))
                            anomalies_created += 1

        # --- Type 6: Erratic Nav Status ---
        # Three sub-detectors (all use SpoofingTypeEnum.ERRATIC_NAV_STATUS):
        #  6a. 3+ nav_status changes within 60 min → episode score=12
        #  6b. nav_status=3 continuously for >6h on a tanker → score=8 (subtype: extended_restricted)
        #  6c. nav_status=15 on a tanker → score=5 (subtype: nav_status_15)

        # 6a: Non-overlapping 60-minute window scan
        _NAV_WINDOW_S = 3600
        i = 0
        while i < len(points) - 1:
            window_end = points[i].timestamp_utc + timedelta(seconds=_NAV_WINDOW_S)
            window = [p for p in points[i:] if p.timestamp_utc <= window_end]
            if len(window) >= 2:
                status_values = [p.nav_status for p in window if p.nav_status is not None]
                changes = sum(1 for a, b in zip(status_values, status_values[1:]) if a != b)
                if changes >= 3:
                    existing_erratic = db.query(SpoofingAnomaly).filter(
                        SpoofingAnomaly.vessel_id == vessel.vessel_id,
                        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                        SpoofingAnomaly.start_time_utc == points[i].timestamp_utc,
                    ).first()
                    if not existing_erratic:
                        db.add(SpoofingAnomaly(
                            vessel_id=vessel.vessel_id,
                            anomaly_type=SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                            start_time_utc=points[i].timestamp_utc,
                            end_time_utc=window[-1].timestamp_utc,
                            risk_score_component=12,
                            evidence_json={
                                "subtype": "erratic_changes",
                                "status_changes": changes,
                                "window_minutes": 60,
                            },
                        ))
                        anomalies_created += 1
                    # Advance past the ENTIRE continuous episode (all consecutive matching windows)
                    # so that one continuous oscillation produces exactly one anomaly.
                    episode_end_idx = max(
                        idx for idx, p in enumerate(points) if p.timestamp_utc <= window_end
                    )
                    while episode_end_idx + 1 < len(points) - 1:
                        next_i = episode_end_idx + 1
                        next_we = points[next_i].timestamp_utc + timedelta(seconds=_NAV_WINDOW_S)
                        next_win = [p for p in points[next_i:] if p.timestamp_utc <= next_we]
                        if len(next_win) >= 2:
                            next_sv = [p.nav_status for p in next_win if p.nav_status is not None]
                            next_ch = sum(1 for a, b in zip(next_sv, next_sv[1:]) if a != b)
                            if next_ch >= 3:
                                episode_end_idx = max(
                                    idx for idx, p in enumerate(points) if p.timestamp_utc <= next_we
                                )
                                continue
                        break
                    i = episode_end_idx + 1
                    continue
            i += 1

        # 6b + 6c: tanker-specific sub-types
        is_tanker_erratic = (
            vessel.vessel_type and "tanker" in vessel.vessel_type.lower()
        ) or (isinstance(vessel.deadweight, (int, float)) and vessel.deadweight >= 20_000)

        if is_tanker_erratic:
            # 6b: extended restricted maneuverability (nav_status=3 > 6h)
            restricted_run: list = []
            for p in points:
                if p.nav_status == 3:
                    restricted_run.append(p)
                else:
                    if len(restricted_run) >= 2:
                        run_hours = (
                            restricted_run[-1].timestamp_utc - restricted_run[0].timestamp_utc
                        ).total_seconds() / 3600
                        if run_hours >= 6:
                            existing = db.query(SpoofingAnomaly).filter(
                                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                                SpoofingAnomaly.start_time_utc == restricted_run[0].timestamp_utc,
                            ).first()
                            if not existing:
                                db.add(SpoofingAnomaly(
                                    vessel_id=vessel.vessel_id,
                                    anomaly_type=SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                                    start_time_utc=restricted_run[0].timestamp_utc,
                                    end_time_utc=restricted_run[-1].timestamp_utc,
                                    risk_score_component=8,
                                    evidence_json={
                                        "subtype": "extended_restricted",
                                        "hours": round(run_hours, 1),
                                    },
                                ))
                                anomalies_created += 1
                    restricted_run = []

            # 6c: nav_status=15 on tanker
            for p in points:
                if p.nav_status == 15:
                    existing = db.query(SpoofingAnomaly).filter(
                        SpoofingAnomaly.vessel_id == vessel.vessel_id,
                        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                        SpoofingAnomaly.start_time_utc == p.timestamp_utc,
                    ).first()
                    if not existing:
                        db.add(SpoofingAnomaly(
                            vessel_id=vessel.vessel_id,
                            anomaly_type=SpoofingTypeEnum.ERRATIC_NAV_STATUS,
                            start_time_utc=p.timestamp_utc,
                            end_time_utc=p.timestamp_utc,
                            risk_score_component=5,
                            evidence_json={"subtype": "nav_status_15"},
                        ))
                        anomalies_created += 1

        # --- Type 3: Slow Roll ---
        is_tanker = (
            vessel.vessel_type and "tanker" in vessel.vessel_type.lower()
        ) or (vessel.deadweight and vessel.deadweight >= 20_000)
        if is_tanker:
            slow_run = []
            for p in points:
                if p.sog is not None and 0.5 <= p.sog <= 2.0:
                    slow_run.append(p)
                else:
                    if len(slow_run) >= 2:
                        run_hours = (slow_run[-1].timestamp_utc - slow_run[0].timestamp_utc).total_seconds() / 3600
                        if run_hours >= 12:
                            if not _is_near_port(db, statistics.mean(p.lat for p in slow_run), statistics.mean(p.lon for p in slow_run)):
                                existing = db.query(SpoofingAnomaly).filter(
                                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SLOW_ROLL,
                                    SpoofingAnomaly.start_time_utc == slow_run[0].timestamp_utc,
                                ).first()
                                if not existing:
                                    db.add(SpoofingAnomaly(
                                        vessel_id=vessel.vessel_id,
                                        anomaly_type=SpoofingTypeEnum.SLOW_ROLL,
                                        start_time_utc=slow_run[0].timestamp_utc,
                                        end_time_utc=slow_run[-1].timestamp_utc,
                                        risk_score_component=12,
                                        evidence_json={"run_hours": run_hours},
                                    ))
                                    anomalies_created += 1
                    slow_run = []

    db.commit()
    logger.info("Spoofing detection complete: %d anomalies detected", anomalies_created)
    return {"anomalies_detected": anomalies_created}
