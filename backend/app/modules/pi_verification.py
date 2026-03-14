"""P&I Club Insurance Verification via Equasis.

Binary insurance signal: vessel with no identifiable P&I coverage from a
recognised International Group (IG) club = elevated risk.

Gated by EQUASIS_SCRAPING_ENABLED (disabled by default, ToS-sensitive).
When disabled, check_pi_coverage() returns None and scoring signals are
silently skipped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# The 12 member clubs of the International Group of P&I Clubs.
# Used for case-insensitive partial matching against Equasis data.
IG_PI_CLUBS: list[str] = [
    "American",
    "Britannia",
    "Gard",
    "Japan",
    "London",
    "North",
    "Shipowners",
    "Skuld",
    "Standard",
    "Steamship",
    "Sweden",
    "West",
]


def _is_ig_club(club_name: str) -> bool:
    """Check if *club_name* matches any IG P&I club (case-insensitive partial match)."""
    club_lower = club_name.lower()
    return any(ig.lower() in club_lower for ig in IG_PI_CLUBS)


def check_pi_coverage(db: Session, vessel: Vessel) -> dict | None:  # noqa: ARG001 — db reserved for future caching
    """Query Equasis for P&I club coverage of *vessel*.

    Returns
    -------
    dict
        ``{"found": bool, "club_name": str | None, "source": "equasis"}``
        *found* is True only when a recognised IG P&I club is listed.
    None
        When Equasis scraping is disabled, the vessel has no IMO, or the
        Equasis query fails.
    """
    if not settings.EQUASIS_SCRAPING_ENABLED:
        return None

    imo = getattr(vessel, "imo", None)
    if not imo:
        return None

    try:
        from app.modules.equasis_client import EquasisClient

        client = EquasisClient()
        pi_info = client.get_pi_info(str(imo))
    except RuntimeError:
        # EquasisClient raises RuntimeError when disabled or misconfigured
        return None
    except Exception:
        logger.warning("Equasis P&I lookup failed for IMO %s", imo, exc_info=True)
        return None

    if pi_info is None:
        return {"found": False, "club_name": None, "source": "equasis"}

    club_name = pi_info.get("club_name")
    if not club_name:
        return {"found": False, "club_name": None, "source": "equasis"}

    found = _is_ig_club(club_name)
    return {"found": found, "club_name": club_name, "source": "equasis"}
