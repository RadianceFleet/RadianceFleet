"""Pydantic schemas for the explainability framework."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignalExplanation(BaseModel):
    """A single signal from the risk breakdown with human-readable explanation."""

    key: str = Field(description="Original risk breakdown key")
    value: float = Field(description="Point contribution of this signal")
    explanation: str = Field(description="Human-readable explanation of the signal")
    category: str = Field(description="Evidence category (behavioral, spatial, temporal, identity, sanctions, environmental)")
    tier: int = Field(description="Template tier: 1=hand-written, 2=pattern-matched, 3=fallback")


class WaterfallEntry(BaseModel):
    """A single entry in the waterfall (cumulative contribution) chart."""

    label: str = Field(description="Display label for the signal")
    value: float = Field(description="Individual contribution (+/-)")
    cumulative: float = Field(description="Running total after this entry")
    is_multiplier: bool = Field(default=False, description="True if this is a multiplier effect rather than additive")


class ExplainabilityResponse(BaseModel):
    """Full explainability response for an alert."""

    alert_id: int = Field(description="The gap_event_id that was explained")
    total_score: float = Field(description="Total risk score for this alert")
    signals: list[SignalExplanation] = Field(default_factory=list, description="All signals with explanations")
    waterfall: list[WaterfallEntry] = Field(default_factory=list, description="Waterfall chart data")
    categories: dict[str, list[SignalExplanation]] = Field(default_factory=dict, description="Signals grouped by category")
    summary: str = Field(default="", description="One-paragraph executive summary")
