"""CaseAnalyst — tracks multi-analyst participation on investigation cases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CaseAnalyst(Base):
    __tablename__ = "case_analysts"
    __table_args__ = (UniqueConstraint("case_id", "analyst_id", name="uq_case_analyst"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("investigation_cases.case_id"), nullable=False, index=True
    )
    analyst_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="contributor"
    )  # "lead", "contributor", "reviewer"
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    added_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
