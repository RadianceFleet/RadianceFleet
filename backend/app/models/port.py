"""Port entity — major ports for anchor spoof validation."""
from __future__ import annotations

from sqlalchemy import Integer, String, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geometry
from app.models.base import Base


class Port(Base):
    __tablename__ = "ports"
    __table_args__ = (
        Index("ix_ports_geometry", "geometry", postgresql_using="gist"),
    )

    port_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=False)
    geometry: Mapped[object] = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    major_port: Mapped[bool] = mapped_column(Boolean, default=True)
    is_eu: Mapped[bool] = mapped_column(Boolean, default=False)
    is_russian_oil_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
