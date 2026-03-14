"""Analyst entity — user accounts for multi-analyst workflow."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AnalystRoleEnum, Base


class Analyst(Base):
    __tablename__ = "analysts"

    analyst_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        SAEnum(AnalystRoleEnum), nullable=False, default=AnalystRoleEnum.ANALYST
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # v4.3 — workload balancer
    specializations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_concurrent_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    shift_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shift_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
