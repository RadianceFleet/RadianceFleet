"""Dark vessel discovery — auto-hunt, clustering, and orchestration.

Provides:
  - auto_hunt_dark_vessels()   — automated vessel hunt for high-risk gaps
  - cluster_dark_detections()  — spatial+temporal clustering of unmatched SAR
  - discover_dark_vessels()    — full pipeline orchestrator
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def auto_hunt_dark_vessels(
    db: Session,
    min_gap_score: int = 50,
) -> dict:
    """Automatically create hunt missions for high-risk gap events.

    For each AISGapEvent with risk_score >= min_gap_score:
      1. Get position (start_point or gap_off_lat/lon)
      2. Create VesselTargetProfile with auto-populated position
      3. Create SearchMission (gap window + 6h buffer)
      4. find_hunt_candidates() — score dark detections in drift ellipse
      5. Link found candidates to the gap

    Returns dict with hunt statistics.
    """
    from app.models.gap_event import AISGapEvent
    from app.models.stubs import DarkVesselDetection
    from app.modules.vessel_hunt import (
        create_target_profile,
        create_search_mission,
        find_hunt_candidates,
    )
    from app.utils.geo import haversine_nm

    stats: dict[str, Any] = {
        "gaps_hunted": 0,
        "missions_created": 0,
        "candidates_found": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "sts_confirmed": 0,
        "errors": [],
    }

    gaps = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.risk_score >= min_gap_score)
        .order_by(AISGapEvent.risk_score.desc())
        .all()
    )

    for gap in gaps:
        # Determine position for the hunt
        hunt_lat = hunt_lon = None

        # Prefer GFW off-position (more accurate for external gaps)
        if getattr(gap, "gap_off_lat", None) is not None:
            hunt_lat, hunt_lon = gap.gap_off_lat, gap.gap_off_lon
        elif gap.start_point is not None:
            hunt_lat, hunt_lon = gap.start_point.lat, gap.start_point.lon

        if hunt_lat is None or hunt_lon is None:
            continue

        try:
            profile = create_target_profile(
                gap.vessel_id, db,
                last_lat=hunt_lat, last_lon=hunt_lon,
            )

            search_end = gap.gap_end_utc + timedelta(hours=6)
            mission = create_search_mission(
                profile.profile_id,
                gap.gap_start_utc,
                search_end,
                db,
            )
            stats["missions_created"] += 1

            candidates = find_hunt_candidates(mission.mission_id, db)
            stats["candidates_found"] += len(candidates)

            for c in candidates:
                band = (c.score_breakdown_json or {}).get("band", "LOW")
                if band == "HIGH":
                    stats["high"] += 1
                elif band == "MEDIUM":
                    stats["medium"] += 1
                else:
                    stats["low"] += 1

            stats["gaps_hunted"] += 1

        except Exception as exc:
            logger.warning("Auto-hunt failed for gap %d: %s", gap.gap_event_id, exc)
            stats["errors"].append(f"gap_{gap.gap_event_id}: {exc}")

    # STS dark confirmation: for each STS event, find dark detections within 1nm + ±2h
    try:
        from app.models.sts_transfer import StsTransferEvent
        sts_events = db.query(StsTransferEvent).all()
        for sts in sts_events:
            if sts.mean_lat is None or sts.mean_lon is None or sts.start_time_utc is None:
                continue
            dark_nearby = db.query(DarkVesselDetection).filter(
                DarkVesselDetection.ais_match_result == "unmatched",
                DarkVesselDetection.detection_time_utc.between(
                    sts.start_time_utc - timedelta(hours=2),
                    sts.end_time_utc + timedelta(hours=2),
                ),
            ).all()
            for det in dark_nearby:
                if det.detection_lat is None or det.detection_lon is None:
                    continue
                dist = haversine_nm(sts.mean_lat, sts.mean_lon, det.detection_lat, det.detection_lon)
                if dist <= 1.0:
                    stats["sts_confirmed"] += 1
                    break  # One confirmation per STS event
    except Exception as exc:
        logger.warning("STS dark confirmation failed: %s", exc)

    db.commit()
    logger.info("Auto-hunt complete: %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats


def cluster_dark_detections(
    db: Session,
    radius_nm: float = 5.0,
    min_detections: int = 3,
    days_window: int = 14,
) -> list[dict]:
    """Group co-located unmatched SAR detections into clusters.

    Greedy spatial+temporal clustering: pick the unmatched detection with the
    most neighbors, form a cluster, remove clustered detections, repeat.

    Scores: +20 STS corridor, +15/extra detection, +10 tanker type, +25 if >7 days persistent.

    Returns list of cluster dicts with center, detection_ids, score, corridor_id.
    """
    from app.models.stubs import DarkVesselDetection
    from app.utils.geo import haversine_nm

    detections = (
        db.query(DarkVesselDetection)
        .filter(DarkVesselDetection.ais_match_result == "unmatched")
        .all()
    )

    if len(detections) < min_detections:
        return []

    # Build adjacency: each detection knows its neighbors
    remaining = {d.detection_id: d for d in detections}
    clusters: list[dict] = []

    while len(remaining) >= min_detections:
        # Find the detection with the most neighbors
        best_center_id = None
        best_neighbors: list[int] = []

        for did, det in remaining.items():
            if det.detection_lat is None or det.detection_lon is None:
                continue
            if det.detection_time_utc is None:
                continue

            neighbors = []
            for nid, ndet in remaining.items():
                if nid == did:
                    continue
                if ndet.detection_lat is None or ndet.detection_lon is None:
                    continue
                if ndet.detection_time_utc is None:
                    continue

                dist = haversine_nm(
                    det.detection_lat, det.detection_lon,
                    ndet.detection_lat, ndet.detection_lon,
                )
                time_diff = abs((det.detection_time_utc - ndet.detection_time_utc).total_seconds())
                if dist <= radius_nm and time_diff <= days_window * 86400:
                    neighbors.append(nid)

            if len(neighbors) >= min_detections - 1 and len(neighbors) > len(best_neighbors):
                best_center_id = did
                best_neighbors = neighbors

        if best_center_id is None:
            break

        # Form cluster
        cluster_ids = [best_center_id] + best_neighbors
        cluster_dets = [remaining[cid] for cid in cluster_ids]

        center_lat = sum(d.detection_lat for d in cluster_dets if d.detection_lat) / len(cluster_dets)
        center_lon = sum(d.detection_lon for d in cluster_dets if d.detection_lon) / len(cluster_dets)

        # Time span
        times = [d.detection_time_utc for d in cluster_dets if d.detection_time_utc]
        time_span_days = (max(times) - min(times)).total_seconds() / 86400 if len(times) >= 2 else 0

        # Corridor match (majority vote)
        corridor_ids = [d.corridor_id for d in cluster_dets if d.corridor_id is not None]
        cluster_corridor = max(set(corridor_ids), key=corridor_ids.count) if corridor_ids else None

        # Score
        score = 0
        if cluster_corridor is not None:
            # Check if STS zone corridor
            from app.models.corridor import Corridor
            corr = db.query(Corridor).filter(Corridor.corridor_id == cluster_corridor).first()
            if corr and corr.corridor_type in ("sts_zone", "STS_ZONE"):
                score += 20
        score += max(0, (len(cluster_dets) - min_detections)) * 15
        tanker_count = sum(
            1 for d in cluster_dets
            if d.vessel_type_inferred and "tanker" in (d.vessel_type_inferred or "").lower()
        )
        if tanker_count > 0:
            score += 10
        if time_span_days > 7:
            score += 25

        clusters.append({
            "center_lat": center_lat,
            "center_lon": center_lon,
            "detection_ids": cluster_ids,
            "count": len(cluster_ids),
            "score": score,
            "corridor_id": cluster_corridor,
            "time_span_days": round(time_span_days, 1),
            "first_seen": min(times).isoformat() if times else None,
            "last_seen": max(times).isoformat() if times else None,
        })

        # Remove clustered detections from remaining
        for cid in cluster_ids:
            remaining.pop(cid, None)

    logger.info("Dark detection clustering: %d clusters from %d detections", len(clusters), len(detections))
    return clusters


def discover_dark_vessels(
    db: Session,
    start_date: str,
    end_date: str,
    skip_fetch: bool = False,
    min_gap_score: int = 50,
) -> dict:
    """Full dark vessel discovery pipeline orchestrator.

    Steps:
      1. Fetch GFW gap events        (SOFT fail)
      2. SAR corridor sweep           (SOFT fail)
      3. Gap detection on local AIS   (HARD fail)
      4. Spoofing detection           (SOFT)
      5. Loitering detection          (SOFT)
      6. STS detection                (SOFT)
      7. Score all alerts             (HARD fail)
      8. Cluster dark detections      (SOFT)
      9. Auto-hunt                    (SOFT)
      10. Identity resolution         (SOFT)
      11. MMSI cloning                (SOFT)
      12. Summary report              (always)

    Args:
        db: SQLAlchemy session.
        start_date: ISO date string.
        end_date: ISO date string.
        skip_fetch: Skip steps 1-2 (use existing data).
        min_gap_score: Min score for auto-hunt.

    Returns dict with run_status, steps, top_alerts.
    """
    from datetime import date as _date

    result: dict[str, Any] = {
        "run_status": "complete",
        "steps": {},
        "top_alerts": [],
    }
    date_from = _date.fromisoformat(start_date)
    date_to = _date.fromisoformat(end_date)

    def _run_step(name: str, fn, *args, hard: bool = False, **kwargs) -> Any:
        """Execute a pipeline step with failure policy."""
        try:
            step_result = fn(*args, **kwargs)
            result["steps"][name] = {"status": "ok", "detail": str(step_result)}
            return step_result
        except Exception as exc:
            logger.warning("Pipeline step '%s' failed: %s", name, exc)
            if hard:
                result["run_status"] = "failed"
                result["steps"][name] = {"status": "failed", "detail": str(exc)}
                raise
            else:
                result["run_status"] = "partial"
                result["steps"][name] = {"status": "failed", "detail": str(exc)}
                return None

    # Step 1: GFW gap events
    if not skip_fetch:
        try:
            from app.modules.gfw_client import import_gfw_gap_events
            gfw_result = _run_step(
                "gfw_gap_events", import_gfw_gap_events,
                db, start_date=start_date, end_date=end_date,
            )
        except Exception:
            pass  # Soft fail — already recorded
    else:
        result["steps"]["gfw_gap_events"] = {"status": "skipped", "detail": "--skip-fetch"}

    # Step 2: SAR corridor sweep
    if not skip_fetch:
        try:
            from app.modules.gfw_client import sweep_corridors_sar
            _run_step(
                "sar_corridor_sweep", sweep_corridors_sar,
                db, start_date=start_date, end_date=end_date,
            )
        except Exception:
            pass
    else:
        result["steps"]["sar_corridor_sweep"] = {"status": "skipped", "detail": "--skip-fetch"}

    # Step 3: Gap detection (HARD)
    try:
        from app.modules.gap_detector import run_gap_detection
        _run_step(
            "gap_detection", run_gap_detection,
            db, date_from=date_from, date_to=date_to,
            hard=True,
        )
    except Exception:
        return result  # Abort on hard fail

    # Step 4: Spoofing detection (SOFT)
    from app.modules.gap_detector import run_spoofing_detection
    _run_step(
        "spoofing_detection", run_spoofing_detection,
        db, date_from=date_from, date_to=date_to,
    )

    # Step 5: Loitering detection (SOFT)
    try:
        from app.modules.loitering_detector import run_loitering_detection
        _run_step(
            "loitering_detection", run_loitering_detection,
            db, date_from=date_from, date_to=date_to,
        )
    except ImportError:
        result["steps"]["loitering_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 6: STS detection (SOFT)
    try:
        from app.modules.sts_detector import detect_sts_events
        _run_step(
            "sts_detection", detect_sts_events,
            db, date_from=date_from, date_to=date_to,
        )
    except ImportError:
        result["steps"]["sts_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 7: Score all alerts (HARD)
    try:
        from app.modules.risk_scoring import rescore_all_alerts
        _run_step("scoring", rescore_all_alerts, db, hard=True)
    except Exception:
        return result

    # Step 8: Cluster dark detections (SOFT)
    _run_step("dark_clustering", cluster_dark_detections, db)

    # Step 9: Auto-hunt (SOFT)
    _run_step("auto_hunt", auto_hunt_dark_vessels, db, min_gap_score=min_gap_score)

    # Step 10: Identity resolution (SOFT)
    try:
        from app.modules.identity_resolver import detect_merge_candidates
        _run_step("identity_resolution", detect_merge_candidates, db)
    except ImportError:
        result["steps"]["identity_resolution"] = {"status": "skipped", "detail": "module not available"}

    # Step 11: MMSI cloning (SOFT)
    try:
        from app.modules.mmsi_cloning_detector import detect_mmsi_cloning
        _run_step("mmsi_cloning", detect_mmsi_cloning, db)
    except ImportError:
        result["steps"]["mmsi_cloning"] = {"status": "skipped", "detail": "module not available"}

    # Step 12: Top alerts summary
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    top = (
        db.query(AISGapEvent, Vessel)
        .join(Vessel, AISGapEvent.vessel_id == Vessel.vessel_id)
        .filter(AISGapEvent.risk_score >= min_gap_score)
        .order_by(AISGapEvent.risk_score.desc())
        .limit(10)
        .all()
    )
    result["top_alerts"] = [
        {
            "gap_event_id": g.gap_event_id,
            "mmsi": v.mmsi,
            "risk_score": g.risk_score,
            "duration_h": round(g.duration_minutes / 60, 1),
            "corridor_id": g.corridor_id,
        }
        for g, v in top
    ]

    return result
