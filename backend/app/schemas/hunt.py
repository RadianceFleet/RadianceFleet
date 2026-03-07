"""Pydantic schemas for hunt and dark-vessel entities."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VesselTargetProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    profile_id: int
    vessel_id: int
    reference_images_json: Optional[list] = None
    hull_type: Optional[str] = None
    deadweight_dwt: Optional[float] = None
    loa_meters: Optional[float] = None
    beam_meters: Optional[float] = None
    typical_draft_meters: Optional[float] = None
    funnel_color: Optional[str] = None
    hull_color: Optional[str] = None
    last_ais_position_lat: Optional[float] = None
    last_ais_position_lon: Optional[float] = None
    last_ais_timestamp_utc: Optional[datetime] = None
    profile_created_at: datetime
    created_by_analyst_id: Optional[str] = None


class SearchMissionRead(BaseModel):
    model_config = {"from_attributes": True}

    mission_id: int
    vessel_id: int
    profile_id: Optional[int] = None
    search_start_utc: Optional[datetime] = None
    search_end_utc: Optional[datetime] = None
    created_at: datetime
    analyst_id: Optional[str] = None
    search_ellipse_wkt: Optional[str] = None
    center_lat: Optional[float] = None
    center_lon: Optional[float] = None
    max_radius_nm: Optional[float] = None
    elapsed_hours: Optional[float] = None
    confidence: Optional[str] = None
    status: str


class HuntCandidateRead(BaseModel):
    model_config = {"from_attributes": True}

    candidate_id: int
    mission_id: int
    detection_lat: Optional[float] = None
    detection_lon: Optional[float] = None
    detection_time_utc: Optional[datetime] = None
    visual_similarity_score: Optional[float] = None
    length_estimate_m: Optional[float] = None
    heading_estimate_deg: Optional[float] = None
    hunt_score: Optional[float] = None
    score_breakdown_json: Optional[dict] = None
    satellite_scene_id: Optional[str] = None
    image_chip_ref: Optional[str] = None
    analyst_review_status: Optional[str] = None
    government_alert_sent: bool


class DarkVesselDetectionRead(BaseModel):
    model_config = {"from_attributes": True}

    detection_id: int
    scene_id: Optional[str] = None
    detection_lat: Optional[float] = None
    detection_lon: Optional[float] = None
    detection_time_utc: Optional[datetime] = None
    length_estimate_m: Optional[float] = None
    vessel_type_inferred: Optional[str] = None
    ais_match_attempted: bool
    ais_match_result: Optional[str] = None
    matched_vessel_id: Optional[int] = None
    corridor_id: Optional[int] = None
    model_confidence: Optional[float] = None
    created_gap_event_id: Optional[int] = None


class HuntTargetCreateRequest(BaseModel):
    vessel_id: int
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None


class SearchMissionCreateRequest(BaseModel):
    target_profile_id: int
    search_start_utc: datetime
    search_end_utc: datetime


class MissionFinalizeRequest(BaseModel):
    candidate_id: int


class DarkVesselListResponse(BaseModel):
    items: list[DarkVesselDetectionRead]
    total: int


class HuntCandidateListResponse(BaseModel):
    items: list[HuntCandidateRead]
    total: int
