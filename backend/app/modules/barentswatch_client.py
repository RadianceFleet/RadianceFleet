"""BarentsWatch AIS API client -- Norwegian EEZ + Svalbard track data.

REST API complement to Kystverket TCP stream. Covers Murmansk corridor.
Auth: OAuth 2.0 Client Credentials grant.
CRITICAL LIMITATION: Max 14 days of history. Data older than 14 days is purged.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)

_TIMEOUT = 30


def get_barentswatch_token(
    client_id: str | None = None,
    client_secret: str | None = None,
    token_url: str | None = None,
) -> str:
    """Fetch OAuth 2.0 bearer token (Client Credentials grant, scope=ais).

    Args:
        client_id: OAuth client ID (falls back to settings).
        client_secret: OAuth client secret (falls back to settings).
        token_url: Token endpoint (falls back to settings).

    Returns:
        Bearer access token string.

    Raises:
        httpx.HTTPStatusError: On auth failure.
    """
    cid = client_id or getattr(settings, "BARENTSWATCH_CLIENT_ID", "")
    csecret = client_secret or getattr(settings, "BARENTSWATCH_CLIENT_SECRET", "")
    url = token_url or getattr(
        settings, "BARENTSWATCH_TOKEN_URL",
        "https://id.barentswatch.no/connect/token",
    )

    if not cid or not csecret:
        raise ValueError("BARENTSWATCH_CLIENT_ID and BARENTSWATCH_CLIENT_SECRET required")

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = breakers["barentswatch"].call(
            client.post,
            url,
            data={
                "grant_type": "client_credentials",
                "scope": "ais",
                "client_id": cid,
                "client_secret": csecret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


def fetch_barentswatch_tracks(
    db: Session,
    mmsis: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    token: str | None = None,
) -> dict:
    """Fetch tracks from BarentsWatch REST API and ingest into DB.

    API base: https://live.ais.barentswatch.no/api
    Response: GeoJSON FeatureCollection with SOG, COG, heading, draught, destination.

    Args:
        db: Active SQLAlchemy session.
        mmsis: Optional list of MMSIs to query. If empty/None, fetches latest positions.
        start_date: Start of date range (max 14 days ago).
        end_date: End of date range.
        token: Bearer token. If None, will be fetched automatically.

    Returns:
        Stats dict with points_imported, vessels_seen, api_calls, errors.
    """
    if not getattr(settings, "BARENTSWATCH_ENABLED", False):
        logger.info("BarentsWatch disabled (BARENTSWATCH_ENABLED=False)")
        return {"points_imported": 0, "vessels_seen": 0, "api_calls": 0, "errors": 0}

    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.modules.normalize import is_non_vessel_mmsi
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag

    stats = {"points_imported": 0, "vessels_seen": 0, "api_calls": 0, "errors": 0}

    # Enforce 14-day limit
    now = datetime.now(timezone.utc).date()
    if start_date and start_date < now - timedelta(days=14):
        logger.warning("BarentsWatch: clamping start_date to 14 days ago (was %s)", start_date)
        start_date = now - timedelta(days=14)

    # Get token if not provided
    if token is None:
        try:
            token = get_barentswatch_token()
        except Exception as e:
            logger.error("BarentsWatch: auth failed: %s", e)
            stats["errors"] += 1
            return stats

    api_base = getattr(
        settings, "BARENTSWATCH_API_URL",
        "https://live.ais.barentswatch.no/api",
    ).rstrip("/")

    vessels_seen: set[str] = set()

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            headers = {"Authorization": f"Bearer {token}"}

            if mmsis:
                # Fetch tracks per MMSI
                for mmsi in mmsis:
                    try:
                        params: dict = {"mmsi": mmsi}
                        if start_date:
                            params["from"] = start_date.isoformat()
                        if end_date:
                            params["to"] = end_date.isoformat()

                        resp = breakers["barentswatch"].call(
                            client.get,
                            f"{api_base}/v1/ais/tracks",
                            params=params,
                            headers=headers,
                        )
                        stats["api_calls"] += 1
                        resp.raise_for_status()

                        data = resp.json()
                        features = data.get("features", []) if isinstance(data, dict) else []
                        if isinstance(data, list):
                            features = data

                        for feat in features:
                            _ingest_barentswatch_feature(
                                db, feat, vessels_seen, stats,
                                mmsi_to_flag, flag_to_risk_category,
                                is_non_vessel_mmsi,
                                Vessel, AISPoint,
                            )

                    except httpx.HTTPStatusError as e:
                        logger.warning("BarentsWatch: MMSI %s failed: %s", mmsi, e)
                        stats["errors"] += 1
                    except Exception as e:
                        logger.warning("BarentsWatch: error for MMSI %s: %s", mmsi, e)
                        stats["errors"] += 1
            else:
                # Fetch latest positions (no specific MMSIs)
                params = {}
                if start_date:
                    params["from"] = start_date.isoformat()
                if end_date:
                    params["to"] = end_date.isoformat()

                resp = breakers["barentswatch"].call(
                    client.get,
                    f"{api_base}/v1/ais/latest",
                    params=params,
                    headers=headers,
                )
                stats["api_calls"] += 1
                resp.raise_for_status()

                data = resp.json()
                features = data.get("features", []) if isinstance(data, dict) else []
                if isinstance(data, list):
                    features = data

                for feat in features:
                    _ingest_barentswatch_feature(
                        db, feat, vessels_seen, stats,
                        mmsi_to_flag, flag_to_risk_category,
                        is_non_vessel_mmsi,
                        Vessel, AISPoint,
                    )

        db.commit()

    except Exception as e:
        logger.error("BarentsWatch fetch failed: %s", e)
        stats["errors"] += 1

    stats["vessels_seen"] = len(vessels_seen)
    logger.info(
        "BarentsWatch: %d points, %d vessels, %d api_calls, %d errors",
        stats["points_imported"], stats["vessels_seen"],
        stats["api_calls"], stats["errors"],
    )
    return stats


def _ingest_barentswatch_feature(
    db, feat, vessels_seen, stats,
    mmsi_to_flag, flag_to_risk_category, is_non_vessel_mmsi,
    Vessel, AISPoint,
):
    """Ingest a single GeoJSON feature from BarentsWatch."""
    try:
        props = feat.get("properties", {}) if isinstance(feat, dict) else {}
        geom = feat.get("geometry", {}) if isinstance(feat, dict) else {}

        mmsi = str(props.get("mmsi", ""))
        if not mmsi or len(mmsi) != 9:
            return
        if is_non_vessel_mmsi(mmsi):
            return

        coords = geom.get("coordinates", [])
        if not coords or len(coords) < 2:
            return

        lon, lat = float(coords[0]), float(coords[1])
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return

        sog = props.get("sog")
        cog = props.get("cog")
        heading = props.get("heading")
        draught = props.get("draught")

        ts_raw = props.get("timestamp") or props.get("msgtime")
        timestamp = datetime.utcnow()
        if ts_raw:
            try:
                if isinstance(ts_raw, (int, float)):
                    timestamp = datetime.utcfromtimestamp(ts_raw / 1000)
                else:
                    timestamp = datetime.fromisoformat(
                        str(ts_raw).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
            except Exception:
                pass

        vessels_seen.add(mmsi)

        # Upsert vessel
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            derived_flag = mmsi_to_flag(mmsi)
            vessel = Vessel(
                mmsi=mmsi,
                flag=derived_flag,
                flag_risk_category=flag_to_risk_category(derived_flag),
                ais_class="A",
                ais_source="barentswatch",
                mmsi_first_seen_utc=timestamp,
            )
            try:
                with db.begin_nested():
                    db.add(vessel)
                    db.flush()
            except IntegrityError:
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    stats["errors"] += 1
                    return

        # Update data freshness tracking
        current_ais = getattr(vessel, "last_ais_received_utc", None)
        if current_ais is None or not isinstance(current_ais, datetime) or timestamp > current_ais:
            vessel.last_ais_received_utc = timestamp

        # Dedup
        existing = (
            db.query(AISPoint)
            .filter(
                AISPoint.vessel_id == vessel.vessel_id,
                AISPoint.timestamp_utc == timestamp,
            )
            .first()
        )
        if existing:
            return

        point = AISPoint(
            vessel_id=vessel.vessel_id,
            timestamp_utc=timestamp,
            lat=lat,
            lon=lon,
            sog=float(sog) if sog is not None else None,
            cog=float(cog) if cog is not None else None,
            heading=float(heading) if heading is not None and heading != 511 else None,
            draught=float(draught) if draught is not None else None,
            ais_class="A",
            source="barentswatch",
        )
        db.add(point)
        stats["points_imported"] += 1

    except Exception as exc:
        logger.debug("Failed to process BarentsWatch feature: %s", exc)
        stats["errors"] += 1
