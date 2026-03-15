"""VesselSimilarityResult entity — stores pairwise vessel similarity comparisons."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class VesselSimilarityResult(Base):
    __tablename__ = "vessel_similarity_results"
    __table_args__ = (
        UniqueConstraint("source_vessel_id", "target_vessel_id", name="uq_similarity_pair"),
    )

    result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    target_vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    fingerprint_distance: Mapped[float] = mapped_column(Float, nullable=False)
    fingerprint_band: Mapped[str] = mapped_column(String(20), nullable=False)
    ownership_similarity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    composite_similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    similarity_tier: Mapped[str] = mapped_column(String(10), nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
