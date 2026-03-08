"""Pydantic schemas for PSC detention records."""

from datetime import date

from pydantic import BaseModel


class PscDetentionRead(BaseModel):
    psc_detention_id: int
    detention_date: date
    release_date: date | None = None
    port_name: str | None = None
    port_country: str | None = None
    mou_source: str
    deficiency_count: int = 0
    major_deficiency_count: int = 0
    detention_reason: str | None = None
    authority_name: str | None = None
    flag_at_detention: str | None = None
    data_source: str = ""

    model_config = {"from_attributes": True}
