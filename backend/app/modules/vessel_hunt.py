"""FR9: Named Vessel Hunt — drift ellipse construction and candidate scoring.

Workflow:
  1. create_target_profile()  — register a vessel for hunting
  2. create_search_mission()  — build drift ellipse for the missing window
  3. find_hunt_candidates()   — score dark vessel detections within ellipse
  4. finalize_mission()       — mark best candidate as confirmed
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models.stubs import (
    VesselTargetProfile,
    SearchMission,
    HuntCandidate,
    DarkVesselDetection,
)
from app.models.vessel import Vessel
from app.modules.gap_detector import compute_max_distance_nm, _haversine_nm


def create_target_profile(vessel_id: int, db: Session) -> VesselTargetProfile:
    """Register a vessel as a surveillance target."""
    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if vessel is None:
        raise ValueError(f"Vessel {vessel_id} not found")
    profile = VesselTargetProfile(
        vessel_id=vessel_id,
        deadweight_dwt=vessel.deadweight,
        loa_meters=None,
        beam_meters=None,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def create_search_mission(
    target_profile_id: int,
    search_start_utc: datetime,
    search_end_utc: datetime,
    db: Session,
) -> SearchMission:
    """
    Build a search mission with drift ellipse for a vessel hunt window.

    The ellipse is approximated as a circle of radius = compute_max_distance_nm(dwt, elapsed_hours).
    Polygon vertices are computed via haversine offsets at 36 cardinal points (10 deg increments).
    Center is the vessel's last_ais_position from the profile.
    """
    profile = db.query(VesselTargetProfile).filter(
        VesselTargetProfile.profile_id == target_profile_id
    ).first()
    if profile is None:
        raise ValueError(f"Target profile {target_profile_id} not found")

    elapsed_hours = (search_end_utc - search_start_utc).total_seconds() / 3600
    max_radius_nm = compute_max_distance_nm(profile.deadweight_dwt, elapsed_hours)

    if profile.last_ais_position_lat is None or profile.last_ais_position_lon is None:
        raise ValueError(
            f"Target profile {target_profile_id} has no last AIS position — "
            "set last_ais_position_lat/lon before creating a mission"
        )
    center_lat = profile.last_ais_position_lat
    center_lon = profile.last_ais_position_lon

    # Build WKT polygon: circle approximated with 36 points
    NM_TO_DEG_LAT = 1.0 / 60.0
    points = []
    for i in range(36):
        angle_deg = i * 10
        angle_rad = math.radians(angle_deg)
        dlat = max_radius_nm * NM_TO_DEG_LAT * math.cos(angle_rad)
        # Longitude degrees depend on latitude
        nm_to_deg_lon = 1.0 / (60.0 * math.cos(math.radians(center_lat))) if center_lat != 90 else 1.0
        dlon = max_radius_nm * nm_to_deg_lon * math.sin(angle_rad)
        points.append(f"{center_lon + dlon} {center_lat + dlat}")
    points.append(points[0])  # close the ring
    ellipse_wkt = f"POLYGON(({', '.join(points)}))"

    mission = SearchMission(
        vessel_id=profile.vessel_id,
        profile_id=profile.profile_id,
        search_start_utc=search_start_utc,
        search_end_utc=search_end_utc,
        search_ellipse_wkt=ellipse_wkt,
        center_lat=center_lat,
        center_lon=center_lon,
        max_radius_nm=max_radius_nm,
        elapsed_hours=elapsed_hours,
        status="pending_imagery",
    )
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


def _compute_hunt_score(
    det: DarkVesselDetection,
    mission: SearchMission,
    vessel: Vessel | None,
) -> tuple[float, dict]:
    """
    Score a candidate detection against the hunt mission.

    PRD 7.9.5 formula (max 60 pts without visual_similarity):
      visual_similarity x 40  — skipped in v1.1 (no ML inference)
      length_match      x 20
      heading_plausible x 15  — always True for dark vessel (no heading data)
      drift_probability x 15
      vessel_class_match x 10

    Score bands: HIGH >= 80, MEDIUM 50-79, LOW < 50
    Note: visual_similarity (weight 40) is None in v1.1, so max possible = 60.
    """
    breakdown: dict = {}

    # Length match (0 or 1): within 20% of profile LOA
    length_score = 0.0
    if (
        det.length_estimate_m is not None
        and vessel is not None
        and vessel.deadweight is not None
        and vessel.deadweight > 0
    ):
        # Rough LOA estimate from DWT (tanker approximation)
        estimated_loa = 150 + (vessel.deadweight / 3000)
        ratio = det.length_estimate_m / estimated_loa
        length_score = 20.0 if 0.8 <= ratio <= 1.2 else 0.0
    breakdown["length_match"] = length_score

    # Heading plausible: always True for dark vessel (no heading to check)
    heading_score = 15.0
    breakdown["heading_plausible"] = heading_score

    # Drift probability: inverse of distance / max_radius
    drift_score = 0.0
    if mission.center_lat is not None and mission.center_lon is not None and mission.max_radius_nm and mission.max_radius_nm > 0:
        if det.detection_lat is not None and det.detection_lon is not None:
            dist_nm = _haversine_nm(
                mission.center_lat, mission.center_lon,
                det.detection_lat, det.detection_lon,
            )
            proximity_ratio = max(0.0, 1.0 - dist_nm / mission.max_radius_nm)
            drift_score = 15.0 * proximity_ratio
    breakdown["drift_probability"] = drift_score

    # Vessel class match: type inference matches vessel type
    class_score = 0.0
    if (
        det.vessel_type_inferred is not None
        and vessel is not None
        and vessel.vessel_type is not None
        and det.vessel_type_inferred.lower() in vessel.vessel_type.lower()
    ):
        class_score = 10.0
    breakdown["vessel_class_match"] = class_score

    total = length_score + heading_score + drift_score + class_score
    breakdown["total"] = total
    breakdown["visual_similarity"] = None  # v1.1: no ML inference

    return total, breakdown


def find_hunt_candidates(mission_id: int, db: Session) -> list[HuntCandidate]:
    """
    Find and score dark vessel detections within drift ellipse during search window.

    Sources:
    1. DarkVesselDetection where ais_match_result='unmatched' and
       detection within search window and within max_radius_nm of center
    """
    mission = db.query(SearchMission).filter(
        SearchMission.mission_id == mission_id
    ).first()
    if mission is None:
        raise ValueError(f"Mission {mission_id} not found")

    vessel = db.query(Vessel).filter(Vessel.vessel_id == mission.vessel_id).first()

    # Use the actual search window stored on the mission (not DB creation time)
    window_start = mission.search_start_utc or mission.created_at
    window_end = mission.search_end_utc
    if window_end is None:
        window_end_seconds = (mission.elapsed_hours or 24) * 3600
        window_end = window_start + timedelta(seconds=window_end_seconds)

    detections = db.query(DarkVesselDetection).filter(
        DarkVesselDetection.ais_match_result == "unmatched",
        DarkVesselDetection.detection_time_utc >= window_start,
        DarkVesselDetection.detection_time_utc <= window_end,
    ).all()

    candidates = []
    max_radius = mission.max_radius_nm or float("inf")

    for det in detections:
        if det.detection_lat is None or det.detection_lon is None:
            continue
        if mission.center_lat is not None and mission.center_lon is not None:
            dist_nm = _haversine_nm(
                mission.center_lat, mission.center_lon,
                det.detection_lat, det.detection_lon,
            )
            if dist_nm > max_radius:
                continue

        score, breakdown = _compute_hunt_score(det, mission, vessel)

        if score >= 80:
            band = "HIGH"
        elif score >= 50:
            band = "MEDIUM"
        else:
            band = "LOW"

        candidate = HuntCandidate(
            mission_id=mission_id,
            detection_lat=det.detection_lat,
            detection_lon=det.detection_lon,
            detection_time_utc=det.detection_time_utc,
            visual_similarity_score=None,  # v1.1: no ML
            length_estimate_m=det.length_estimate_m,
            heading_estimate_deg=None,
            hunt_score=score,
            score_breakdown_json={**breakdown, "band": band},
            satellite_scene_id=det.scene_id,
            analyst_review_status=band.lower(),
        )
        db.add(candidate)
        candidates.append(candidate)

    db.commit()
    for c in candidates:
        db.refresh(c)
    return candidates


def finalize_mission(mission_id: int, candidate_id: int, db: Session) -> SearchMission:
    """Mark mission complete and candidate as confirmed."""
    mission = db.query(SearchMission).filter(
        SearchMission.mission_id == mission_id
    ).first()
    if mission is None:
        raise ValueError(f"Mission {mission_id} not found")

    candidate = db.query(HuntCandidate).filter(
        HuntCandidate.candidate_id == candidate_id
    ).first()
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id} not found")

    candidate.analyst_review_status = "confirmed"
    candidate.government_alert_sent = False  # will be set by FR10
    mission.status = "reviewed"
    db.commit()
    db.refresh(mission)
    return mission
