"""SanctionsPropagation entity — multi-hop sanctions risk propagation records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SanctionsPropagation(Base):
    __tablename__ = "sanctions_propagations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    source_vessel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=True
    )
    source_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    propagation_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    propagation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    propagation_path_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
