"""Pydantic schemas for hunt and dark-vessel entities."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class VesselTargetProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    profile_id: int
    vessel_id: int
    reference_images_json: list | None = None
    hull_type: str | None = None
    deadweight_dwt: float | None = None
    loa_meters: float | None = None
    beam_meters: float | None = None
    typical_draft_meters: float | None = None
    funnel_color: str | None = None
    hull_color: str | None = None
    last_ais_position_lat: float | None = None
    last_ais_position_lon: float | None = None
    last_ais_timestamp_utc: datetime | None = None
    profile_created_at: datetime
    created_by_analyst_id: str | None = None


class SearchMissionRead(BaseModel):
    model_config = {"from_attributes": True}

    mission_id: int
    vessel_id: int
    profile_id: int | None = None
    search_start_utc: datetime | None = None
    search_end_utc: datetime | None = None
    created_at: datetime
    analyst_id: str | None = None
    search_ellipse_wkt: str | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    max_radius_nm: float | None = None
    elapsed_hours: float | None = None
    confidence: str | None = None
    status: str


class HuntCandidateRead(BaseModel):
    model_config = {"from_attributes": True}

    candidate_id: int
    mission_id: int
    detection_lat: float | None = None
    detection_lon: float | None = None
    detection_time_utc: datetime | None = None
    visual_similarity_score: float | None = None
    length_estimate_m: float | None = None
    heading_estimate_deg: float | None = None
    hunt_score: float | None = None
    score_breakdown_json: dict | None = None
    satellite_scene_id: str | None = None
    image_chip_ref: str | None = None
    analyst_review_status: str | None = None
    government_alert_sent: bool


class DarkVesselDetectionRead(BaseModel):
    model_config = {"from_attributes": True}

    detection_id: int
    scene_id: str | None = None
    detection_lat: float | None = None
    detection_lon: float | None = None
    detection_time_utc: datetime | None = None
    length_estimate_m: float | None = None
    vessel_type_inferred: str | None = None
    ais_match_attempted: bool
    ais_match_result: str | None = None
    matched_vessel_id: int | None = None
    corridor_id: int | None = None
    model_confidence: float | None = None
    created_gap_event_id: int | None = None


class HuntTargetCreateRequest(BaseModel):
    vessel_id: int
    last_lat: float | None = None
    last_lon: float | None = None


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
