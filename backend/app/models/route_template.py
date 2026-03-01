"""RouteTemplate entity — common vessel trade route patterns."""
from __future__ import annotations

import datetime

from sqlalchemy import Column, Integer, String, DateTime, Float, JSON
from app.models.base import Base


class RouteTemplate(Base):
    __tablename__ = "route_templates"

    template_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_type = Column(String(100))
    route_ports_json = Column(JSON)  # ordered list of port IDs
    frequency = Column(Integer)  # how many vessels follow this route
    avg_duration_days = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
