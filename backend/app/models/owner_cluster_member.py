"""OwnerClusterMember entity — links VesselOwner to OwnerCluster."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class OwnerClusterMember(Base):
    __tablename__ = "owner_cluster_members"

    member_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("owner_clusters.cluster_id"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessel_owners.owner_id"), nullable=False
    )
    similarity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
