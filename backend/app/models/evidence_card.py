"""EvidenceCard entity — exported investigation record."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class EvidenceCard(Base):
    __tablename__ = "evidence_cards"

    evidence_card_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_event_id: Mapped[int] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    export_format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    export_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    gap_event: Mapped["AISGapEvent"] = relationship("AISGapEvent", back_populates="evidence_cards")
