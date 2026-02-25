"""Pydantic schemas for Vessel entity — used by FastAPI for request/response typing."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class VesselBase(BaseModel):
    mmsi: str
    imo: Optional[str] = None
    name: Optional[str] = None
    flag: Optional[str] = None
    vessel_type: Optional[str] = None
    deadweight: Optional[float] = None
    year_built: Optional[int] = None
    ais_class: Optional[str] = None
    flag_risk_category: Optional[str] = None
    pi_coverage_status: Optional[str] = None
    psc_detained_last_12m: bool = False
    psc_major_deficiencies_last_12m: int = 0

    @field_validator("mmsi")
    @classmethod
    def mmsi_must_be_9_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 9:
            raise ValueError("MMSI must be exactly 9 digits")
        return v


class VesselCreate(VesselBase):
    pass


class VesselRead(VesselBase):
    vessel_id: int
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
