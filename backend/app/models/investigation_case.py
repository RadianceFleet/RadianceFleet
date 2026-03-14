"""InvestigationCase entity — groups related alerts into investigation cases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CasePriorityEnum, CaseStatusEnum


class InvestigationCase(Base):
    __tablename__ = "investigation_cases"

    case_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(CaseStatusEnum), nullable=False, default=CaseStatusEnum.OPEN
    )
    priority: Mapped[str] = mapped_column(
        SAEnum(CasePriorityEnum), nullable=False, default=CasePriorityEnum.MEDIUM
    )
    assigned_to: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    vessel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=True
    )
    corridor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
