"""Pydantic schemas for Vessel entity — used by FastAPI for request/response typing."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class VesselBase(BaseModel):
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag: str | None = None
    vessel_type: str | None = None
    deadweight: float | None = None
    year_built: int | None = None
    ais_class: str | None = None
    flag_risk_category: str | None = None
    pi_coverage_status: str | None = None
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
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
