"""Pydantic schemas for signal-corridor FP rate matrix."""

from __future__ import annotations

from pydantic import BaseModel


class SignalCorridorCell(BaseModel):
    signal_name: str
    corridor_id: int
    corridor_name: str
    tp_count: int
    fp_count: int
    total: int
    fp_rate: float
    lift: float  # fp_rate for this (signal, corridor) vs global fp_rate for this signal


class SignalRegionCell(BaseModel):
    signal_name: str
    region_id: int
    region_name: str
    tp_count: int
    fp_count: int
    total: int
    fp_rate: float
    lift: float


class SuppressionCandidate(BaseModel):
    signal_name: str
    corridor_id: int
    corridor_name: str
    fp_rate: float
    total: int
    global_fp_rate: float  # signal's overall FP rate for comparison
    suggested_action: str  # e.g., "Reduce weight by 50%" or "Consider suppressing"
