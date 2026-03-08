"""Ground truth CSV loader — imports known shadow fleet / clean vessel lists."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.ground_truth import GroundTruthVessel
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)


def _parse_date(val: str | None):
    """Best-effort date parse from CSV value."""
    if not val or not val.strip():
        return None
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val.strip(), fmt)
        except ValueError:
            continue
    logger.warning("Could not parse date: %s", val)
    return None


def _existing_keys(db: Session) -> set[tuple[str | None, str]]:
    """Return set of (imo, source) already in ground_truth_vessels."""
    rows = db.query(GroundTruthVessel.imo, GroundTruthVessel.source).all()
    return {(r[0], r[1]) for r in rows}


def _load_csv(
    db: Session,
    csv_path: str,
    source: str,
    expected_band: str,
    is_shadow_fleet: bool,
) -> int:
    """Generic CSV loader. Returns count of inserted rows."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    existing = _existing_keys(db)
    added = 0

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            imo = row.get("imo", "").strip() or None
            if (imo, source) in existing:
                continue
            rec = GroundTruthVessel(
                imo=imo,
                mmsi=row.get("mmsi", "").strip() or None,
                vessel_name=row.get("vessel_name", "").strip() or None,
                source=source,
                expected_band=expected_band,
                is_shadow_fleet=is_shadow_fleet,
                date_listed=_parse_date(row.get("date_listed")),
                notes=row.get("notes", "").strip() or None,
            )
            db.add(rec)
            existing.add((imo, source))
            added += 1

    db.commit()
    logger.info("Loaded %d ground truth records from %s (source=%s)", added, csv_path, source)
    return added


def load_kse_csv(db: Session, csv_path: str) -> int:
    """Load KSE shadow fleet CSV."""
    return _load_csv(db, csv_path, source="KSE_SHADOW", expected_band="high", is_shadow_fleet=True)


def load_ofac_sdn_csv(db: Session, csv_path: str) -> int:
    """Load OFAC SDN sanctioned vessels CSV."""
    return _load_csv(
        db, csv_path, source="OFAC_SDN", expected_band="critical", is_shadow_fleet=True
    )


def load_clean_vessels_csv(db: Session, csv_path: str) -> int:
    """Load clean baseline vessels CSV."""
    return _load_csv(
        db, csv_path, source="CLEAN_BASELINE", expected_band="low", is_shadow_fleet=False
    )


def link_ground_truth(db: Session) -> int:
    """Link unlinked GroundTruthVessel records to Vessel table by IMO then MMSI."""
    unlinked = db.query(GroundTruthVessel).filter(GroundTruthVessel.vessel_id.is_(None)).all()
    linked = 0

    for gt in unlinked:
        vessel = None
        if gt.imo:
            vessel = db.query(Vessel).filter(Vessel.imo == gt.imo).first()
        if vessel is None and gt.mmsi:
            vessel = db.query(Vessel).filter(Vessel.mmsi == gt.mmsi).first()
        if vessel is not None:
            gt.vessel_id = vessel.vessel_id
            linked += 1

    db.commit()
    logger.info("Linked %d / %d ground truth records to vessels", linked, len(unlinked))
    return linked
