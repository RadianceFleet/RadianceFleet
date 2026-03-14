"""VesselOwner entity — vessel ownership and sanctions status."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class VesselOwner(Base):
    __tablename__ = "vessel_owners"

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ownership verification fields (Phase C15)
    verified_by: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ism_manager: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pi_club_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Stage 5-A: Ownership graph
    parent_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ownership_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Beneficial ownership transparency (OpenCorporates enrichment)
    opencorporates_url: Mapped[str | None] = mapped_column(String, nullable=True)
    company_number: Mapped[str | None] = mapped_column(String, nullable=True)
    incorporation_jurisdiction: Mapped[str | None] = mapped_column(String, nullable=True)
    incorporation_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_spv: Mapped[bool] = mapped_column(Boolean, default=False)
    spv_indicators_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
