"""Abstract base class for satellite imagery providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ArchiveSearchResult:
    scene_id: str
    provider: str
    acquired_at: datetime
    cloud_cover_pct: float | None = None
    resolution_m: float | None = None
    thumbnail_url: str | None = None
    geometry_wkt: str | None = None
    product_type: str | None = None
    estimated_cost_usd: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class OrderSubmitResult:
    external_order_id: str
    status: str
    estimated_cost_usd: float | None = None
    message: str | None = None


@dataclass
class OrderStatusResult:
    external_order_id: str
    status: str  # accepted/processing/delivered/failed/cancelled
    scene_urls: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    message: str | None = None
    metadata: dict = field(default_factory=dict)


class SatelliteProvider(ABC):
    """Abstract satellite imagery provider."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def search_archive(
        self,
        aoi_wkt: str,
        start: datetime,
        end: datetime,
        cloud_cover_max: float = 30.0,
        limit: int = 10,
    ) -> list[ArchiveSearchResult]: ...

    @abstractmethod
    def submit_order(
        self, scene_ids: list[str], product_type: str = "analytic"
    ) -> OrderSubmitResult: ...

    @abstractmethod
    def check_order_status(self, external_order_id: str) -> OrderStatusResult: ...

    @abstractmethod
    def cancel_order(self, external_order_id: str) -> bool: ...

    @abstractmethod
    def estimated_cost_per_scene(self, product_type: str = "analytic") -> float: ...
