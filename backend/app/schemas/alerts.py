"""Pydantic schemas for alert and watchlist operations."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class BulkStatusUpdateRequest(BaseModel):
    alert_ids: list[int] = Field(..., min_length=1)
    status: str = Field(...)


class WatchlistAddRequest(BaseModel):
    vessel_id: Optional[int] = None
    mmsi: Optional[str] = None
    imo: Optional[str] = None
    vessel_name: Optional[str] = None
    source: str = Field(default="manual")
    reason: Optional[str] = None
    watchlist_source: Optional[str] = None


class NoteAddRequest(BaseModel):
    notes: Optional[str] = None
    text: Optional[str] = None  # legacy key
