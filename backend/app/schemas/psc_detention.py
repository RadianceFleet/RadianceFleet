"""Pydantic schemas for PSC detention records."""
from pydantic import BaseModel
from datetime import date
from typing import Optional


class PscDetentionRead(BaseModel):
    psc_detention_id: int
    detention_date: date
    release_date: Optional[date] = None
    port_name: Optional[str] = None
    port_country: Optional[str] = None
    mou_source: str
    deficiency_count: int = 0
    major_deficiency_count: int = 0
    detention_reason: Optional[str] = None
    authority_name: Optional[str] = None
    flag_at_detention: Optional[str] = None
    data_source: str = ""

    model_config = {"from_attributes": True}
