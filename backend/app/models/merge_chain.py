"""MergeChain — tracks connected components of vessel identity merges."""
from __future__ import annotations

import datetime

from sqlalchemy import Column, Integer, String, DateTime, Float, JSON
from app.models.base import Base


class MergeChain(Base):
    __tablename__ = "merge_chains"

    chain_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_ids_json = Column(JSON)  # list of vessel IDs in chain order
    links_json = Column(JSON)  # list of MergeCandidate PKs for each link
    chain_length = Column(Integer)  # number of vessels (hops + 1)
    confidence = Column(Float)  # min(link scores) -- weakest link
    confidence_band = Column(String(20))  # HIGH/MEDIUM/LOW
    created_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    evidence_json = Column(JSON)  # optional summary
