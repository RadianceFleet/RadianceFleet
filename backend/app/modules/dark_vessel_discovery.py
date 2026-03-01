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

from app.config import settings

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
    from datetime import date as _date, datetime as _datetime

    result: dict[str, Any] = {
        "run_status": "complete",
        "steps": {},
        "top_alerts": [],
    }
    date_from = _date.fromisoformat(start_date)
    date_to = _date.fromisoformat(end_date)

    # Create PipelineRun record
    pipeline_run = None
    try:
        from app.models.pipeline_run import PipelineRun
        pipeline_run = PipelineRun(status="running")
        db.add(pipeline_run)
        db.flush()  # get run_id
    except Exception as exc:
        logger.debug("Could not create PipelineRun: %s", exc)

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

    # Step 3b: Feed outage detection — MOVED to Step 6b (after anomaly detection)
    # so that gaps with co-occurring STS/spoofing signals are NOT suppressed.

    # Step 3c: Coverage quality tagging (SOFT, feature-gated)
    if settings.COVERAGE_QUALITY_TAGGING_ENABLED:
        try:
            from app.modules.feed_outage_detector import tag_coverage_quality
            _run_step("coverage_quality_tagging", tag_coverage_quality, db)
        except ImportError:
            result["steps"]["coverage_quality_tagging"] = {"status": "skipped", "detail": "module not available"}

    # Step 4: Spoofing detection (SOFT)
    from app.modules.gap_detector import run_spoofing_detection
    _run_step(
        "spoofing_detection", run_spoofing_detection,
        db, date_from=date_from, date_to=date_to,
    )

    # Step 4a: Stale AIS detection (SOFT, feature-gated)
    if settings.STALE_AIS_DETECTION_ENABLED:
        try:
            from app.modules.gap_detector import detect_stale_ais_data
            _run_step(
                "stale_ais_detection", detect_stale_ais_data,
                db, date_from=date_from, date_to=date_to,
            )
        except ImportError:
            result["steps"]["stale_ais_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 4b: Destination manipulation detection (SOFT, feature-gated)
    if settings.DESTINATION_DETECTION_ENABLED:
        try:
            from app.modules.destination_detector import detect_destination_anomalies
            _run_step(
                "destination_detection", detect_destination_anomalies,
                db, date_from=date_from, date_to=date_to,
            )
        except ImportError:
            result["steps"]["destination_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 4c: Track naturalness (SOFT, feature-gated)
    if settings.TRACK_NATURALNESS_ENABLED:
        try:
            from app.modules.track_naturalness_detector import run_track_naturalness_detection
            _run_step("track_naturalness", run_track_naturalness_detection, db)
        except ImportError:
            result["steps"]["track_naturalness"] = {"status": "skipped", "detail": "module not available"}

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

    # Step 6b: STS relay chain detection (SOFT, feature-gated)
    if settings.STS_CHAIN_DETECTION_ENABLED:
        try:
            from app.modules.sts_chain_detector import detect_sts_chains
            _run_step(
                "sts_chain_detection", detect_sts_chains,
                db, date_from=date_from, date_to=date_to,
            )
        except ImportError:
            result["steps"]["sts_chain_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 6c: Draught detection (SOFT, feature-gated)
    if settings.DRAUGHT_DETECTION_ENABLED:
        try:
            from app.modules.draught_detector import run_draught_detection
            _run_step("draught_detection", run_draught_detection, db)
        except ImportError:
            result["steps"]["draught_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 6b: Feed outage detection (SOFT, feature-gated)
    # Runs AFTER anomaly detection (Steps 4-6c) so gaps with co-occurring
    # STS/spoofing signals are NOT marked as feed outage (see E2).
    if settings.FEED_OUTAGE_DETECTION_ENABLED:
        try:
            from app.modules.feed_outage_detector import detect_feed_outages
            _run_step("feed_outage_detection", detect_feed_outages, db)
        except ImportError:
            result["steps"]["feed_outage_detection"] = {"status": "skipped", "detail": "module not available"}

    # Steps 6d-6h: Identity fraud + scrapped + convoy detectors (MOVED before scoring)
    # These detectors create SpoofingAnomaly records that scoring reads.
    # Previously at Steps 11b-11f (after scoring), their signals were ZERO.
    if settings.STATELESS_MMSI_DETECTION_ENABLED:
        try:
            from app.modules.stateless_detector import run_stateless_detection
            _run_step("stateless_mmsi", run_stateless_detection, db)
        except ImportError:
            result["steps"]["stateless_mmsi"] = {"status": "skipped", "detail": "module not available"}
    if settings.FLAG_HOPPING_DETECTION_ENABLED:
        try:
            from app.modules.flag_hopping_detector import run_flag_hopping_detection
            _run_step("flag_hopping", run_flag_hopping_detection, db)
        except ImportError:
            result["steps"]["flag_hopping"] = {"status": "skipped", "detail": "module not available"}
    if settings.IMO_FRAUD_DETECTION_ENABLED:
        try:
            from app.modules.imo_fraud_detector import run_imo_fraud_detection
            _run_step("imo_fraud", run_imo_fraud_detection, db)
        except ImportError:
            result["steps"]["imo_fraud"] = {"status": "skipped", "detail": "module not available"}

    # Step 6e2: IMO fraud merge recheck (SOFT)
    if settings.IMO_FRAUD_DETECTION_ENABLED:
        try:
            from app.modules.identity_resolver import recheck_merges_for_imo_fraud
            _run_step("imo_fraud_merge_recheck", recheck_merges_for_imo_fraud, db)
        except ImportError:
            result["steps"]["imo_fraud_merge_recheck"] = {"status": "skipped", "detail": "module not available"}

    # Step 6f: Scrapped vessel registry detection (SOFT, feature-gated)
    if settings.SCRAPPED_REGISTRY_DETECTION_ENABLED:
        try:
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse
            _run_step(
                "scrapped_registry", detect_scrapped_imo_reuse,
                db, date_from=date_from, date_to=date_to,
            )
        except ImportError:
            result["steps"]["scrapped_registry"] = {"status": "skipped", "detail": "module not available"}

    # Step 6g: Track replay detection (SOFT, feature-gated)
    if settings.TRACK_REPLAY_DETECTION_ENABLED:
        try:
            from app.modules.scrapped_registry import detect_track_replay
            _run_step(
                "track_replay", detect_track_replay,
                db, date_from=date_from, date_to=date_to,
            )
        except ImportError:
            result["steps"]["track_replay"] = {"status": "skipped", "detail": "module not available"}

    # Step 6h: Convoy detection (SOFT, feature-gated)
    if settings.CONVOY_DETECTION_ENABLED:
        try:
            from app.modules.convoy_detector import detect_convoys, detect_floating_storage, detect_arctic_no_ice_class
            _run_step(
                "convoy_detection", detect_convoys,
                db, date_from=date_from, date_to=date_to,
            )
            _run_step("floating_storage", detect_floating_storage, db)
            _run_step("arctic_no_ice_class", detect_arctic_no_ice_class, db)
        except ImportError:
            result["steps"]["convoy_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 6i: Vessel type consistency (SOFT, feature-gated)
    if settings.TYPE_CONSISTENCY_DETECTION_ENABLED:
        try:
            from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
            _run_step("vessel_type_consistency", run_vessel_type_consistency_detection, db)
        except ImportError:
            result["steps"]["vessel_type_consistency"] = {"status": "skipped", "detail": "module not available"}

    # Step 6k: Route laundering (SOFT, feature-gated)
    if settings.ROUTE_LAUNDERING_DETECTION_ENABLED:
        try:
            from app.modules.route_laundering_detector import run_route_laundering_detection
            _run_step("route_laundering", run_route_laundering_detection, db)
        except ImportError:
            result["steps"]["route_laundering"] = {"status": "skipped", "detail": "module not available"}

    # Step 6l: P&I cycling (SOFT, feature-gated)
    if settings.PI_CYCLING_DETECTION_ENABLED:
        try:
            from app.modules.pi_cycling_detector import run_pi_cycling_detection
            _run_step("pi_cycling", run_pi_cycling_detection, db)
        except ImportError:
            result["steps"]["pi_cycling"] = {"status": "skipped", "detail": "module not available"}

    # Step 6m: Sparse transmission (SOFT, feature-gated)
    if settings.SPARSE_TRANSMISSION_DETECTION_ENABLED:
        try:
            from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
            _run_step("sparse_transmission", run_sparse_transmission_detection, db)
        except ImportError:
            result["steps"]["sparse_transmission"] = {"status": "skipped", "detail": "module not available"}

    # Step 7: Score all alerts (HARD)
    try:
        from app.modules.risk_scoring import rescore_all_alerts
        _run_step("scoring", rescore_all_alerts, db, hard=True)
    except Exception:
        return result

    # Step 7b: Confidence classification (SOFT)
    # Runs after scoring to classify vessels into CONFIRMED/HIGH/MEDIUM/LOW/NONE
    try:
        from app.modules.confidence_classifier import classify_all_vessels
        _run_step("confidence_classification", classify_all_vessels, db)
    except ImportError:
        result["steps"]["confidence_classification"] = {"status": "skipped", "detail": "module not available"}

    # Step 8: Cluster dark detections (SOFT)
    _run_step("dark_clustering", cluster_dark_detections, db)

    # Step 9: Auto-hunt (SOFT)
    _run_step("auto_hunt", auto_hunt_dark_vessels, db, min_gap_score=min_gap_score)

    # Step 9b: SAR-AIS correlation (SOFT, feature-gated)
    if settings.SAR_CORRELATION_ENABLED:
        try:
            from app.modules.sar_correlator import correlate_sar_detections
            _run_step("sar_correlation", correlate_sar_detections, db)
        except ImportError:
            result["steps"]["sar_correlation"] = {"status": "skipped", "detail": "module not available"}

    # Step 10: Identity resolution (SOFT)
    try:
        from app.modules.identity_resolver import detect_merge_candidates
        _run_step("identity_resolution", detect_merge_candidates, db)
    except ImportError:
        result["steps"]["identity_resolution"] = {"status": "skipped", "detail": "module not available"}

    # Step 10b: Extended merge pass + chain detection (SOFT, feature-gated)
    if settings.MERGE_CHAIN_DETECTION_ENABLED:
        try:
            from app.modules.identity_resolver import extended_merge_pass, detect_merge_chains
            _run_step("extended_merge_pass", extended_merge_pass, db)
            _run_step("merge_chain_detection", detect_merge_chains, db)
        except ImportError:
            result["steps"]["merge_chain_detection"] = {"status": "skipped", "detail": "module not available"}

    # Step 11: MMSI cloning (SOFT)
    try:
        from app.modules.mmsi_cloning_detector import detect_mmsi_cloning
        _run_step("mmsi_cloning", detect_mmsi_cloning, db)
    except ImportError:
        result["steps"]["mmsi_cloning"] = {"status": "skipped", "detail": "module not available"}

    # Note: Identity fraud detectors (stateless, flag_hopping, imo_fraud),
    # scrapped registry, track replay, and convoy detection were MOVED to
    # Steps 6d-6h (before scoring) so their signals contribute to scores.

    # Step 11c: Fleet analysis + ISM continuity (SOFT, feature-gated)
    if settings.FLEET_ANALYSIS_ENABLED:
        try:
            from app.modules.owner_dedup import run_owner_dedup
            _run_step("owner_dedup", run_owner_dedup, db)
        except ImportError:
            result["steps"]["owner_dedup"] = {"status": "skipped", "detail": "module not available"}
        try:
            from app.modules.fleet_analyzer import run_fleet_analysis
            _run_step("fleet_analysis", run_fleet_analysis, db)
        except ImportError:
            result["steps"]["fleet_analysis"] = {"status": "skipped", "detail": "module not available"}

    # Step 11c2: ISM/P&I continuity detection (SOFT, feature-gated)
    if settings.ISM_CONTINUITY_DETECTION_ENABLED:
        try:
            from app.modules.fleet_analyzer import detect_ism_pi_continuity
            _run_step("ism_pi_continuity", detect_ism_pi_continuity, db)
        except ImportError:
            result["steps"]["ism_pi_continuity"] = {"status": "skipped", "detail": "module not available"}

    # Step 11d: Ownership graph (SOFT, feature-gated)
    if settings.OWNERSHIP_GRAPH_ENABLED:
        try:
            from app.modules.ownership_graph import build_ownership_graph, propagate_sanctions
            _run_step("ownership_graph", build_ownership_graph, db)
            _run_step("sanctions_propagation", propagate_sanctions, db)
        except ImportError:
            result["steps"]["ownership_graph"] = {"status": "skipped", "detail": "module not available"}

    # Step 11g: Behavioral fingerprinting (SOFT, feature-gated)
    if settings.FINGERPRINT_ENABLED:
        try:
            from app.modules.vessel_fingerprint import run_fingerprint_computation
            _run_step("fingerprint_computation", run_fingerprint_computation, db)
        except ImportError:
            result["steps"]["fingerprint_computation"] = {"status": "skipped", "detail": "module not available"}

    # Step 11h: Voyage prediction (SOFT, feature-gated)
    if settings.VOYAGE_PREDICTION_ENABLED:
        try:
            from app.modules.voyage_predictor import build_route_templates
            _run_step("route_templates", build_route_templates, db)
        except ImportError:
            result["steps"]["route_templates"] = {"status": "skipped", "detail": "module not available"}

    # Step 11z: UNCONDITIONAL second scoring + classification pass.
    # Fingerprint, voyage predictor, fleet analysis, and ownership graph can all
    # create new SpoofingAnomaly/FleetAlert records after Step 7's first scoring pass.
    # Re-scoring ensures ALL post-Step-10 signals are captured in risk scores.
    try:
        from app.modules.risk_scoring import rescore_all_alerts as _rescore_second
        _run_step("scoring_second_pass", _rescore_second, db)
    except Exception:
        pass  # Non-fatal — first pass scores are still valid
    try:
        from app.modules.confidence_classifier import classify_all_vessels as _classify_second
        _run_step("confidence_classification_second_pass", _classify_second, db)
    except ImportError:
        pass

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

    # Finalize PipelineRun — record anomaly counts, data volume, and drift
    if pipeline_run is not None:
        try:
            # E4: Skip drift detection during warm-up period (< 3 pipeline runs)
            from app.models.pipeline_run import PipelineRun as _PR_warmup
            run_count = db.query(_PR_warmup).count()
            if run_count < 3:
                result["steps"]["drift_detection"] = {"status": "skipped", "detail": "warm_up_period"}
                _finalize_pipeline_run(db, pipeline_run, result, skip_drift=True)
            else:
                _finalize_pipeline_run(db, pipeline_run, result)
        except Exception as exc:
            logger.debug("Could not finalize PipelineRun: %s", exc)

    return result


def _finalize_pipeline_run(db: Session, pipeline_run, result: dict, skip_drift: bool = False) -> None:
    """Record anomaly counts, data volume, and detect drift.

    Drift detection: if any detector's anomaly count changed >50% between
    consecutive runs AND data volume change is <20%, auto-disable scoring
    for that detector.

    Args:
        skip_drift: If True, skip drift detection (warm-up period, E4).
    """
    from datetime import datetime as _dt
    from sqlalchemy import func

    from app.models.pipeline_run import PipelineRun
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.gap_event import AISGapEvent
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel

    # Collect anomaly counts per detector type
    anomaly_counts: dict[str, int] = {}
    try:
        rows = db.query(
            SpoofingAnomaly.anomaly_type,
            func.count(SpoofingAnomaly.spoofing_id),
        ).group_by(SpoofingAnomaly.anomaly_type).all()
        for atype, count in rows:
            atype_str = atype.value if hasattr(atype, "value") else str(atype)
            anomaly_counts[atype_str] = count
    except Exception:
        pass

    anomaly_counts["gap_events"] = db.query(AISGapEvent).count()

    # Data volume
    data_volume = {
        "ais_points_count": db.query(AISPoint).count(),
        "vessels_count": db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).count(),
    }

    pipeline_run.detector_anomaly_counts_json = anomaly_counts
    pipeline_run.data_volume_json = data_volume
    pipeline_run.completed_at = _dt.now(tz=__import__('datetime').timezone.utc)
    pipeline_run.status = result.get("run_status", "complete")

    # Drift detection — compare with previous run
    drift_disabled: list[str] = []

    if skip_drift:
        logger.info("Drift detection skipped: warm-up period (< 3 pipeline runs)")
        pipeline_run.drift_disabled_detectors_json = None
        db.commit()
        return

    prev_run = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.run_id != pipeline_run.run_id,
            PipelineRun.status.in_(["complete", "partial"]),
        )
        .order_by(PipelineRun.run_id.desc())
        .first()
    )

    if prev_run and prev_run.detector_anomaly_counts_json and prev_run.data_volume_json:
        prev_counts = prev_run.detector_anomaly_counts_json
        prev_volume = prev_run.data_volume_json

        # Check data volume change
        prev_pts = prev_volume.get("ais_points_count", 0)
        curr_pts = data_volume.get("ais_points_count", 0)
        data_change_pct = abs(curr_pts - prev_pts) / max(prev_pts, 1) * 100

        if data_change_pct < 20:
            # Only check drift when data volume is stable
            for detector, curr_count in anomaly_counts.items():
                prev_count = prev_counts.get(detector, 0)
                if prev_count == 0:
                    continue
                count_change_pct = abs(curr_count - prev_count) / prev_count * 100
                if count_change_pct > 50:
                    logger.warning(
                        "Drift detected: %s anomaly count changed %.0f%% "
                        "(data volume change: %.0f%%) — scoring auto-disabled",
                        detector, count_change_pct, data_change_pct,
                    )
                    drift_disabled.append(detector)

    # Carry forward previously disabled detectors (unless confirmed)
    if prev_run and prev_run.drift_disabled_detectors_json:
        for det in prev_run.drift_disabled_detectors_json:
            if det not in drift_disabled:
                drift_disabled.append(det)

    pipeline_run.drift_disabled_detectors_json = drift_disabled or None
    db.commit()
