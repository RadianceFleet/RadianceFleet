"""FlagRiskProfile — per-flag continuous risk scoring (v2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FlagRiskProfile(Base):
    __tablename__ = "flag_risk_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flag_code: Mapped[str] = mapped_column(String(10), unique=True, index=True, nullable=False)

    # Component scores (each 0-100)
    psc_detention_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fp_rate_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fleet_composition_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flag_hopping_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    transparency_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Weighted composite
    composite_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_tier: Mapped[str] = mapped_column(String(10), nullable=False, default="LOW")

    # Raw counts for diagnostics
    vessel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fp_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Detailed computation log
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=func.now(), onupdate=func.now()
    )
