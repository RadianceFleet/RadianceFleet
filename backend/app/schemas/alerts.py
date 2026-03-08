"""Pydantic schemas for alert and watchlist operations."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BulkStatusUpdateRequest(BaseModel):
    alert_ids: list[int] = Field(..., min_length=1)
    status: str = Field(...)


class WatchlistAddRequest(BaseModel):
    vessel_id: int | None = None
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None
    source: str = Field(default="manual")
    reason: str | None = None
    watchlist_source: str | None = None


class NoteAddRequest(BaseModel):
    notes: str | None = None
    text: str | None = None  # legacy key
