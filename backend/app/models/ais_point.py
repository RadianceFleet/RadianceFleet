"""AISPoint entity — individual AIS broadcast records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AISClassEnum, Base


class AISPoint(Base):
    __tablename__ = "ais_points"
    __table_args__ = (
        CheckConstraint("lat >= -90 AND lat <= 90", name="ck_ais_lat_bounds"),
        CheckConstraint("lon >= -180 AND lon <= 180", name="ck_ais_lon_bounds"),
        Index("ix_ais_vessel_ts", "vessel_id", "timestamp_utc"),
        UniqueConstraint(
            "vessel_id", "timestamp_utc", "source", name="uq_ais_point_vessel_ts_source"
        ),
    )

    ais_point_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    sog: Mapped[float | None] = mapped_column(Float, nullable=True)
    cog: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ais_class: Mapped[str | None] = mapped_column(
        SAEnum(AISClassEnum), nullable=True, default=AISClassEnum.A
    )
    sog_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    cog_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    raw_payload_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    draught: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_timestamp_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    vessel: Mapped[Vessel] = relationship("Vessel", back_populates="ais_points")
