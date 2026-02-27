"""NOAA AIS historical data downloader and importer.

NOAA distributes historical AIS data at:
  https://coast.noaa.gov/htdata/CMSP/AISDataHandler/

Format varies by year:
  - ≤2024: ZIP archives containing CSV (AIS_{YYYY}_{MM}_{DD}.zip)
  - 2025+: Zstandard-compressed CSV (ais-{YYYY}-{MM}-{DD}.csv.zst)
"""
from __future__ import annotations

import csv
import io
import logging
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

NOAA_BASE_URL = getattr(settings, "NOAA_BASE_URL", "https://coast.noaa.gov/htdata/CMSP/AISDataHandler")
_TIMEOUT = 300.0  # 5 min for large files
_BATCH_SIZE = 5000


def _url_for_date(d: date) -> str:
    """Build NOAA download URL for a given date."""
    if d.year >= 2025:
        return f"{NOAA_BASE_URL}/{d.year}/ais-{d.year}-{d.month:02d}-{d.day:02d}.csv.zst"
    else:
        return f"{NOAA_BASE_URL}/{d.year}/AIS_{d.year}_{d.month:02d}_{d.day:02d}.zip"


def _build_corridor_bbox(db) -> tuple[float, float, float, float] | None:
    """Build a merged bounding box from all corridors (±1° buffer)."""
    from app.models.corridor import Corridor
    from app.modules.gfw_client import _extract_bbox_from_wkt

    corridors = db.query(Corridor).filter(Corridor.geometry.isnot(None)).all()
    if not corridors:
        return None

    all_lats = []
    all_lons = []
    for c in corridors:
        bbox = _extract_bbox_from_wkt(c.geometry)
        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            all_lats.extend([lat_min, lat_max])
            all_lons.extend([lon_min, lon_max])

    if not all_lats:
        return None

    # ±1° buffer
    return (min(all_lats) - 1.0, min(all_lons) - 1.0, max(all_lats) + 1.0, max(all_lons) + 1.0)


def _point_in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    """Check if (lat, lon) is within (lat_min, lon_min, lat_max, lon_max)."""
    return bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3]


def download_noaa_file(target_date: date, output_dir: Path | None = None) -> Path:
    """Download a single NOAA AIS file for the given date.

    Returns path to the downloaded file.
    """
    url = _url_for_date(target_date)
    output_dir = output_dir or Path(settings.DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = url.split("/")[-1]
    output_path = output_dir / filename
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    if output_path.exists():
        logger.info("NOAA file already exists: %s", output_path)
        return output_path

    logger.info("Downloading NOAA AIS data: %s", url)

    from app.utils.http_retry import retry_request

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

    # Validate before rename
    if filename.endswith(".zip"):
        try:
            with zipfile.ZipFile(tmp_path) as zf:
                if zf.testzip() is not None:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"Corrupt ZIP archive: {filename}")
        except zipfile.BadZipFile:
            tmp_path.unlink(missing_ok=True)
            raise ValueError(f"Invalid ZIP file: {filename}")
    elif filename.endswith(".csv.zst"):
        try:
            import zstandard as zstd
            dctx = zstd.ZstdDecompressor()
            with open(tmp_path, "rb") as f:
                # Read first 1KB to validate
                reader = dctx.stream_reader(f)
                header = reader.read(1024)
                if not header:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"Empty Zstandard file: {filename}")
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise ValueError(f"Invalid Zstandard file {filename}: {exc}")

    tmp_path.rename(output_path)
    logger.info("Downloaded NOAA file: %s (%.1f MB)", output_path, output_path.stat().st_size / 1e6)
    return output_path


def _decompress_csv_lines(filepath: Path):
    """Yield CSV lines from ZIP or Zstandard file."""
    name = filepath.name

    if name.endswith(".zip"):
        with zipfile.ZipFile(filepath) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise ValueError(f"No CSV files in ZIP: {filepath}")
            for csv_name in csv_names:
                with zf.open(csv_name) as f:
                    text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                    yield from text_stream

    elif name.endswith(".csv.zst"):
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
        with open(filepath, "rb") as f:
            reader = dctx.stream_reader(f)
            text_stream = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
            yield from text_stream

    else:
        # Plain CSV
        with open(filepath, encoding="utf-8", errors="replace") as f:
            yield from f


def import_noaa_file(
    filepath: Path,
    db,
    corridor_filter: bool = True,
) -> dict[str, int]:
    """Import AIS positions from a NOAA file into the database.

    Args:
        filepath: Path to ZIP or .csv.zst file.
        db: SQLAlchemy session.
        corridor_filter: If True, only import points within corridor bboxes.

    Returns import statistics dict.
    """
    from app.modules.normalize import normalize_noaa_row, validate_ais_row
    from app.modules.ingest import _get_or_create_vessel, _create_ais_point

    stats: dict[str, int] = {
        "total_rows": 0,
        "accepted": 0,
        "rejected": 0,
        "filtered_geo": 0,
        "duplicates": 0,
    }

    # Build geo filter
    bbox = _build_corridor_bbox(db) if corridor_filter else None
    if corridor_filter and bbox is None:
        logger.warning("No corridors loaded — importing all positions (no geo-filter)")

    lines = _decompress_csv_lines(filepath)
    reader = csv.DictReader(lines)

    batch_count = 0

    for row in reader:
        stats["total_rows"] += 1

        # Normalize NOAA column names
        normalized = normalize_noaa_row(row)
        if normalized is None:
            stats["rejected"] += 1
            continue

        # Geographic pre-filter
        try:
            lat = float(normalized["lat"])
            lon = float(normalized["lon"])
        except (KeyError, TypeError, ValueError):
            stats["rejected"] += 1
            continue

        if bbox and not _point_in_bbox(lat, lon, bbox):
            stats["filtered_geo"] += 1
            continue

        # Validate using standard AIS validation
        error = validate_ais_row(normalized)
        if error:
            stats["rejected"] += 1
            continue

        # Import via existing ingest pipeline
        try:
            vessel = _get_or_create_vessel(db, normalized)
            if vessel is None:
                stats["rejected"] += 1
                continue
            result = _create_ais_point(db, vessel, normalized)
            if result is None:
                stats["duplicates"] += 1
            else:
                stats["accepted"] += 1
        except Exception:
            stats["rejected"] += 1
            continue

        batch_count += 1
        if batch_count % _BATCH_SIZE == 0:
            db.commit()
            if stats["total_rows"] % 50000 == 0:
                logger.info("NOAA import progress: %d rows processed, %d accepted", stats["total_rows"], stats["accepted"])

    db.commit()
    logger.info("NOAA import complete: %s", stats)
    return stats


def fetch_and_import_noaa(
    db,
    start_date: date,
    end_date: date,
    corridor_filter: bool = True,
    import_data: bool = True,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Download and optionally import NOAA AIS data for a date range.

    Returns stats dict.
    """
    stats: dict[str, Any] = {
        "dates_attempted": 0,
        "dates_downloaded": 0,
        "dates_failed": [],
        "total_accepted": 0,
        "total_rows": 0,
    }

    current = start_date
    while current <= end_date:
        stats["dates_attempted"] += 1
        try:
            filepath = download_noaa_file(current, output_dir=output_dir)
            stats["dates_downloaded"] += 1

            if import_data:
                result = import_noaa_file(filepath, db, corridor_filter=corridor_filter)
                stats["total_rows"] += result["total_rows"]
                stats["total_accepted"] += result["accepted"]
        except Exception as exc:
            logger.warning("NOAA date %s failed: %s", current, exc)
            stats["dates_failed"].append({"date": current.isoformat(), "error": str(exc)})

        current += timedelta(days=1)

    logger.info("NOAA fetch complete: %s", {k: v for k, v in stats.items() if k != "dates_failed"})
    return stats
