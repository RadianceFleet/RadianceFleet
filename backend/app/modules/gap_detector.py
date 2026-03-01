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


from app.utils.vessel import classify_vessel_speed

# Legacy alias — kept for backward compat; delegates to shared util
CLASS_SPEEDS: list[tuple[tuple[float, float | None], tuple[float, float]]] = [
    ((200_000, None),    (18, 22)),   # VLCC
    ((120_000, 200_000), (19, 23)),   # Suezmax
    ((80_000, 120_000),  (20, 24)),   # Aframax
    ((60_000, 80_000),   (20, 24)),   # Panamax
    ((0, 60_000),        (17, 22)),   # Default / small
]


def _class_speed(dwt: float | None) -> tuple[float, float]:
    """Return (max_speed_kn, spoof_threshold_kn) for given DWT."""
    return classify_vessel_speed(dwt)


def compute_max_distance_nm(vessel_dwt: float | None, elapsed_hours: float) -> float:
    """Maximum plausible drift distance for a vessel class over elapsed time.

    Reused by: gap detection, vessel hunt drift ellipse calculation.
    """
    max_speed_kn, _ = _class_speed(vessel_dwt)
    return max_speed_kn * elapsed_hours


def _is_near_port(db: Session, lat: float, lon: float, radius_nm: float = 5.0) -> bool:
    """Check if a position is within radius_nm of any known major port.

    Uses haversine distance against port coordinates parsed from WKT geometry.
    """
    from app.models.port import Port
    from app.utils.geo import haversine_nm, load_geometry

    ports = db.query(Port).filter(Port.major_port == True).all()
    for port in ports:
        pt = load_geometry(port.geometry)
        if pt is None:
            continue
        if haversine_nm(lat, lon, pt.y, pt.x) <= radius_nm:
            return True
    return False


def _is_in_anchorage_corridor(db: Session, lat: float, lon: float, tolerance: float = 0.05) -> bool:
    """Check if a position falls within any anchorage_holding corridor.

    Designated waiting anchorages (e.g. Laconian Gulf STS anchorage) are modeled
    as CorridorTypeEnum.ANCHORAGE_HOLDING corridors, not as Port records.  A vessel
    with nav_status=1 for 72h in such a corridor should NOT fire ANCHOR_SPOOF.
    """
    from app.models.corridor import Corridor
    from app.models.base import CorridorTypeEnum
    from app.modules.corridor_correlator import _parse_wkt_bbox, _geometry_wkt

    corridors = db.query(Corridor).filter(
        Corridor.corridor_type == CorridorTypeEnum.ANCHORAGE_HOLDING,
        Corridor.geometry.isnot(None),
    ).all()
    for c in corridors:
        wkt = _geometry_wkt(c.geometry)
        bbox = _parse_wkt_bbox(wkt) if wkt else None
        if bbox and (bbox[0] - tolerance <= lon <= bbox[2] + tolerance
                     and bbox[1] - tolerance <= lat <= bbox[3] + tolerance):
            return True
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
    from app.models.corridor import Corridor
    corridor_count = db.query(Corridor).count()
    if corridor_count == 0:
        logger.warning(
            "No corridors loaded! Run 'radiancefleet corridors import'. "
            "All gaps will miss corridor multipliers."
        )

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
        if delta_seconds < settings.CLASS_B_NOISE_FILTER_SECONDS:
            continue

        if delta_seconds < min_gap_seconds:
            continue

        # Check if gap already recorded (±10 min window to dedup with GFW imports)
        _dedup_window = timedelta(minutes=10)
        existing = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.vessel_id == vessel.vessel_id,
                AISGapEvent.gap_start_utc >= p1.timestamp_utc - _dedup_window,
                AISGapEvent.gap_start_utc <= p1.timestamp_utc + _dedup_window,
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
            original_vessel_id=vessel.vessel_id,  # forward provenance for scoring
            start_point_id=p1.ais_point_id,
            end_point_id=p2.ais_point_id,
            gap_start_utc=p1.timestamp_utc,
            gap_end_utc=p2.timestamp_utc,
            duration_minutes=duration_minutes,
            risk_score=0,  # scoring runs separately
            status="new",
            # Threshold is 1.1 (not PRD's 1.0) to tolerate minor GPS/timestamp rounding
            # errors: AIS timestamps have 1-second resolution, and great-circle vs. actual
            # sailing path differences can produce ratios up to ~1.05 for legitimate voyages.
            # A 10% buffer prevents false positives on clean vessels while still catching
            # physically impossible reappearances (ratio >> 1.1).
            impossible_speed_flag=(ratio > 1.1),
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
        except ImportError:
            logger.warning("Corridor correlator module not available — skipping")

        _create_movement_envelope(db, gap, vessel)
        gap_count += 1

    db.commit()
    return gap_count


def _create_movement_envelope(db: Session, gap: AISGapEvent, vessel: Vessel) -> None:
    """Create movement envelope with real interpolation methods (PRD §7.4).

    <2h:  Linear interpolation (2-point track)
    2-6h: Cubic Hermite spline using start/end COG+SOG
    >6h:  Multi-scenario envelopes (min/max speed bounds)
    """
    from app.models.movement_envelope import MovementEnvelope
    from app.models.base import EstimatedMethodEnum
    from app.utils.interpolation import interpolate_linear, interpolate_hermite, interpolate_scenarios

    duration_h = gap.duration_minutes / 60
    max_dist_nm = compute_max_distance_nm(vessel.deadweight if vessel else None, duration_h)

    semi_major = 0.7 * max_dist_nm
    semi_minor = 0.3 * max_dist_nm

    # Fetch start and end points for interpolation
    start_pt = db.get(AISPoint, gap.start_point_id) if gap.start_point_id else None
    end_pt = db.get(AISPoint, gap.end_point_id) if gap.end_point_id else None
    heading = (start_pt.cog or start_pt.heading) if start_pt else None

    # Interpolation based on duration
    positions_json = None
    ellipse_wkt = None

    if start_pt and end_pt:
        if duration_h <= 2:
            method = EstimatedMethodEnum.LINEAR
            positions_json, ellipse_wkt = interpolate_linear(
                start_pt.lat, start_pt.lon, end_pt.lat, end_pt.lon, duration_h
            )
        elif duration_h <= 6:
            method = EstimatedMethodEnum.SPLINE
            positions_json, ellipse_wkt = interpolate_hermite(
                start_pt.lat, start_pt.lon, end_pt.lat, end_pt.lon,
                start_sog=start_pt.sog or 0, start_cog=start_pt.cog or 0,
                end_sog=end_pt.sog or 0, end_cog=end_pt.cog or 0,
                duration_h=duration_h,
            )
        else:
            method = EstimatedMethodEnum.KALMAN
            max_speed_kn = _class_speed(vessel.deadweight if vessel else None)[0]
            positions_json, ellipse_wkt = interpolate_scenarios(
                start_pt.lat, start_pt.lon, end_pt.lat, end_pt.lon,
                start_sog=start_pt.sog or 0, start_cog=start_pt.cog or 0,
                max_speed_kn=max_speed_kn, duration_h=duration_h,
            )
    else:
        method = EstimatedMethodEnum.LINEAR

    envelope = MovementEnvelope(
        gap_event_id=gap.gap_event_id,
        max_plausible_distance_nm=max_dist_nm,
        actual_gap_distance_nm=gap.actual_gap_distance_nm,
        velocity_plausibility_ratio=gap.velocity_plausibility_ratio,
        envelope_semi_major_nm=semi_major,
        envelope_semi_minor_nm=semi_minor,
        envelope_heading_degrees=heading,
        estimated_method=method,
        interpolated_positions_json=positions_json,
        confidence_ellipse_geometry=ellipse_wkt,
    )
    db.add(envelope)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in nautical miles (Haversine formula).

    Thin wrapper around app.utils.geo.haversine_nm for backward compatibility.
    """
    from app.utils.geo import haversine_nm
    return haversine_nm(lat1, lon1, lat2, lon2)


def detect_stale_ais_data(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect repeating AIS data values (stale transponder data).

    Identifies sequences where heading, SOG, and COG are all identical across
    >N consecutive messages spanning >2 hours while vessel SOG > 0.5 (underway).

    A stale transponder broadcasting frozen values while the vessel is moving
    is a strong indicator of AIS manipulation or hardware tampering.

    Gated by STALE_AIS_DETECTION_ENABLED feature flag.

    Returns dict with count of anomalies detected.
    """
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.base import SpoofingTypeEnum

    if not settings.STALE_AIS_DETECTION_ENABLED:
        return {"stale_ais_anomalies": 0, "skipped": True}

    vessels = db.query(Vessel).all()
    anomalies_created = 0
    _MIN_CONSECUTIVE = 10
    _MIN_SPAN_HOURS = 2.0

    for vessel in vessels:
        q = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc)
        )
        if date_from:
            q = q.filter(AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            q = q.filter(AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time()))

        points = q.all()
        if len(points) < _MIN_CONSECUTIVE:
            continue

        # Scan for stale sequences
        run_start = 0
        for i in range(1, len(points)):
            p_prev = points[i - 1]
            p_curr = points[i]

            # Check if heading, SOG, and COG are all identical and vessel underway
            same_heading = (p_prev.heading is not None and p_curr.heading is not None
                           and p_prev.heading == p_curr.heading)
            same_sog = (p_prev.sog is not None and p_curr.sog is not None
                        and p_prev.sog == p_curr.sog)
            same_cog = (p_prev.cog is not None and p_curr.cog is not None
                        and p_prev.cog == p_curr.cog)
            underway = p_curr.sog is not None and p_curr.sog > 0.5

            if same_heading and same_sog and same_cog and underway:
                continue  # still in a stale run
            else:
                # Run ended; check if it meets thresholds
                run_length = i - run_start
                if run_length >= _MIN_CONSECUTIVE:
                    span_hours = (
                        points[i - 1].timestamp_utc - points[run_start].timestamp_utc
                    ).total_seconds() / 3600
                    if span_hours >= _MIN_SPAN_HOURS:
                        # Check underway for first point in run
                        first_sog = points[run_start].sog
                        if first_sog is not None and first_sog > 0.5:
                            # Dedup
                            existing = db.query(SpoofingAnomaly).filter(
                                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STALE_AIS_DATA,
                                SpoofingAnomaly.start_time_utc == points[run_start].timestamp_utc,
                            ).first()
                            if not existing:
                                db.add(SpoofingAnomaly(
                                    vessel_id=vessel.vessel_id,
                                    anomaly_type=SpoofingTypeEnum.STALE_AIS_DATA,
                                    start_time_utc=points[run_start].timestamp_utc,
                                    end_time_utc=points[i - 1].timestamp_utc,
                                    risk_score_component=20,
                                    evidence_json={
                                        "consecutive_count": run_length,
                                        "span_hours": round(span_hours, 2),
                                        "frozen_sog": points[run_start].sog,
                                        "frozen_cog": points[run_start].cog,
                                        "frozen_heading": points[run_start].heading,
                                    },
                                ))
                                anomalies_created += 1
                run_start = i

        # Check final run
        run_length = len(points) - run_start
        if run_length >= _MIN_CONSECUTIVE:
            span_hours = (
                points[-1].timestamp_utc - points[run_start].timestamp_utc
            ).total_seconds() / 3600
            if span_hours >= _MIN_SPAN_HOURS:
                first_sog = points[run_start].sog
                if first_sog is not None and first_sog > 0.5:
                    existing = db.query(SpoofingAnomaly).filter(
                        SpoofingAnomaly.vessel_id == vessel.vessel_id,
                        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STALE_AIS_DATA,
                        SpoofingAnomaly.start_time_utc == points[run_start].timestamp_utc,
                    ).first()
                    if not existing:
                        db.add(SpoofingAnomaly(
                            vessel_id=vessel.vessel_id,
                            anomaly_type=SpoofingTypeEnum.STALE_AIS_DATA,
                            start_time_utc=points[run_start].timestamp_utc,
                            end_time_utc=points[-1].timestamp_utc,
                            risk_score_component=20,
                            evidence_json={
                                "consecutive_count": run_length,
                                "span_hours": round(span_hours, 2),
                                "frozen_sog": points[run_start].sog,
                                "frozen_cog": points[run_start].cog,
                                "frozen_heading": points[run_start].heading,
                            },
                        ))
                        anomalies_created += 1

    db.commit()
    logger.info("Stale AIS detection complete: %d anomalies detected", anomalies_created)
    return {"stale_ais_anomalies": anomalies_created}


def run_spoofing_detection(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """
    Detect AIS spoofing anomalies.

    Typologies:
    - anchor_spoof: nav_status=1 for >=72h, SOG<0.1, NOT near major port
    - circle_spoof: SOG>3kn but positions cluster tightly (std_dev<0.02 deg, ~2nm per PRD §7.4.5)
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

        # --- Type 7: Dual Transmission Candidate ---
        # Two positions from same MMSI with <30min delta but impossible speed (>30kn)
        # within a 1h sliding window. Indicates two physical transmitters.
        _DUAL_WINDOW_H = 1
        _DUAL_MAX_DELTA_MIN = 30
        for i in range(len(points)):
            anchor_time = points[i].timestamp_utc
            window_end = anchor_time + timedelta(hours=_DUAL_WINDOW_H)
            for j in range(i + 1, len(points)):
                if points[j].timestamp_utc > window_end:
                    break
                dt_s = (points[j].timestamp_utc - points[i].timestamp_utc).total_seconds()
                if dt_s <= 0 or dt_s > _DUAL_MAX_DELTA_MIN * 60:
                    continue
                dist_nm = _haversine_nm(points[i].lat, points[i].lon, points[j].lat, points[j].lon)
                implied_speed = dist_nm / (dt_s / 3600)
                if implied_speed > 30:
                    existing_dual = db.query(SpoofingAnomaly).filter(
                        SpoofingAnomaly.vessel_id == vessel.vessel_id,
                        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.DUAL_TRANSMISSION,
                        SpoofingAnomaly.start_time_utc == points[i].timestamp_utc,
                    ).first()
                    if not existing_dual:
                        db.add(SpoofingAnomaly(
                            vessel_id=vessel.vessel_id,
                            anomaly_type=SpoofingTypeEnum.DUAL_TRANSMISSION,
                            start_time_utc=points[i].timestamp_utc,
                            end_time_utc=points[j].timestamp_utc,
                            implied_speed_kn=implied_speed,
                            risk_score_component=30,
                            evidence_json={
                                "implied_speed_kn": round(implied_speed, 1),
                                "delta_minutes": round(dt_s / 60, 1),
                                "dist_nm": round(dist_nm, 1),
                            },
                        ))
                        anomalies_created += 1
                    break  # one detection per anchor point

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
                                    risk_score_component=20,
                                    evidence_json={"run_hours": run_hours, "mean_lat": mean_lat, "mean_lon": mean_lon},
                                ))
                                anomalies_created += 1
                anchor_run = []

        # --- Type 2: Circle Spoof ---
        # Time-based 6h sliding window anchored on each point's timestamp.
        # Collect all points within 6h forward of the anchor; require >= 6 points.
        # This handles irregular AIS intervals correctly (unlike fixed point-count windows).
        _CIRCLE_WINDOW_H = 6
        _CIRCLE_MIN_POINTS = 6
        if len(points) >= _CIRCLE_MIN_POINTS:
            for i in range(len(points)):
                anchor_time = points[i].timestamp_utc
                window_end_time = anchor_time + timedelta(hours=_CIRCLE_WINDOW_H)
                window = [p for p in points[i:] if p.timestamp_utc <= window_end_time]
                if len(window) < _CIRCLE_MIN_POINTS:
                    continue
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
                # Scale threshold by latitude to prevent false positives at high latitudes
                lat_scale = max(math.cos(math.radians(mean_lat)), 0.3)
                threshold = 0.02 / lat_scale  # Caps at ~0.067° at very high latitudes
                if std_lat < threshold and std_lon_corrected < threshold:
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
        from app.utils.vessel_filter import is_tanker_type
        is_tanker_erratic = is_tanker_type(vessel)

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
        is_tanker = is_tanker_type(vessel)
        if is_tanker:
            slow_run = []
            for p in points:
                if p.sog is not None and 0.5 <= p.sog <= 2.0:
                    slow_run.append(p)
                else:
                    if len(slow_run) >= 2:
                        run_hours = (slow_run[-1].timestamp_utc - slow_run[0].timestamp_utc).total_seconds() / 3600
                        if run_hours >= 12:
                            if not any(_is_near_port(db, p.lat, p.lon) for p in slow_run):
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

    # Post-processing: link unlinked SpoofingAnomaly records to their closest overlapping gap
    unlinked = db.query(SpoofingAnomaly).filter(
        SpoofingAnomaly.gap_event_id == None,
    ).all()
    linked_count = 0
    for anomaly in unlinked:
        # Find gap events for this vessel that overlap temporally with the anomaly
        matching_gap = db.query(AISGapEvent).filter(
            AISGapEvent.vessel_id == anomaly.vessel_id,
            AISGapEvent.gap_start_utc <= anomaly.end_time_utc + timedelta(hours=2),
            AISGapEvent.gap_end_utc >= anomaly.start_time_utc - timedelta(hours=2),
        ).order_by(
            # Prefer the gap whose start is closest to the anomaly start
            AISGapEvent.gap_start_utc
        ).first()
        if matching_gap:
            anomaly.gap_event_id = matching_gap.gap_event_id
            linked_count += 1
    if linked_count:
        db.commit()
        logger.info("Linked %d spoofing anomalies to gap events", linked_count)

    logger.info("Spoofing detection complete: %d anomalies detected", anomalies_created)
    return {"anomalies_detected": anomalies_created}
