"""OwnerCluster entity — deduplicated ownership group."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class OwnerCluster(Base):
    __tablename__ = "owner_clusters"

    cluster_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    vessel_count: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=func.now())
