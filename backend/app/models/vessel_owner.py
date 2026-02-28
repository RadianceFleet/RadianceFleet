"""VesselOwner entity — vessel ownership and sanctions status."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class VesselOwner(Base):
    __tablename__ = "vessel_owners"

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ownership verification fields (Phase C15)
    verified_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ism_manager: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pi_club_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
