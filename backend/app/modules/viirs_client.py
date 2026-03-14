"""VIIRS nighttime boat detection client.

Downloads nightly CSV data from NOAA EOG (Earth Observation Group) VIIRS
Boat Detection (VBD) product. Filters to QF_Detect=1 (boats only),
excluding gas flares, recurring platforms, etc.

Data source: https://eogdata.mines.edu/wwwdata/viirs_products/vbd/v23/
Key CSV columns: Lat_DNB, Lon_DNB, Rad_DNB, Date_Proc, QF_Detect
QF_Detect values: 1=Boat, 4=Gas Flare, 8=Recurring, 11=Platform
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)

# QF_Detect value for boat detections
QF_DETECT_BOAT = 1


def _retry_request(url: str, data_dir: Path, timeout: float = 120.0) -> Path:
    """Download a file from URL with httpx, returning local path."""
    filename = url.rsplit("/", 1)[-1]
    dest = data_dir / filename
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def download_viirs_csv(date_str: str, data_dir: str | None = None) -> Path:
    """Download VIIRS VBD CSV for a given date from EOG.

    Args:
        date_str: Date string in YYYYMMDD format.
        data_dir: Directory to store downloaded CSV. Defaults to settings.VIIRS_DATA_DIR.

    Returns:
        Path to the downloaded CSV file.
    """
    if data_dir is None:
        data_dir = settings.VIIRS_DATA_DIR
    dest_dir = Path(data_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    base_url = settings.VIIRS_EOG_BASE_URL.rstrip("/")
    url = f"{base_url}/VBD_npp_{date_str}.csv"

    logger.info("Downloading VIIRS CSV from %s", url)
    path = breakers["viirs"].call(_retry_request, url, dest_dir)
    logger.info("VIIRS CSV downloaded to %s", path)
    return path


def parse_viirs_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Parse a VIIRS VBD CSV file and filter to boat detections (QF_Detect=1).

    Args:
        csv_path: Path to the CSV file.

    Returns:
        List of detection dicts with keys: lat, lon, radiance, date_proc,
        qf_detect, scene_id.
    """
    detections: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                qf_detect = int(row.get("QF_Detect", "0"))
            except (ValueError, TypeError):
                continue

            if qf_detect != QF_DETECT_BOAT:
                continue

            try:
                lat = float(row["Lat_DNB"])
                lon = float(row["Lon_DNB"])
                radiance = float(row.get("Rad_DNB", 0.0))
            except (ValueError, KeyError, TypeError):
                logger.debug("Skipping row with invalid coordinates/radiance")
                continue

            date_proc = row.get("Date_Proc", "")
            # Build a unique scene_id from date + coordinates
            scene_id = f"viirs-{date_proc}-{lat:.4f}-{lon:.4f}"

            detection_time = None
            if date_proc:
                for fmt in ("%Y%m%d", "%Y-%m-%d"):
                    try:
                        detection_time = datetime.strptime(date_proc, fmt)
                        break
                    except ValueError:
                        continue

            detections.append({
                "lat": lat,
                "lon": lon,
                "radiance": radiance,
                "date_proc": date_proc,
                "qf_detect": qf_detect,
                "scene_id": scene_id,
                "detection_time": detection_time,
            })

    logger.info("Parsed %d boat detections from %s", len(detections), csv_path)
    return detections


def import_viirs_detections(db: Any, detections: list[dict[str, Any]]) -> dict[str, int]:
    """Import parsed VIIRS detections into the DarkVesselDetection table.

    Performs upsert by scene_id to avoid duplicates.

    Args:
        db: SQLAlchemy session.
        detections: List of detection dicts from parse_viirs_csv.

    Returns:
        Dict with 'imported' and 'skipped' counts.
    """
    from app.models.stubs import DarkVesselDetection

    imported = 0
    skipped = 0

    for det in detections:
        # Check for existing detection with same scene_id
        existing = (
            db.query(DarkVesselDetection)
            .filter(DarkVesselDetection.scene_id == det["scene_id"])
            .first()
        )
        if existing is not None:
            skipped += 1
            continue

        record = DarkVesselDetection(
            scene_id=det["scene_id"],
            detection_lat=det["lat"],
            detection_lon=det["lon"],
            detection_time_utc=det.get("detection_time"),
            ais_match_result="unmatched",
            ais_match_attempted=False,
            model_confidence=det.get("radiance"),
        )
        db.add(record)
        imported += 1

    if imported > 0:
        db.commit()
        logger.info("Imported %d VIIRS detections (%d duplicates skipped)", imported, skipped)

    return {"imported": imported, "skipped": skipped}


def collect_viirs(db: Any) -> dict[str, Any]:
    """Orchestrator: download latest VIIRS CSV, parse, and import.

    Downloads yesterday's data by default (VIIRS data has ~1 day latency).

    Args:
        db: SQLAlchemy session.

    Returns:
        Stats dict with 'imported', 'skipped', 'errors' counts.
    """
    if not settings.VIIRS_ENABLED:
        logger.debug("VIIRS collection disabled")
        return {"imported": 0, "skipped": 0, "errors": 0}

    yesterday = date.today() - timedelta(days=1)
    date_str = yesterday.strftime("%Y%m%d")

    try:
        csv_path = download_viirs_csv(date_str)
        detections = parse_viirs_csv(csv_path)

        # Apply gas flaring filter if enabled
        if settings.VIIRS_GAS_FLARING_FILTER_ENABLED:
            try:
                from app.modules.gas_flaring_filter import filter_flaring, load_flaring_platforms

                config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "gas_flaring_platforms.yaml"
                platforms = load_flaring_platforms(str(config_path))
                radius_nm = settings.VIIRS_GAS_FLARING_EXCLUSION_RADIUS_NM
                detections = filter_flaring(detections, platforms, radius_nm)
            except Exception:
                logger.warning("Gas flaring filter failed, proceeding without filtering", exc_info=True)

        result = import_viirs_detections(db, detections)
        return {**result, "errors": 0}
    except Exception:
        logger.exception("VIIRS collection failed")
        return {"imported": 0, "skipped": 0, "errors": 1}
