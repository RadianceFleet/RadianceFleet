"""Pydantic schemas for vessel detail and search responses."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class VesselHistoryRead(BaseModel):
    vessel_history_id: int
    field_changed: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    observed_at: datetime
    source: Optional[str] = None

    model_config = {"from_attributes": True}


class WatchlistEntryRead(BaseModel):
    watchlist_entry_id: int
    watchlist_source: str
    reason: Optional[str] = None
    date_listed: Optional[Any] = None
    is_active: bool

    model_config = {"from_attributes": True}


class VesselSearchResult(BaseModel):
    vessel_id: int
    mmsi: str
    imo: Optional[str] = None
    name: Optional[str] = None
    flag: Optional[str] = None
    vessel_type: Optional[str] = None
    deadweight: Optional[float] = None
    last_risk_score: Optional[int] = None
    watchlist_status: bool = False

    model_config = {"from_attributes": True}


class VesselDetailRead(BaseModel):
    vessel_id: int
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
    mmsi_first_seen_utc: Optional[datetime] = None
    vessel_laid_up_30d: bool = False
    vessel_laid_up_60d: bool = False
    vessel_laid_up_in_sts_zone: bool = False
    merged_into_vessel_id: Optional[int] = None
    watchlist_entries: list[WatchlistEntryRead] = []
    total_gaps_7d: int = 0
    total_gaps_30d: int = 0

    model_config = {"from_attributes": True}


class IngestionStatusRead(BaseModel):
    status: str  # idle | running | completed | failed
    file_name: Optional[str] = None
    processed: int = 0
    accepted: int = 0
    rejected: int = 0
    percent_complete: Optional[float] = None
    error: Optional[str] = None
