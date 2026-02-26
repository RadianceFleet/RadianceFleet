"""Data fetcher — downloads watchlist files from public URLs.

Supported sources:
  OFAC SDN CSV     — US Treasury Office of Foreign Assets Control
  OpenSanctions    — Open dataset aggregating multiple sanctions lists

Downloads use httpx with streaming to avoid buffering large files in memory.
Files are written to a temp path first, validated, then atomically renamed.
ETag/Last-Modified headers are cached to skip redundant downloads.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Source URLs ──────────────────────────────────────────────────────────────

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
OPENSANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/vessels/entities.json"
)

# PSC detention data sources (FTM JSON format from OpenSanctions)
PSC_FTM_URLS: dict[str, str] = {
    "tokyo_mou": "https://data.opensanctions.org/datasets/latest/tokyo_mou_detention/entities.ftm.json",
    "black_sea_mou": "https://data.opensanctions.org/datasets/latest/black_sea_mou_detention/entities.ftm.json",
    "abuja_mou": "https://data.opensanctions.org/datasets/latest/abuja_mou_detention/entities.ftm.json",
    "paris_mou_banned": "https://data.opensanctions.org/datasets/latest/paris_mou_banned/entities.ftm.json",
}

# EMSA ship ban API (canonical Paris MOU banned vessels — clean JSON)
EMSA_BAN_URL = "https://portal.emsa.europa.eu/o/portlet-public/rest/ban/getBanShips.json"

# ── Metadata cache ───────────────────────────────────────────────────────────

_METADATA_FILENAME = ".fetch_metadata.json"


def _metadata_path(output_dir: Path) -> Path:
    return output_dir / _METADATA_FILENAME


def _load_metadata(output_dir: Path) -> dict:
    p = _metadata_path(output_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_metadata(output_dir: Path, meta: dict) -> None:
    p = _metadata_path(output_dir)
    p.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ── Validation helpers ───────────────────────────────────────────────────────


def _validate_ofac_csv(path: Path) -> bool:
    """Check that the file looks like an OFAC SDN CSV (has expected header columns)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            header = f.readline()
        # OFAC SDN CSV should contain SDN_TYPE and SDN_NAME columns
        return "SDN_TYPE" in header or "ent_num" in header
    except Exception:
        return False


def _validate_opensanctions_json(path: Path) -> bool:
    """Check that the file is valid JSON containing vessel entities."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return False
        # Check that at least one entity has a 'schema' field
        return any(isinstance(e, dict) and "schema" in e for e in data[:100])
    except (json.JSONDecodeError, OSError):
        return False


# ── Download engine ──────────────────────────────────────────────────────────


def _download_file(
    url: str,
    output_path: Path,
    source_key: str,
    metadata: dict,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> tuple[Path | None, str | None]:
    """Download a file with conditional GET support.

    Returns (path, None) on success or (None, error_message) on failure.
    """
    if timeout is None:
        timeout = settings.DATA_FETCH_TIMEOUT

    headers: dict[str, str] = {}
    if not force and source_key in metadata:
        cached = metadata[source_key]
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    # Retry transient failures (503, 429, etc.) up to 3 times
    import time as _time
    _RETRYABLE = {429, 500, 502, 503, 504}
    _RETRY_DELAYS = [5, 15, 30]

    last_error: str | None = None
    for attempt in range(1 + len(_RETRY_DELAYS)):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 304:
                        logger.info(
                            "%s: not modified (304), skipping download", source_key
                        )
                        return None, None  # Not an error — already up to date

                    if response.status_code in _RETRYABLE and attempt < len(_RETRY_DELAYS):
                        delay = _RETRY_DELAYS[attempt]
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    delay = max(delay, float(retry_after))
                                except (ValueError, TypeError):
                                    pass
                        logger.warning(
                            "%s: HTTP %d — retrying in %.0fs",
                            source_key, response.status_code, delay,
                        )
                        _time.sleep(delay)
                        continue

                    response.raise_for_status()

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(tmp_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)

                    # Store conditional GET headers for next time
                    metadata[source_key] = {
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                        "downloaded_at": date.today().isoformat(),
                        "url": url,
                    }
            break  # Success — exit retry loop

        except httpx.ConnectError:
            if attempt < len(_RETRY_DELAYS):
                _time.sleep(_RETRY_DELAYS[attempt])
                continue
            _cleanup_tmp(tmp_path)
            return None, (
                f"Could not reach {_host(url)} (ConnectionError)\n"
                f"Tip: Download manually and run the appropriate import command."
            )
        except httpx.TimeoutException:
            if attempt < len(_RETRY_DELAYS):
                _time.sleep(_RETRY_DELAYS[attempt])
                continue
            _cleanup_tmp(tmp_path)
            return None, (
                f"Timed out connecting to {_host(url)} after {timeout}s\n"
                f"Tip: Retry with --force or increase DATA_FETCH_TIMEOUT."
            )
        except httpx.HTTPStatusError as exc:
            _cleanup_tmp(tmp_path)
            return None, f"HTTP {exc.response.status_code} from {url}"
        except OSError as exc:
            _cleanup_tmp(tmp_path)
            return None, f"File write error: {exc}"

    # Atomic rename from .tmp to final path
    try:
        tmp_path.rename(output_path)
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        return None, f"Could not rename temp file: {exc}"

    return output_path, None


def _cleanup_tmp(tmp_path: Path) -> None:
    try:
        tmp_path.unlink(missing_ok=True)
    except OSError:
        pass


def _host(url: str) -> str:
    """Extract hostname from URL for error messages."""
    from urllib.parse import urlparse

    return urlparse(url).hostname or url


# ── Public API ───────────────────────────────────────────────────────────────


def fetch_ofac_sdn(
    output_dir: Path | str | None = None,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> dict:
    """Download the OFAC SDN CSV.

    Returns ``{"path": Path, "status": "downloaded"|"up_to_date"|"error",
               "error": str|None}``.
    """
    output_dir = Path(output_dir or settings.DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(output_dir)

    today = date.today().isoformat()
    filename = f"ofac_sdn_{today}.csv"
    output_path = output_dir / filename

    path, error = _download_file(
        OFAC_SDN_URL, output_path, "ofac", metadata, force=force, timeout=timeout
    )

    if error:
        return {"path": None, "status": "error", "error": error}

    if path is None:
        # 304 Not Modified
        _save_metadata(output_dir, metadata)
        existing = metadata.get("ofac", {}).get("downloaded_at")
        return {
            "path": _find_latest(output_dir, "ofac_sdn_"),
            "status": "up_to_date",
            "error": None,
            "last_download": existing,
        }

    # Validate the downloaded file
    if not _validate_ofac_csv(path):
        path.unlink(missing_ok=True)
        return {
            "path": None,
            "status": "error",
            "error": "Downloaded OFAC file failed validation (missing expected CSV headers)",
        }

    _save_metadata(output_dir, metadata)
    logger.info("OFAC SDN downloaded to %s", path)
    return {"path": path, "status": "downloaded", "error": None}


def fetch_opensanctions_vessels(
    output_dir: Path | str | None = None,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> dict:
    """Download the OpenSanctions vessel entities JSON.

    Returns ``{"path": Path, "status": "downloaded"|"up_to_date"|"error",
               "error": str|None}``.
    """
    output_dir = Path(output_dir or settings.DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(output_dir)

    today = date.today().isoformat()
    filename = f"opensanctions_vessels_{today}.json"
    output_path = output_dir / filename

    path, error = _download_file(
        OPENSANCTIONS_URL,
        output_path,
        "opensanctions",
        metadata,
        force=force,
        timeout=timeout,
    )

    if error:
        return {"path": None, "status": "error", "error": error}

    if path is None:
        # 304 Not Modified
        _save_metadata(output_dir, metadata)
        existing = metadata.get("opensanctions", {}).get("downloaded_at")
        return {
            "path": _find_latest(output_dir, "opensanctions_vessels_"),
            "status": "up_to_date",
            "error": None,
            "last_download": existing,
        }

    # Validate the downloaded file
    if not _validate_opensanctions_json(path):
        path.unlink(missing_ok=True)
        return {
            "path": None,
            "status": "error",
            "error": "Downloaded OpenSanctions file failed validation (not valid JSON vessel array)",
        }

    _save_metadata(output_dir, metadata)
    logger.info("OpenSanctions vessels downloaded to %s", path)
    return {"path": path, "status": "downloaded", "error": None}


def fetch_all(
    output_dir: Path | str | None = None,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> dict:
    """Download all supported watchlist sources.

    Returns ``{"ofac": result_dict, "opensanctions": result_dict, "errors": [str]}``.
    """
    ofac = fetch_ofac_sdn(output_dir, force=force, timeout=timeout)
    opensanctions = fetch_opensanctions_vessels(
        output_dir, force=force, timeout=timeout
    )

    errors = []
    if ofac.get("error"):
        errors.append(f"OFAC: {ofac['error']}")
    if opensanctions.get("error"):
        errors.append(f"OpenSanctions: {opensanctions['error']}")

    return {"ofac": ofac, "opensanctions": opensanctions, "errors": errors}


def fetch_psc_ftm(
    output_dir: Path | str | None = None,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> dict:
    """Download PSC detention FTM JSON files from OpenSanctions.

    Returns ``{"files": {source: path_or_none}, "errors": [str]}``.
    """
    output_dir = Path(output_dir or settings.DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(output_dir)

    today = date.today().isoformat()
    files: dict[str, Path | None] = {}
    errors: list[str] = []

    for source_key, url in PSC_FTM_URLS.items():
        filename = f"psc_{source_key}_{today}.json"
        output_path = output_dir / filename
        meta_key = f"psc_{source_key}"

        path, error = _download_file(
            url, output_path, meta_key, metadata, force=force, timeout=timeout
        )

        if error:
            errors.append(f"{source_key}: {error}")
            files[source_key] = None
        elif path is None:
            # 304 Not Modified
            files[source_key] = _find_latest(output_dir, f"psc_{source_key}_")
        else:
            files[source_key] = path

    _save_metadata(output_dir, metadata)
    return {"files": files, "errors": errors}


def fetch_emsa_bans(
    output_dir: Path | str | None = None,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> dict:
    """Download EMSA ship ban list (Paris MOU banned vessels).

    Returns ``{"path": Path|None, "status": str, "error": str|None}``.
    """
    output_dir = Path(output_dir or settings.DATA_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(output_dir)

    today = date.today().isoformat()
    filename = f"emsa_bans_{today}.json"
    output_path = output_dir / filename

    path, error = _download_file(
        EMSA_BAN_URL, output_path, "emsa_bans", metadata, force=force, timeout=timeout
    )

    if error:
        return {"path": None, "status": "error", "error": error}

    if path is None:
        _save_metadata(output_dir, metadata)
        return {
            "path": _find_latest(output_dir, "emsa_bans_"),
            "status": "up_to_date",
            "error": None,
        }

    _save_metadata(output_dir, metadata)
    logger.info("EMSA bans downloaded to %s", path)
    return {"path": path, "status": "downloaded", "error": None}


def _find_latest(directory: Path, prefix: str) -> Path | None:
    """Find the most recently modified file matching a prefix."""
    matches = sorted(directory.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None
