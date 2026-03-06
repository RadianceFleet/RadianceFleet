"""Merge chain BFS detection — connected-component analysis over confirmed merges.

The core BFS algorithm lives in identity_resolver.detect_merge_chains().
This module provides a public API surface and convenience wrappers.
"""
from __future__ import annotations

import logging
from sqlalchemy.orm import Session

from app.models.merge_chain import MergeChain

logger = logging.getLogger(__name__)


def detect_merge_chains(db: Session) -> dict:
    """Find connected components of merged vessels using BFS.

    Delegates to identity_resolver.detect_merge_chains which:
      1. Queries confirmed merges (AUTO_MERGED / ANALYST_MERGED, confidence >= 50)
      2. Builds adjacency list
      3. BFS to find connected components
      4. Creates MergeChain records for components with >= 3 vessels

    Returns dict with chains_created, chains_by_band, etc.
    """
    from app.modules.identity_resolver import detect_merge_chains as _detect
    return _detect(db)


def get_merge_chains(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 50,
    min_confidence: float | None = None,
    confidence_band: str | None = None,
) -> list[MergeChain]:
    """Query stored merge chains with optional filters and pagination."""
    query = db.query(MergeChain)

    if min_confidence is not None:
        query = query.filter(MergeChain.confidence >= min_confidence)
    if confidence_band is not None:
        query = query.filter(MergeChain.confidence_band == confidence_band.upper())

    return (
        query
        .order_by(MergeChain.chain_id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_merge_chain_count(
    db: Session,
    *,
    min_confidence: float | None = None,
    confidence_band: str | None = None,
) -> int:
    """Count merge chains with optional filters."""
    query = db.query(MergeChain)
    if min_confidence is not None:
        query = query.filter(MergeChain.confidence >= min_confidence)
    if confidence_band is not None:
        query = query.filter(MergeChain.confidence_band == confidence_band.upper())
    return query.count()


def serialize_merge_chain(chain: MergeChain) -> dict:
    """Convert a MergeChain ORM object to a JSON-serializable dict."""
    return {
        "chain_id": chain.chain_id,
        "vessel_ids": chain.vessel_ids_json or [],
        "links": chain.links_json or [],
        "chain_length": chain.chain_length,
        "confidence": chain.confidence,
        "confidence_band": chain.confidence_band,
        "created_at": chain.created_at.isoformat() if chain.created_at else None,
        "evidence": chain.evidence_json or {},
    }
