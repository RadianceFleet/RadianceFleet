"""MergeOperation — audit trail for vessel identity merges, supports undo."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class MergeOperation(Base):
    __tablename__ = "merge_operations"

    merge_op_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("merge_candidates.candidate_id"), nullable=True
    )
    canonical_vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False
    )
    absorbed_vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    executed_by: Mapped[str] = mapped_column(String(100), nullable=False, default="auto")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    affected_records_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    candidate: Mapped[Optional["MergeCandidate"]] = relationship("MergeCandidate")
    canonical_vessel: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[canonical_vessel_id],
    )
    absorbed_vessel: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[absorbed_vessel_id],
    )
