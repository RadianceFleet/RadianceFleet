"""v1.1 entity stubs.

These tables are created in v1.0 with their full schema so v1.1 does not require
schema migrations. See PRD §7.9, §7.10 for full specifications.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Float, Boolean, DateTime, JSON, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class VesselTargetProfile(Base):
    """Named vessel profile for the vessel hunt module (v1.1)."""
    __tablename__ = "vessel_target_profiles"

    profile_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False)
    reference_images_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    hull_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    deadweight_dwt: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    loa_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    beam_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    typical_draft_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    funnel_color: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    hull_color: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_ais_position_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_ais_position_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_ais_timestamp_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    profile_created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_by_analyst_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class SearchMission(Base):
    """Vessel hunt search mission (v1.1)."""
    __tablename__ = "search_missions"

    mission_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False)
    profile_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("vessel_target_profiles.profile_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    analyst_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    search_ellipse_wkt: Mapped[Optional[str]] = mapped_column(String(5000), nullable=True)
    center_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    center_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_radius_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending_imagery")


class HuntCandidate(Base):
    """Vessel detection candidate from a hunt mission (v1.1)."""
    __tablename__ = "hunt_candidates"

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_missions.mission_id"), nullable=False)
    detection_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detection_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detection_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    visual_similarity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    length_estimate_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heading_estimate_deg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hunt_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    satellite_scene_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    image_chip_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    analyst_review_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    government_alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class DarkVesselDetection(Base):
    """Satellite-detected vessel with no matching AIS (v1.1)."""
    __tablename__ = "dark_vessel_detections"

    detection_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    detection_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detection_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detection_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    length_estimate_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vessel_type_inferred: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ais_match_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    ais_match_result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    matched_vessel_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=True)
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("corridors.corridor_id"), nullable=True)
    model_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_gap_event_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True)
