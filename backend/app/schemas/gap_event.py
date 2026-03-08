"""Pydantic schemas for AISGapEvent."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


class AISPointSummary(BaseModel):
    timestamp_utc: datetime
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    source: str | None = None
    draught: float | None = None
    destination: str | None = None
    nav_status: int | None = None


class MovementEnvelopeRead(BaseModel):
    envelope_id: int
    max_plausible_distance_nm: float | None = None
    actual_gap_distance_nm: float | None = None
    velocity_plausibility_ratio: float | None = None
    envelope_semi_major_nm: float | None = None
    envelope_semi_minor_nm: float | None = None
    envelope_heading_degrees: float | None = None
    confidence_ellipse_geojson: Any | None = None
    interpolated_positions_json: list | None = None
    estimated_method: str | None = None

    model_config = {"from_attributes": True}


class SatelliteCheckSummary(BaseModel):
    sat_check_id: int
    provider: str | None = None
    review_status: str
    copernicus_url: str | None = None
    imagery_url: str | None = None
    cloud_cover_pct: float | None = None

    model_config = {"from_attributes": True}


class SpoofingAnomalySummary(BaseModel):
    anomaly_id: int
    anomaly_type: str
    start_time_utc: datetime
    risk_score_component: int | None = None
    evidence_json: Any | None = None


class LoiteringSummary(BaseModel):
    loiter_id: int
    start_time_utc: datetime
    duration_hours: float | None = None
    mean_lat: float | None = None
    mean_lon: float | None = None
    median_sog_kn: float | None = None


class StsSummary(BaseModel):
    sts_id: int
    partner_name: str | None = None
    partner_mmsi: str | None = None
    detection_type: str | None = None
    start_time_utc: datetime


class GapEventRead(BaseModel):
    gap_event_id: int
    vessel_id: int
    gap_start_utc: datetime
    gap_end_utc: datetime
    duration_minutes: int
    corridor_id: int | None = None
    risk_score: int
    risk_breakdown_json: dict[str, Any] | None = None
    status: str
    analyst_notes: str | None = None
    impossible_speed_flag: bool
    velocity_plausibility_ratio: float | None = None
    max_plausible_distance_nm: float | None = None
    actual_gap_distance_nm: float | None = None
    in_dark_zone: bool
    prior_similar_count: int | None = None
    is_recurring_pattern: bool | None = None
    coverage_quality: str | None = None
    is_feed_outage: bool | None = None
    original_vessel_id: int | None = None
    is_false_positive: bool | None = None
    reviewed_by: str | None = None
    review_date: datetime | None = None

    @field_validator("risk_breakdown_json", mode="before")
    @classmethod
    def _parse_json_string(cls, v: Any) -> dict[str, Any] | None:
        if isinstance(v, str):
            return json.loads(v)
        return v

    model_config = {"from_attributes": True}


class GapEventDetailRead(GapEventRead):
    assigned_to: int | None = None
    assigned_to_username: str | None = None
    assigned_at: str | None = None
    version: int = 1

    vessel_name: str | None = None
    vessel_mmsi: str | None = None
    vessel_flag: str | None = None
    vessel_deadweight: float | None = None
    corridor_name: str | None = None
    movement_envelope: MovementEnvelopeRead | None = None
    satellite_check: SatelliteCheckSummary | None = None
    last_point: AISPointSummary | None = None
    first_point_after: AISPointSummary | None = None
    spoofing_anomalies: list[SpoofingAnomalySummary] | None = None
    loitering_events: list[LoiteringSummary] | None = None
    sts_events: list[StsSummary] | None = None

    model_config = {"from_attributes": False}


class GapEventStatusUpdate(BaseModel):
    status: str
    analyst_notes: str | None = None


class AlertStatusUpdate(BaseModel):
    status: str
    reason: str | None = None
    version: int | None = None


class AlertNoteUpdate(BaseModel):
    notes: str | None = None
    text: str | None = None  # legacy key


class AlertVerdictRequest(BaseModel):
    verdict: str  # "confirmed_tp" or "confirmed_fp"
    reason: str | None = None
    reviewed_by: str | None = None
    version: int | None = None
