"""PSC Detention records — detailed port state control detention history."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PscDetention(Base):
    __tablename__ = "psc_detentions"
    __table_args__ = (
        UniqueConstraint(
            "vessel_id", "detention_date", "mou_source", "raw_entity_id",
            name="uq_psc_detention_vessel_date_source",
        ),
    )

    psc_detention_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    detention_date: Mapped[date] = mapped_column(Date, nullable=False)
    release_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    port_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    port_country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    mou_source: Mapped[str] = mapped_column(String(50), nullable=False)  # tokyo_mou, paris_mou, black_sea_mou, abuja_mou
    data_source: Mapped[str] = mapped_column(String(50), nullable=False)  # opensanctions_ftm, emsa_ban_api
    deficiency_count: Mapped[int] = mapped_column(Integer, default=0)
    major_deficiency_count: Mapped[int] = mapped_column(Integer, default=0)
    detention_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ban_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    authority_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    imo_at_detention: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    vessel_name_at_detention: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    flag_at_detention: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    raw_entity_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="psc_detentions")
