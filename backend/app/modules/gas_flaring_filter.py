"""Gas flaring platform filter for VIIRS detections.

Loads known gas flaring platform coordinates from YAML config and
excludes VIIRS detections that fall within a configurable radius
of any known platform.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)


def load_flaring_platforms(config_path: str | None = None) -> list[dict[str, Any]]:
    """Load gas flaring platform locations from YAML config.

    Args:
        config_path: Path to gas_flaring_platforms.yaml. If None, uses default.

    Returns:
        List of platform dicts with 'name', 'lat', 'lon' keys.
    """
    if config_path is None:
        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "config"
            / "gas_flaring_platforms.yaml"
        )

    path = Path(config_path)
    if not path.exists():
        logger.warning("Gas flaring platforms config not found at %s", path)
        return []

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    platforms = data.get("platforms", [])
    valid = []
    for p in platforms:
        if "lat" in p and "lon" in p:
            valid.append(p)
        else:
            logger.warning("Skipping platform entry without lat/lon: %s", p)

    logger.info("Loaded %d gas flaring platforms", len(valid))
    return valid


def is_near_flaring_platform(
    lat: float,
    lon: float,
    platforms: list[dict[str, Any]],
    radius_nm: float = 5.0,
) -> bool:
    """Check if a position is within radius of any known flaring platform.

    Args:
        lat: Detection latitude.
        lon: Detection longitude.
        platforms: List of platform dicts from load_flaring_platforms.
        radius_nm: Exclusion radius in nautical miles.

    Returns:
        True if the position is near a flaring platform.
    """
    for platform in platforms:
        dist = haversine_nm(lat, lon, platform["lat"], platform["lon"])
        if dist <= radius_nm:
            return True
    return False


def filter_flaring(
    detections: list[dict[str, Any]],
    platforms: list[dict[str, Any]],
    radius_nm: float = 5.0,
) -> list[dict[str, Any]]:
    """Filter out detections near known gas flaring platforms.

    Args:
        detections: List of detection dicts with 'lat' and 'lon' keys.
        platforms: List of platform dicts from load_flaring_platforms.
        radius_nm: Exclusion radius in nautical miles.

    Returns:
        Filtered list of detections (those NOT near flaring platforms).
    """
    if not platforms:
        return detections

    filtered = []
    excluded = 0
    for det in detections:
        if is_near_flaring_platform(det["lat"], det["lon"], platforms, radius_nm):
            excluded += 1
        else:
            filtered.append(det)

    if excluded > 0:
        logger.info(
            "Gas flaring filter: excluded %d of %d detections (radius=%.1f nm)",
            excluded,
            len(detections),
            radius_nm,
        )
    return filtered
