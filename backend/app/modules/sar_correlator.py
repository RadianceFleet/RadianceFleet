"""Stage 4-C: Satellite-AIS correlation.

Matches SAR/satellite dark vessel detections to known vessels by
correlating position proximity, vessel length, heading, and class.

Scoring:
  - Proximity: 15 * (1 - distance/max_drift_nm), min 0
  - Length match: +10  (LOA within +/- 15% of DWT-estimated value)
  - Heading match: +10 (always True for v1.1 — no heading on dark detections)
  - Class match: +10   (vessel_type contains "tanker" and detection suggests tanker)

Thresholds:
  score >= 70 -> auto-link (update DarkVesselDetection.ais_match_result)
  40 <= score < 70 -> create MergeCandidate for analyst review
  score < 40 -> no action
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AUTO_LINK_THRESHOLD: float = 40.0
CANDIDATE_THRESHOLD: float = 25.0
PROXIMITY_WEIGHT: float = 15.0
LENGTH_WEIGHT: float = 10.0
HEADING_WEIGHT: float = 10.0
CLASS_WEIGHT: float = 10.0
DEFAULT_MAX_DRIFT_NM: float = 200.0  # fallback if no DWT-based estimate
DEFAULT_ELAPSED_HOURS: float = 48.0  # default gap window for drift calc


# ── Helpers ──────────────────────────────────────────────────────────────────

def estimate_loa(dwt: float) -> float:
    """Estimate vessel length overall (LOA) from deadweight tonnage.

    Empirical approximation for tankers: LOA ~ 5.0 * DWT^0.325
    """
    return 5.0 * (dwt ** 0.325)


def length_matches(
    estimated_loa: float,
    detected_length: float | None,
    tolerance: float = 0.15,
) -> bool:
    """Check if a detected length is within tolerance of estimated LOA.

    Returns True if detected_length is None (no evidence against).
    """
    if detected_length is None:
        return True
    lower = estimated_loa * (1.0 - tolerance)
    upper = estimated_loa * (1.0 + tolerance)
    return lower <= detected_length <= upper


def _heading_diff(h1: float, h2: float) -> float:
    """Compute the smallest angle between two headings (0-360)."""
    diff = abs(h1 - h2) % 360
    return min(diff, 360 - diff)


def _compute_max_drift_nm(dwt: float | None, elapsed_hours: float) -> float:
    """Compute max drift distance using gap_detector logic, with fallback."""
    try:
        from app.modules.gap_detector import compute_max_distance_nm
        return compute_max_distance_nm(dwt, elapsed_hours)
    except Exception:
        # Fallback: 14 kn * elapsed hours
        return 14.0 * elapsed_hours


# ── Main correlation function ────────────────────────────────────────────────

def correlate_sar_detections(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Match unmatched dark vessel detections to known vessels.

    For each unmatched DarkVesselDetection:
      1. Compute drift envelope for each vessel based on last AIS position
      2. Filter vessels within plausible drift range
      3. Score matches on proximity, length, heading, and class
      4. Auto-link (>= 70) or create MergeCandidate (40-69)

    Args:
        db: SQLAlchemy session.
        date_from: Optional filter start date.
        date_to: Optional filter end date.

    Returns:
        Stats dict with counts of processed, auto_linked, candidates_created, skipped.
    """
    from app.models.stubs import DarkVesselDetection
    from app.models.vessel import Vessel
    from app.models.ais_point import AISPoint
    from app.models.merge_candidate import MergeCandidate
    from app.models.base import MergeCandidateStatusEnum

    stats: dict[str, Any] = {
        "detections_processed": 0,
        "auto_linked": 0,
        "candidates_created": 0,
        "skipped_no_position": 0,
        "no_match": 0,
        "errors": [],
    }

    if not settings.SAR_CORRELATION_ENABLED:
        logger.debug("SAR correlation disabled by feature flag")
        return stats

    # Query unmatched detections
    query = db.query(DarkVesselDetection).filter(
        DarkVesselDetection.ais_match_result == "unmatched",
    )
    if date_from is not None:
        query = query.filter(DarkVesselDetection.detection_time_utc >= date_from)
    if date_to is not None:
        query = query.filter(DarkVesselDetection.detection_time_utc <= date_to)

    detections = query.all()

    if not detections:
        logger.info("SAR correlator: no unmatched detections to process")
        return stats

    # Load all vessels with their last known AIS positions
    vessels = db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).all()

    for det in detections:
        # Skip detections with no position
        if det.detection_lat is None or det.detection_lon is None:
            stats["skipped_no_position"] += 1
            continue

        stats["detections_processed"] += 1
        best_score = 0.0
        best_vessel = None
        best_breakdown: dict[str, Any] = {}

        for vessel in vessels:
            try:
                score, breakdown = _score_vessel_match(db, det, vessel)
            except Exception as exc:
                stats["errors"].append(
                    f"det_{det.detection_id}_vessel_{vessel.vessel_id}: {exc}"
                )
                continue

            if score > best_score:
                best_score = score
                best_vessel = vessel
                best_breakdown = breakdown

        if best_score >= AUTO_LINK_THRESHOLD and best_vessel is not None:
            # Auto-link: update the detection
            det.ais_match_result = "matched"
            det.matched_vessel_id = best_vessel.vessel_id
            det.ais_match_attempted = True
            stats["auto_linked"] += 1
            logger.info(
                "SAR correlator auto-linked detection %d to vessel %d (score=%.1f)",
                det.detection_id, best_vessel.vessel_id, best_score,
            )

        elif best_score >= CANDIDATE_THRESHOLD and best_vessel is not None:
            # Create MergeCandidate for analyst review
            _create_merge_candidate(db, det, best_vessel, best_score, best_breakdown)
            stats["candidates_created"] += 1

        else:
            stats["no_match"] += 1

    db.commit()
    logger.info(
        "SAR correlator complete: %d processed, %d auto-linked, %d candidates, %d no-match",
        stats["detections_processed"],
        stats["auto_linked"],
        stats["candidates_created"],
        stats["no_match"],
    )
    return stats


def _score_vessel_match(
    db: Session,
    det: Any,
    vessel: Any,
) -> tuple[float, dict[str, Any]]:
    """Score how well a vessel matches a dark detection.

    Returns (total_score, breakdown_dict).
    """
    from app.models.ais_point import AISPoint

    breakdown: dict[str, Any] = {
        "proximity": 0.0,
        "length": 0.0,
        "heading": 0.0,
        "class": 0.0,
        "total": 0.0,
    }

    # Get vessel's last known AIS position
    last_point = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel.vessel_id)
        .order_by(AISPoint.timestamp_utc.desc())
        .first()
    )
    if last_point is None or last_point.lat is None or last_point.lon is None:
        return 0.0, breakdown

    # Compute elapsed hours between last AIS and detection
    elapsed_hours = DEFAULT_ELAPSED_HOURS
    if det.detection_time_utc is not None and last_point.timestamp_utc is not None:
        delta = abs((det.detection_time_utc - last_point.timestamp_utc).total_seconds())
        elapsed_hours = delta / 3600.0

    # Max drift distance
    max_drift_nm = _compute_max_drift_nm(
        getattr(vessel, "deadweight", None),
        elapsed_hours,
    )
    if max_drift_nm <= 0:
        max_drift_nm = DEFAULT_MAX_DRIFT_NM

    # Distance from last AIS to detection
    dist_nm = haversine_nm(
        last_point.lat, last_point.lon,
        det.detection_lat, det.detection_lon,
    )

    # Skip if outside drift envelope
    if dist_nm > max_drift_nm:
        return 0.0, breakdown

    # 1. Proximity score: 15 * (1 - distance/max_drift_nm), min 0
    proximity = PROXIMITY_WEIGHT * max(0.0, 1.0 - dist_nm / max_drift_nm)
    breakdown["proximity"] = round(proximity, 2)

    # 2. Length match: +10 if LOA within tolerance
    length_score = 0.0
    dwt = getattr(vessel, "deadweight", None)
    detected_length = getattr(det, "length_estimate_m", None)
    if dwt is not None and dwt > 0:
        est_loa = estimate_loa(dwt)
        if length_matches(est_loa, detected_length):
            length_score = LENGTH_WEIGHT
    else:
        # No DWT data — cannot disprove, give partial credit
        length_score = LENGTH_WEIGHT if detected_length is None else 0.0
    breakdown["length"] = length_score

    # 3. Heading match: 0 default; +5 when SAR has no heading; +10 when heading matches
    heading_score = 0.0
    det_heading = getattr(det, "heading", None)
    vessel_heading = getattr(last_point, "heading", None)
    if det_heading is not None and vessel_heading is not None:
        if _heading_diff(det_heading, vessel_heading) <= 15.0:
            heading_score = HEADING_WEIGHT
    elif det_heading is None:
        # SAR detection lacks heading — give partial credit (+5)
        heading_score = HEADING_WEIGHT / 2
    breakdown["heading"] = heading_score

    # 4. Class match: +10 if vessel_type contains "tanker" and detection suggests tanker
    class_score = 0.0
    vessel_type = getattr(vessel, "vessel_type", None) or ""
    det_type = getattr(det, "vessel_type_inferred", None) or ""
    if "tanker" in vessel_type.lower() and "tanker" in det_type.lower():
        class_score = CLASS_WEIGHT
    breakdown["class"] = class_score

    total = proximity + length_score + heading_score + class_score
    breakdown["total"] = round(total, 2)

    return total, breakdown


def _create_merge_candidate(
    db: Session,
    det: Any,
    vessel: Any,
    score: float,
    breakdown: dict[str, Any],
) -> None:
    """Create a MergeCandidate for analyst review of a SAR-AIS correlation."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.base import MergeCandidateStatusEnum

    # MergeCandidate requires vessel_a_id and vessel_b_id.
    # For SAR correlation, vessel_a_id is the matched vessel,
    # and vessel_b_id is the same (self-reference indicating SAR detection match).
    # We use the matched_vessel_id for vessel_a_id and store detection info in match_reasons.
    candidate = MergeCandidate(
        vessel_a_id=vessel.vessel_id,
        vessel_b_id=vessel.vessel_id,
        vessel_a_last_lat=det.detection_lat,
        vessel_a_last_lon=det.detection_lon,
        vessel_a_last_time=det.detection_time_utc,
        confidence_score=int(score),
        match_reasons_json={
            "source": "sar_correlation",
            "detection_id": det.detection_id,
            "score_breakdown": breakdown,
        },
        status=MergeCandidateStatusEnum.PENDING,
    )
    db.add(candidate)
