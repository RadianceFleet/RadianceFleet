"""PSC detention data loaders — OpenSanctions FTM JSON and EMSA ban API.

Parses FollowTheMoney entity JSON from OpenSanctions (Tokyo, Black Sea, Abuja MOUs)
and EMSA ship ban JSON (Paris MOU). Matches vessels by IMO number (primary) or
fuzzy name (fallback). Sets psc_detained_last_12m on matching Vessel records.

Coverage limitations:
  - Paris MOU: bans only (~136 vessels), not full detentions (~1,200/year)
  - THETIS has no public bulk API for European PSC data

Additional MOU research (2026-03):
  - Mediterranean MOU (MedMOU): uses THETIS-Med (EMSA). Bulk download strictly
    forbidden per their terms. No public API. 13 member states, ~6,000 inspections/year.
  - Indian Ocean MOU (IOMOU): public search form at iomou.org/php/InspData.php
    but no bulk download or structured API. ~5,800 inspections/year.
  - Riyadh MOU: annual PDF reports only. No public inspection database or API.
  - Vina del Mar Agreement (Latin America): no public data API. 14 member states,
    annual reports only.

If any of these MOUs publish structured data in the future, add a loader
following the load_psc_ftm() or load_emsa_bans() patterns above.
"""

from __future__ import annotations

import contextlib
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


def _upsert_detention(db: Session, vessel: Any, data: dict) -> bool:
    """Create a PscDetention record if no duplicate exists.

    Args:
        db: SQLAlchemy session.
        vessel: Vessel ORM instance.
        data: Dict with keys matching PscDetention columns
              (detention_date, mou_source, data_source, raw_entity_id, etc.).

    Returns:
        True if a new record was created, False if duplicate skipped.
    """
    from app.models.psc_detention import PscDetention

    detention_date = data.get("detention_date")
    mou_source = data.get("mou_source", "unknown")
    raw_entity_id = data.get("raw_entity_id")

    # Check for existing duplicate
    existing = (
        db.query(PscDetention)
        .filter(
            PscDetention.vessel_id == vessel.vessel_id,
            PscDetention.detention_date == detention_date,
            PscDetention.mou_source == mou_source,
            PscDetention.raw_entity_id == raw_entity_id,
        )
        .first()
    )
    if existing:
        return False

    record = PscDetention(
        vessel_id=vessel.vessel_id,
        detention_date=data.get("detention_date"),
        release_date=data.get("release_date"),
        port_name=data.get("port_name"),
        port_country=data.get("port_country"),
        mou_source=mou_source,
        data_source=data.get("data_source", "unknown"),
        deficiency_count=data.get("deficiency_count", 0),
        major_deficiency_count=data.get("major_deficiency_count", 0),
        detention_reason=data.get("detention_reason"),
        ban_type=data.get("ban_type"),
        authority_name=data.get("authority_name"),
        imo_at_detention=data.get("imo_at_detention"),
        vessel_name_at_detention=data.get("vessel_name_at_detention"),
        flag_at_detention=data.get("flag_at_detention"),
        raw_entity_id=raw_entity_id,
    )
    db.add(record)
    return True


def sync_vessel_psc_summary(db: Session, vessel: Any) -> None:
    """Recompute vessel boolean PSC flags from PscDetention records.

    Sets psc_detained_last_12m and psc_major_deficiencies_last_12m based on
    detention records within the last 12 months.
    """
    from app.models.psc_detention import PscDetention

    cutoff = date.today() - timedelta(days=365)

    recent_detentions = (
        db.query(PscDetention)
        .filter(
            PscDetention.vessel_id == vessel.vessel_id,
            PscDetention.detention_date >= cutoff,
        )
        .all()
    )

    vessel.psc_detained_last_12m = len(recent_detentions) > 0
    total_major = sum(d.major_deficiency_count for d in recent_detentions)
    vessel.psc_major_deficiencies_last_12m = total_major


def load_psc_ftm(
    db: Session,
    json_path: str | Path,
    source: str = "unknown_mou",
    recency_days: int = 365,
) -> dict[str, int]:
    """Parse FTM JSON file and set psc_detained_last_12m on matching vessels.

    Also creates PscDetention records for matched vessels.

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
                vessel = db.query(Vessel).filter(Vessel.name.ilike(name)).first()

        if vessel is None:
            stats["skipped"] += 1
            continue

        stats["matched"] += 1

        # Create detailed detention record
        entity_id = entity.get("id")
        ftm_name = _extract_name_from_ftm(entity)
        props = entity.get("properties", {})
        flag_list = props.get("flag", [])

        detention_data = {
            "detention_date": detention_date or date.today(),
            "mou_source": source,
            "data_source": "opensanctions_ftm",
            "raw_entity_id": entity_id,
            "imo_at_detention": imo,
            "vessel_name_at_detention": ftm_name,
            "flag_at_detention": str(flag_list[0]) if flag_list else None,
        }
        _upsert_detention(db, vessel, detention_data)

        # Sync boolean flags from detention records
        sync_vessel_psc_summary(db, vessel)

        if vessel.psc_detained_last_12m:
            stats["recent"] += 1

    db.commit()
    logger.info("PSC FTM (%s): %s", source, stats)
    return stats


def load_emsa_bans(
    db: Session,
    json_path: str | Path,
) -> dict[str, int]:
    """Parse EMSA ban API JSON and set psc_detained_last_12m on matching vessels.

    Also creates PscDetention records for matched vessels.

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
            with contextlib.suppress(ValueError, TypeError):
                ban_date = date.fromisoformat(str(ban_date_str)[:10])

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

        # Create detailed detention record
        detention_data = {
            "detention_date": ban_date or date.today(),
            "mou_source": "paris_mou",
            "data_source": "emsa_ban_api",
            "raw_entity_id": str(imo) if imo else None,
            "imo_at_detention": str(imo) if imo else None,
            "vessel_name_at_detention": ship.get("shipName") or ship.get("name"),
            "flag_at_detention": ship.get("flag"),
            "authority_name": ship.get("banningAuthority"),
            "detention_reason": ship.get("banReason"),
            "ban_type": ship.get("banType"),
        }
        _upsert_detention(db, vessel, detention_data)

        # Sync boolean flags from detention records
        sync_vessel_psc_summary(db, vessel)

        if vessel.psc_detained_last_12m:
            stats["flagged"] += 1

    db.commit()
    logger.info("EMSA bans: %s", stats)
    return stats
