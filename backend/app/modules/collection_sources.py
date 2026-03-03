"""AIS collection source wrappers with session management.

Provides a unified interface for all AIS data sources used by both
the CLI collect command and the CollectionScheduler.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SourceInfo:
    """Metadata for an AIS collection source."""
    name: str
    description: str
    interval_seconds: int
    enabled: bool
    collector: Callable[[Session, int], dict]


def _collect_digitraffic(db: Session, duration_seconds: int = 300) -> dict:
    """Collect from Digitraffic (Finnish AIS)."""
    from app.modules.digitraffic_client import fetch_digitraffic_ais
    return fetch_digitraffic_ais(db)


def _collect_kystverket(db: Session, duration_seconds: int = 300) -> dict:
    """Collect from Kystverket (Norwegian AIS TCP stream)."""
    from app.modules.kystverket_client import stream_kystverket
    return stream_kystverket(db, duration_seconds=duration_seconds)


def _collect_barentswatch(db: Session, duration_seconds: int = 300) -> dict:
    """Collect from BarentsWatch (Norwegian EEZ REST API)."""
    from app.modules.barentswatch_client import fetch_barentswatch_tracks
    return fetch_barentswatch_tracks(db)


def _collect_aisstream(db: Session, duration_seconds: int = 300) -> dict:
    """Collect from aisstream.io WebSocket."""
    from app.modules.aisstream_client import stream_aisstream
    return stream_aisstream(db, duration_seconds=duration_seconds)


def _collect_dma(db: Session, duration_seconds: int = 300) -> dict:
    """Collect from DMA (Danish Maritime Authority daily CSV archives)."""
    from datetime import date, timedelta
    from app.modules.dma_client import fetch_and_import_dma
    yesterday = date.today() - timedelta(days=1)
    return fetch_and_import_dma(db, start_date=yesterday, end_date=yesterday)


# Registry of all known sources
_SOURCE_REGISTRY: dict[str, Callable[[], SourceInfo]] = {
    "digitraffic": lambda: SourceInfo(
        name="digitraffic",
        description="Finnish AIS (Baltic Sea)",
        interval_seconds=getattr(settings, "COLLECT_DIGITRAFFIC_INTERVAL", 1800),
        enabled=getattr(settings, "DIGITRAFFIC_ENABLED", False),
        collector=_collect_digitraffic,
    ),
    "kystverket": lambda: SourceInfo(
        name="kystverket",
        description="Norwegian AIS TCP stream (Barents/Norwegian Sea)",
        interval_seconds=getattr(settings, "COLLECT_AISSTREAM_INTERVAL", 300),
        enabled=getattr(settings, "KYSTVERKET_ENABLED", False),
        collector=_collect_kystverket,
    ),
    "barentswatch": lambda: SourceInfo(
        name="barentswatch",
        description="Norwegian EEZ REST API (Murmansk corridor)",
        interval_seconds=getattr(settings, "COLLECT_DIGITRAFFIC_INTERVAL", 1800),
        enabled=getattr(settings, "BARENTSWATCH_ENABLED", False),
        collector=_collect_barentswatch,
    ),
    "aisstream": lambda: SourceInfo(
        name="aisstream",
        description="aisstream.io WebSocket (global corridors)",
        interval_seconds=getattr(settings, "COLLECT_AISSTREAM_INTERVAL", 300),
        enabled=bool(getattr(settings, "AISSTREAM_API_KEY", None)),
        collector=_collect_aisstream,
    ),
    "dma": lambda: SourceInfo(
        name="dma",
        description="Danish Maritime Authority (Danish Straits daily CSV)",
        interval_seconds=86400,
        enabled=getattr(settings, "DMA_ENABLED", False),
        collector=_collect_dma,
    ),
}


def get_available_sources() -> dict[str, SourceInfo]:
    """Return dict of source_name -> SourceInfo for all enabled sources."""
    result = {}
    for name, factory in _SOURCE_REGISTRY.items():
        info = factory()
        if info.enabled:
            result[name] = info
    return result


def get_all_sources() -> dict[str, SourceInfo]:
    """Return dict of source_name -> SourceInfo for ALL sources (including disabled)."""
    return {name: factory() for name, factory in _SOURCE_REGISTRY.items()}


def collect_from_source(source_name: str, db: Session, duration_seconds: int = 300) -> dict:
    """Run collection for a single source. Returns stats dict.

    Raises:
        ValueError: If source_name is unknown.
    """
    if source_name not in _SOURCE_REGISTRY:
        raise ValueError(
            f"Unknown source: {source_name}. "
            f"Available: {', '.join(sorted(_SOURCE_REGISTRY.keys()))}"
        )
    info = _SOURCE_REGISTRY[source_name]()
    if not info.enabled:
        logger.info("Source %s is disabled, skipping", source_name)
        return {"points_imported": 0, "vessels_seen": 0, "errors": 0, "skipped": True}

    logger.info("Collecting from %s (%s)", source_name, info.description)
    return info.collector(db, duration_seconds)
