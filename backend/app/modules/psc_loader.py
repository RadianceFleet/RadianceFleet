"""PSC detention data loaders — OpenSanctions FTM JSON and EMSA ban API.

Parses FollowTheMoney entity JSON from OpenSanctions (Tokyo, Black Sea, Abuja MOUs)
and EMSA ship ban JSON (Paris MOU). Matches vessels by IMO number (primary) or
fuzzy name (fallback). Sets psc_detained_last_12m on matching Vessel records.

Coverage limitations:
  - Paris MOU: bans only (~136 vessels), not full detentions (~1,200/year)
  - Indian Ocean, Mediterranean, Riyadh, Vina del Mar MOUs: not available
  - THETIS has no public bulk API for European PSC data
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_IMO_PATTERN = re.compile(r"IMO\s*(\d{7})")


def _extract_imo_from_ftm(entity: dict) -> str | None:
    """Extract IMO number from FTM entity properties."""
    props = entity.get("properties", {})

    # Direct imoNumber property (preferred)
    imo_list = props.get("imoNumber", [])
    if imo_list:
        return str(imo_list[0]).strip()

    # Fallback: search identifiers for IMO pattern
    for ident in props.get("idNumber", []) + props.get("registrationNumber", []):
        m = _IMO_PATTERN.search(str(ident))
        if m:
            return m.group(1)

    return None


def _extract_date_from_ftm(entity: dict) -> date | None:
    """Extract most recent date from FTM entity."""
    props = entity.get("properties", {})
    dates = props.get("date", []) + props.get("startDate", [])
    parsed = []
    for d in dates:
        try:
            parsed.append(date.fromisoformat(str(d)[:10]))
        except (ValueError, TypeError):
            continue
    return max(parsed) if parsed else None


def _extract_name_from_ftm(entity: dict) -> str | None:
    """Extract vessel name from FTM entity."""
    props = entity.get("properties", {})
    names = props.get("name", [])
    return str(names[0]).strip() if names else None


def load_psc_ftm(
    db: Session,
    json_path: str | Path,
    source: str = "unknown_mou",
    recency_days: int = 365,
) -> dict[str, int]:
    """Parse FTM JSON file and set psc_detained_last_12m on matching vessels.

    Args:
        db: SQLAlchemy session.
        json_path: Path to FTM JSON file (one JSON object per line, or JSON array).
        source: Source label for logging (e.g. "tokyo_mou").
        recency_days: Only flag detentions within this many days.

    Returns:
        {"total": int, "matched": int, "recent": int, "skipped": int}
    """
    from app.models.vessel import Vessel

    path = Path(json_path)
    entities: list[dict] = []

    # FTM files can be newline-delimited JSON or a JSON array
    content = path.read_text(encoding="utf-8").strip()
    if content.startswith("["):
        entities = json.loads(content)
    else:
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    entities.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    cutoff = date.today() - timedelta(days=recency_days)
    stats = {"total": 0, "matched": 0, "recent": 0, "skipped": 0}

    for entity in entities:
        # Only process Vessel/Ship schema entities
        schema = entity.get("schema", "")
        if schema not in ("Vessel", "Ship", "Thing", "LegalEntity", ""):
            continue

        stats["total"] += 1

        imo = _extract_imo_from_ftm(entity)
        detention_date = _extract_date_from_ftm(entity)

        # Skip old detentions
        if detention_date and detention_date < cutoff:
            stats["skipped"] += 1
            continue

        vessel = None
        if imo:
            vessel = db.query(Vessel).filter(Vessel.imo == imo).first()

        if vessel is None:
            name = _extract_name_from_ftm(entity)
            if name:
                # Fuzzy name match: exact case-insensitive match only
                vessel = (
                    db.query(Vessel)
                    .filter(Vessel.name.ilike(name))
                    .first()
                )

        if vessel is None:
            stats["skipped"] += 1
            continue

        stats["matched"] += 1
        if not vessel.psc_detained_last_12m:
            vessel.psc_detained_last_12m = True
            stats["recent"] += 1

    db.commit()
    logger.info("PSC FTM (%s): %s", source, stats)
    return stats


def load_emsa_bans(
    db: Session,
    json_path: str | Path,
) -> dict[str, int]:
    """Parse EMSA ban API JSON and set psc_detained_last_12m on matching vessels.

    EMSA fields: imoNumber, shipName, banDate, banningAuthority, banReason, flag, ismCompany.

    Returns:
        {"total": int, "matched": int, "flagged": int}
    """
    from app.models.vessel import Vessel

    path = Path(json_path)
    data = json.loads(path.read_text(encoding="utf-8"))

    # EMSA API may wrap in a root object or return a flat array
    if isinstance(data, dict):
        ships = data.get("data", data.get("ships", data.get("results", [])))
        if not isinstance(ships, list):
            ships = [data]
    elif isinstance(data, list):
        ships = data
    else:
        logger.warning("EMSA ban file has unexpected format")
        return {"total": 0, "matched": 0, "flagged": 0}

    cutoff = date.today() - timedelta(days=365)
    stats = {"total": 0, "matched": 0, "flagged": 0}

    for ship in ships:
        if not isinstance(ship, dict):
            continue
        stats["total"] += 1

        imo = ship.get("imoNumber") or ship.get("imo")
        ban_date_str = ship.get("banDate") or ship.get("date")

        # Parse ban date for recency check
        ban_date = None
        if ban_date_str:
            try:
                ban_date = date.fromisoformat(str(ban_date_str)[:10])
            except (ValueError, TypeError):
                pass

        if ban_date and ban_date < cutoff:
            continue

        vessel = None
        if imo:
            vessel = db.query(Vessel).filter(Vessel.imo == str(imo)).first()

        if vessel is None:
            name = ship.get("shipName") or ship.get("name")
            if name:
                vessel = db.query(Vessel).filter(Vessel.name.ilike(str(name).strip())).first()

        if vessel is None:
            continue

        stats["matched"] += 1
        if not vessel.psc_detained_last_12m:
            vessel.psc_detained_last_12m = True
            stats["flagged"] += 1

    db.commit()
    logger.info("EMSA bans: %s", stats)
    return stats
