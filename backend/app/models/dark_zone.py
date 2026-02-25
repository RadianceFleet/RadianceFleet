"""DarkZone entity — GPS jamming / AIS blackout regions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geometry
from app.models.base import Base, DarkZoneTypeEnum


class DarkZone(Base):
    __tablename__ = "dark_zones"

    zone_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    geometry: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=True
    )
    zone_type: Mapped[str] = mapped_column(
        SAEnum(DarkZoneTypeEnum), nullable=False, default=DarkZoneTypeEnum.ACTIVE_JAMMING
    )
    risk_explanation: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
