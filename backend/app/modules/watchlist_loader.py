"""Watchlist loader — ingests vessel sanctions lists into the database.

Supports three source formats:

  OFAC SDN CSV  — US Treasury Office of Foreign Assets Control
  KSE Institute — Kyiv School of Economics shadow-fleet tracker
  OpenSanctions — Open dataset aggregating multiple sanctions lists

Each loader returns a summary dict with ``matched``, ``unmatched``, and
(where applicable) ``skipped`` counts.  Unmatched vessel names are emitted
as warnings rather than exceptions so that a single bad row never aborts
a batch import.

Matching strategy (in priority order for all loaders):
  1. MMSI exact match (9-digit string)
  2. IMO exact match
  3. Fuzzy name match via rapidfuzz.fuzz.ratio at ≥ 85 % confidence
     (with optional flag pre-filter)

Before any insert the loaders check for an existing VesselWatchlist row
for the same (vessel_id, watchlist_source) and update is_active=True
instead of creating a duplicate.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import date
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.vessel import Vessel
from app.models.vessel_watchlist import VesselWatchlist

logger = logging.getLogger(__name__)

# Fuzzy match threshold (0-100).
from app.config import settings as _settings
_FUZZY_THRESHOLD: int = _settings.FUZZY_MATCH_THRESHOLD

# Compiled regex for MMSI validation (exactly 9 digits).
_MMSI_RE = re.compile(r"^\d{9}$")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_valid_mmsi(value: str) -> bool:
    """Return True if *value* looks like a 9-digit MMSI string."""
    return bool(value and _MMSI_RE.match(value.strip()))


def _upsert_watchlist(
    db: Session,
    vessel: Vessel,
    watchlist_source: str,
    reason: Optional[str] = None,
    date_listed: Optional[date] = None,
    source_url: Optional[str] = None,
) -> None:
    """Insert a VesselWatchlist row or re-activate an existing one."""
    existing = (
        db.query(VesselWatchlist)
        .filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == watchlist_source,
        )
        .first()
    )
    if existing:
        existing.is_active = True
        logger.debug(
            "Watchlist: re-activated existing entry for vessel_id=%d source=%s",
            vessel.vessel_id,
            watchlist_source,
        )
    else:
        entry = VesselWatchlist(
            vessel_id=vessel.vessel_id,
            watchlist_source=watchlist_source,
            reason=reason,
            date_listed=date_listed,
            source_url=source_url,
            is_active=True,
        )
        db.add(entry)
        logger.debug(
            "Watchlist: created entry for vessel_id=%d source=%s",
            vessel.vessel_id,
            watchlist_source,
        )


def _fuzzy_match_vessel(
    db: Session,
    name: str,
    flag: Optional[str] = None,
    threshold: int = _FUZZY_THRESHOLD,
) -> Optional[Vessel]:
    """Return the best-matching Vessel for *name* above *threshold*, or None.

    If *flag* is supplied, only vessels with an exact (case-insensitive) flag
    match are considered.  Uses rapidfuzz.fuzz.ratio for string similarity.

    Args:
        db: Active SQLAlchemy session.
        name: Vessel name to match against.
        flag: Optional two- or three-letter flag state code.
        threshold: Minimum similarity score (0-100) to accept a match.

    Returns:
        The best-matching Vessel, or None if no candidate meets the threshold.
    """
    if not name:
        return None

    query = db.query(Vessel).filter(Vessel.name.isnot(None))
    if flag:
        query = query.filter(Vessel.flag.ilike(flag.strip()))

    candidates = query.all()
    best_vessel: Optional[Vessel] = None
    best_score: float = 0.0

    for vessel in candidates:
        if not vessel.name:
            continue
        score = fuzz.ratio(name.upper(), vessel.name.upper())
        if score > best_score:
            best_score = score
            best_vessel = vessel

    if best_score >= threshold:
        logger.debug(
            "Fuzzy match: '%s' -> '%s' (score=%.1f)",
            name,
            best_vessel.name if best_vessel else None,
            best_score,
        )
        return best_vessel

    return None


def _resolve_vessel(
    db: Session,
    mmsi: Optional[str],
    imo: Optional[str],
    name: Optional[str],
    flag: Optional[str] = None,
) -> Optional[Vessel]:
    """Resolve a vessel using MMSI, IMO, then fuzzy name match.

    Returns the matched Vessel or None.
    """
    if mmsi and _is_valid_mmsi(mmsi):
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi.strip()).first()
        if vessel:
            return vessel

    if imo:
        vessel = db.query(Vessel).filter(Vessel.imo == imo.strip()).first()
        if vessel:
            return vessel

    if name:
        return _fuzzy_match_vessel(db, name, flag=flag)

    return None


# ── OFAC SDN loader ───────────────────────────────────────────────────────────

def load_ofac_sdn(db: Session, csv_path: str) -> dict:
    """Load OFAC Specially Designated Nationals (SDN) CSV into the watchlist.

    Only rows where ``SDN_TYPE == "Vessel"`` are processed.  MMSI is read from
    the ``VESSEL_ID`` column (validated as 9-digit); IMO is read from
    ``ent_num`` or alternative identification fields.  Unresolved vessels are
    fuzzy-matched by name.

    Args:
        db: Active SQLAlchemy session.
        csv_path: Absolute path to the OFAC SDN CSV file.

    Returns:
        ``{"matched": N, "unmatched": M, "skipped": K}``
    """
    matched = 0
    unmatched = 0
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sdn_type = row.get("SDN_TYPE", "").strip()
            if sdn_type != "Vessel":
                skipped += 1
                continue

            name = (row.get("SDN_NAME") or row.get("name") or "").strip() or None
            mmsi = (row.get("VESSEL_ID") or "").strip() or None
            imo = (row.get("ent_num") or row.get("ALT_NUM") or "").strip() or None
            remarks = (row.get("REMARKS") or row.get("remarks") or "").strip() or None

            vessel = _resolve_vessel(db, mmsi=mmsi, imo=imo, name=name)
            if vessel is None:
                logger.warning(
                    "OFAC SDN: no vessel match for name=%r mmsi=%r imo=%r",
                    name, mmsi, imo,
                )
                unmatched += 1
                continue

            _upsert_watchlist(
                db,
                vessel=vessel,
                watchlist_source="OFAC_SDN",
                reason=remarks,
                date_listed=None,
                source_url=None,
            )
            matched += 1

    db.commit()
    logger.info(
        "OFAC SDN load complete: matched=%d unmatched=%d skipped=%d",
        matched, unmatched, skipped,
    )
    return {"matched": matched, "unmatched": unmatched, "skipped": skipped}


# ── KSE Institute loader ──────────────────────────────────────────────────────

def load_kse_list(db: Session, csv_path: str) -> dict:
    """Load KSE Institute shadow-fleet CSV into the watchlist.

    Columns vary by export; the loader tries common field names for vessel_name,
    flag, imo, and mmsi.  Direct MMSI/IMO match is attempted first; fuzzy
    name+flag match is used as a fallback.

    Args:
        db: Active SQLAlchemy session.
        csv_path: Absolute path to the KSE CSV file.

    Returns:
        ``{"matched": N, "unmatched": M}``
    """
    matched = 0
    unmatched = 0

    # Column name candidates in priority order.
    _NAME_FIELDS = ["vessel_name", "name", "ship_name", "VESSEL_NAME", "NAME"]
    _FLAG_FIELDS = ["flag", "flag_state", "FLAG", "FLAG_STATE"]
    _IMO_FIELDS = ["imo", "imo_number", "IMO", "IMO_NUMBER"]
    _MMSI_FIELDS = ["mmsi", "MMSI"]

    def _first(row: dict, keys: list[str]) -> Optional[str]:
        for k in keys:
            val = row.get(k, "")
            if val and val.strip():
                return val.strip()
        return None

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = _first(row, _NAME_FIELDS)
            flag = _first(row, _FLAG_FIELDS)
            imo = _first(row, _IMO_FIELDS)
            mmsi = _first(row, _MMSI_FIELDS)

            vessel = _resolve_vessel(db, mmsi=mmsi, imo=imo, name=name, flag=flag)
            if vessel is None:
                logger.warning(
                    "KSE: no vessel match for name=%r flag=%r mmsi=%r imo=%r",
                    name, flag, mmsi, imo,
                )
                unmatched += 1
                continue

            _upsert_watchlist(
                db,
                vessel=vessel,
                watchlist_source="KSE_INSTITUTE",
                reason="KSE shadow fleet list",
                date_listed=None,
                source_url=None,
            )
            matched += 1

    db.commit()
    logger.info(
        "KSE Institute load complete: matched=%d unmatched=%d", matched, unmatched
    )
    return {"matched": matched, "unmatched": unmatched}


# ── OpenSanctions loader ──────────────────────────────────────────────────────

def load_opensanctions(db: Session, json_path: str) -> dict:
    """Load OpenSanctions JSON vessel entities into the watchlist.

    Expected format: a JSON array of entity objects.  Only objects where
    ``schema == "Vessel"`` are processed.  The ``dataset_id`` (or ``datasets``
    list) is used to pick the watchlist_source label:
      - contains "ofac"  → "OFAC_SDN"
      - contains "eu_"   → "EU_COUNCIL"
      - otherwise        → "OPENSANCTIONS"

    Name may be a string or a list; the first element is used for matching.

    Args:
        db: Active SQLAlchemy session.
        json_path: Absolute path to the OpenSanctions JSON file.

    Returns:
        ``{"matched": N, "unmatched": M}``
    """
    matched = 0
    unmatched = 0

    with open(json_path, encoding="utf-8") as fh:
        entities = json.load(fh)

    if not isinstance(entities, list):
        logger.error("OpenSanctions JSON must be a top-level array — aborting.")
        return {"matched": 0, "unmatched": 0}

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        schema = entity.get("schema") or entity.get("type") or ""
        if schema != "Vessel":
            continue

        props = entity.get("properties") or entity
        caption = entity.get("caption") or ""

        # ── Extract name ──────────────────────────────────────────────────────
        raw_name = props.get("name") or caption or ""
        if isinstance(raw_name, list):
            name = raw_name[0].strip() if raw_name else None
        else:
            name = raw_name.strip() or None

        # ── Extract identifiers ───────────────────────────────────────────────
        def _first_prop(key: str) -> Optional[str]:
            val = props.get(key)
            if isinstance(val, list):
                return val[0].strip() if val else None
            return val.strip() if val else None

        mmsi = _first_prop("mmsi") or _first_prop("MMSI")
        imo = _first_prop("imoNumber") or _first_prop("imo")
        flag = _first_prop("flag") or _first_prop("country")

        # ── Determine watchlist source from dataset_id ────────────────────────
        dataset_id: str = ""
        datasets = entity.get("datasets") or []
        if datasets:
            dataset_id = datasets[0].lower() if isinstance(datasets[0], str) else ""
        else:
            dataset_id = str(entity.get("dataset_id") or "").lower()

        if "ofac" in dataset_id:
            source = "OFAC_SDN"
        elif "eu_" in dataset_id or dataset_id.startswith("eu"):
            source = "EU_COUNCIL"
        else:
            source = "OPENSANCTIONS"

        # ── Match vessel ──────────────────────────────────────────────────────
        vessel = _resolve_vessel(db, mmsi=mmsi, imo=imo, name=name, flag=flag)
        if vessel is None:
            logger.warning(
                "OpenSanctions: no vessel match for name=%r mmsi=%r imo=%r dataset=%r",
                name, mmsi, imo, dataset_id,
            )
            unmatched += 1
            continue

        _upsert_watchlist(
            db,
            vessel=vessel,
            watchlist_source=source,
            reason=entity.get("reason") or entity.get("notes"),
            date_listed=None,
            source_url=entity.get("source_url"),
        )
        matched += 1

    db.commit()
    logger.info(
        "OpenSanctions load complete: matched=%d unmatched=%d", matched, unmatched
    )
    return {"matched": matched, "unmatched": unmatched}
