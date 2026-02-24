"""Pydantic schemas for AISGapEvent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, List

from pydantic import BaseModel


class AISPointSummary(BaseModel):
    timestamp_utc: datetime
    lat: float
    lon: float
    sog: Optional[float] = None
    cog: Optional[float] = None


class MovementEnvelopeRead(BaseModel):
    envelope_id: int
    max_plausible_distance_nm: Optional[float] = None
    actual_gap_distance_nm: Optional[float] = None
    velocity_plausibility_ratio: Optional[float] = None
    envelope_semi_major_nm: Optional[float] = None
    envelope_semi_minor_nm: Optional[float] = None
    envelope_heading_degrees: Optional[float] = None
    confidence_ellipse_geojson: Optional[Any] = None
    interpolated_positions_json: Optional[List] = None
    estimated_method: Optional[str] = None

    model_config = {"from_attributes": True}


class SatelliteCheckSummary(BaseModel):
    sat_check_id: int
    provider: Optional[str] = None
    review_status: str
    copernicus_url: Optional[str] = None
    imagery_url: Optional[str] = None
    cloud_cover_pct: Optional[float] = None

    model_config = {"from_attributes": True}


class GapEventRead(BaseModel):
    gap_event_id: int
    vessel_id: int
    gap_start_utc: datetime
    gap_end_utc: datetime
    duration_minutes: int
    corridor_id: Optional[int] = None
    risk_score: int
    risk_breakdown_json: Optional[dict[str, Any]] = None
    status: str
    analyst_notes: Optional[str] = None
    impossible_speed_flag: bool
    velocity_plausibility_ratio: Optional[float] = None
    max_plausible_distance_nm: Optional[float] = None
    actual_gap_distance_nm: Optional[float] = None
    in_dark_zone: bool

    model_config = {"from_attributes": True}


class GapEventDetailRead(GapEventRead):
    vessel_name: Optional[str] = None
    vessel_mmsi: Optional[str] = None
    vessel_flag: Optional[str] = None
    vessel_deadweight: Optional[float] = None
    corridor_name: Optional[str] = None
    movement_envelope: Optional[MovementEnvelopeRead] = None
    satellite_check: Optional[SatelliteCheckSummary] = None
    last_point: Optional[AISPointSummary] = None
    first_point_after: Optional[AISPointSummary] = None

    model_config = {"from_attributes": False}


class GapEventStatusUpdate(BaseModel):
    status: str
    analyst_notes: Optional[str] = None
