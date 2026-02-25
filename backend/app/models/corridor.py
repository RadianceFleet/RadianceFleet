"""Corridor / zone of interest entity."""
from __future__ import annotations

from typing import Optional
from sqlalchemy import Integer, String, Float, Boolean, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from geoalchemy2 import Geometry
from app.models.base import Base, CorridorTypeEnum


class Corridor(Base):
    __tablename__ = "corridors"

    corridor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    geometry: Mapped[object] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=False
    )
    risk_weight: Mapped[float] = mapped_column(Float, default=1.0)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    corridor_type: Mapped[str] = mapped_column(
        SAEnum(CorridorTypeEnum), nullable=False, default=CorridorTypeEnum.EXPORT_ROUTE
    )
    is_jamming_zone: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    gap_events: Mapped[list] = relationship("AISGapEvent", back_populates="corridor")
