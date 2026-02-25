"""VesselOwner entity — vessel ownership and sanctions status."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class VesselOwner(Base):
    __tablename__ = "vessel_owners"

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
