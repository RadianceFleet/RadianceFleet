"""Pydantic schemas for vessel detail and search responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class VesselHistoryRead(BaseModel):
    vessel_history_id: int
    field_changed: str
    old_value: str | None = None
    new_value: str | None = None
    observed_at: datetime
    source: str | None = None

    model_config = {"from_attributes": True}


class WatchlistEntryRead(BaseModel):
    watchlist_entry_id: int
    watchlist_source: str
    reason: str | None = None
    date_listed: Any | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class VesselSearchResult(BaseModel):
    vessel_id: int
    mmsi: str
    imo: str | None = None
    name: str | None = None
    flag: str | None = None
    vessel_type: str | None = None
    deadweight: float | None = None
    last_risk_score: int | None = None
    watchlist_status: bool = False
    watchlist_stub_score: int | None = None
    effective_score: int | None = None  # last_risk_score if not None, else watchlist_stub_score

    model_config = {"from_attributes": True}


class VesselDetailRead(BaseModel):
    vessel_id: int
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
    mmsi_first_seen_utc: datetime | None = None
    vessel_laid_up_30d: bool = False
    vessel_laid_up_60d: bool = False
    vessel_laid_up_in_sts_zone: bool = False
    merged_into_vessel_id: int | None = None
    watchlist_entries: list[WatchlistEntryRead] = []
    total_gaps_7d: int = 0
    total_gaps_30d: int = 0
    watchlist_stub_score: int | None = None
    watchlist_stub_breakdown: dict | None = None
    callsign: str | None = None
    owner_name: str | None = None
    ais_cargo_type: str | None = None
    last_ais_received_utc: datetime | None = None

    model_config = {"from_attributes": True}


class IngestionStatusRead(BaseModel):
    status: str  # idle | running | completed | failed
    file_name: str | None = None
    processed: int = 0
    accepted: int = 0
    rejected: int = 0
    percent_complete: float | None = None
    error: str | None = None
