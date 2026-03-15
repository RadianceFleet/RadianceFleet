"""OwnerClusterMember entity — links VesselOwner to OwnerCluster."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OwnerClusterMember(Base):
    __tablename__ = "owner_cluster_members"

    member_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("owner_clusters.cluster_id"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessel_owners.owner_id"), nullable=False, index=True
    )
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
