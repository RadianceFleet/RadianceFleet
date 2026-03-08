"""Satellite imagery provider registry."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.satellite_providers.base import SatelliteProvider

_PROVIDERS: dict[str, type["SatelliteProvider"]] = {}


def register_provider(name: str, cls: type["SatelliteProvider"]) -> None:
    _PROVIDERS[name] = cls


def get_provider(name: str) -> type["SatelliteProvider"]:
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown satellite provider: {name}. Available: {list(_PROVIDERS.keys())}")
    return _PROVIDERS[name]


def list_providers() -> list[str]:
    return list(_PROVIDERS.keys())


# Eagerly import providers so they self-register
try:
    from app.modules.satellite_providers import planet_client  # noqa: F401
    from app.modules.satellite_providers import capella_client  # noqa: F401
    from app.modules.satellite_providers import maxar_client  # noqa: F401
    from app.modules.satellite_providers import umbra_client  # noqa: F401
except ImportError:
    pass  # Optional providers — missing API key raises ValueError at construction time
