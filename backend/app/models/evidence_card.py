"""EvidenceCard entity — exported investigation record."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey, JSON, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class EvidenceCard(Base):
    __tablename__ = "evidence_cards"
    __table_args__ = (
        UniqueConstraint("gap_event_id", "export_format", "version", name="uq_evidence_gap_format_version"),
    )

    evidence_card_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_event_id: Mapped[int] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    export_format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    export_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    # Snapshot fields: capture score at export time so rescoring doesn't retroactively alter cards
    score_snapshot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    breakdown_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Merge provenance: preserves original vessel context when gap events are reassigned
    original_vessel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    original_mmsi: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)
    # Chain-of-custody fields
    exported_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approval_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="draft")
    approval_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    gap_event: Mapped["AISGapEvent"] = relationship("AISGapEvent", back_populates="evidence_cards")
