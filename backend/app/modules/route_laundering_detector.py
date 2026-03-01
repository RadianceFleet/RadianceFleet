"""Route laundering detector -- identifies vessels using intermediary ports
to obscure Russian-origin cargo before delivery to sanctioned destinations.

A common sanctions-evasion technique is to route cargo through intermediary
ports (e.g., Fujairah, Sohar, Ceuta) to disguise Russian-origin petroleum.
This detector scans PortCall sequences per vessel for suspicious 2-hop and
3-hop patterns.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# ── Sanctioned destination countries (simplified list) ────────────────────
# Countries under comprehensive oil import sanctions or where deliveries
# would require sanctions circumvention.
_SANCTIONED_DESTINATIONS: frozenset[str] = frozenset({
    "KP",  # North Korea
    "SY",  # Syria
    "IR",  # Iran
    "CU",  # Cuba
    "VE",  # Venezuela
})

_RUSSIAN_ORIGIN_COUNTRIES: frozenset[str] = frozenset({
    "RU",  # Russia
})

_INTERMEDIARY_CONFIG: dict[str, Any] | None = None


def _load_intermediary_config() -> list[dict]:
    """Load intermediary port list from YAML config."""
    global _INTERMEDIARY_CONFIG
    if _INTERMEDIARY_CONFIG is not None:
        return _INTERMEDIARY_CONFIG.get("intermediary_ports", [])

    # Derive config directory from the risk_scoring.yaml path (same base)
    scoring_config = Path(settings.RISK_SCORING_CONFIG)
    config_path = scoring_config.parent / "laundering_intermediaries.yaml"
    if not config_path.exists():
        logger.warning("laundering_intermediaries.yaml not found at %s", config_path)
        _INTERMEDIARY_CONFIG = {}
        return []

    with open(config_path) as f:
        _INTERMEDIARY_CONFIG = yaml.safe_load(f) or {}
    return _INTERMEDIARY_CONFIG.get("intermediary_ports", [])


def _get_intermediary_countries() -> frozenset[str]:
    """Return set of intermediary country codes from config."""
    ports = _load_intermediary_config()
    return frozenset(p.get("country", "").upper() for p in ports if p.get("country"))


def _get_intermediary_names() -> frozenset[str]:
    """Return set of intermediary port names (lowered) from config."""
    ports = _load_intermediary_config()
    return frozenset(p.get("name", "").lower() for p in ports if p.get("name"))


def run_route_laundering_detection(db: Session) -> dict:
    """Detect vessels with route laundering patterns.

    Scans PortCall sequences per vessel for Russian-origin port followed by
    intermediary port followed by sanctioned-destination port.

    Scoring:
      - 3-hop confirmed (Russian -> intermediary -> sanctioned dest): +35
      - 2-hop partial (Russian -> intermediary, no confirmed final dest): +20
      - Pattern-only (intermediary visit without co-occurring risk signals): +15

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.ROUTE_LAUNDERING_DETECTION_ENABLED:
        return {"status": "disabled"}

    lookback_days = settings.ROUTE_LAUNDERING_LOOKBACK_DAYS
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    intermediary_countries = _get_intermediary_countries()
    intermediary_names = _get_intermediary_names()

    if not intermediary_countries and not intermediary_names:
        logger.warning("No intermediary ports configured -- skipping route laundering detection")
        return {"status": "ok", "anomalies_created": 0, "vessels_checked": 0}

    # Load all ports for country/name lookups
    all_ports = {p.port_id: p for p in db.query(Port).all()}

    vessels = db.query(Vessel).all()
    anomalies_created = 0

    for vessel in vessels:
        # Get port calls in lookback window, ordered by arrival
        port_calls = (
            db.query(PortCall)
            .filter(
                PortCall.vessel_id == vessel.vessel_id,
                PortCall.arrival_utc >= cutoff,
            )
            .order_by(PortCall.arrival_utc)
            .all()
        )

        if len(port_calls) < 2:
            continue

        # Check for existing anomaly
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel.vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
        ).first()
        if existing:
            continue

        # Classify each port call
        classified: list[tuple[PortCall, str]] = []  # (port_call, category)
        for pc in port_calls:
            port = all_ports.get(pc.port_id) if pc.port_id else None
            category = _classify_port(port, pc.raw_port_name, intermediary_countries, intermediary_names)
            classified.append((pc, category))

        # Scan for patterns
        best_pattern = _find_best_pattern(classified)
        if best_pattern is None:
            continue

        hop_count, evidence = best_pattern

        # Determine score
        if hop_count >= 3:
            score = 35
        elif hop_count >= 2:
            score = 20
        else:
            score = 15

        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.ROUTE_LAUNDERING,
            start_time_utc=evidence["first_call_utc"],
            end_time_utc=evidence.get("last_call_utc", evidence["first_call_utc"]),
            risk_score_component=score,
            evidence_json={
                "hop_count": hop_count,
                "pattern": evidence["pattern"],
                "port_sequence": evidence["port_sequence"],
            },
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()
    logger.info(
        "Route laundering: %d anomalies from %d vessels checked",
        anomalies_created, len(vessels),
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": len(vessels),
    }


def _classify_port(
    port: Port | None,
    raw_name: str | None,
    intermediary_countries: frozenset[str],
    intermediary_names: frozenset[str],
) -> str:
    """Classify a port as 'russian', 'intermediary', 'sanctioned', or 'other'."""
    country = ""
    port_name = ""

    if port is not None:
        country = (port.country or "").upper()
        port_name = (port.name or "").lower()
        if getattr(port, "is_russian_oil_terminal", False):
            return "russian"

    if not country and raw_name:
        port_name = raw_name.lower()

    if country in _RUSSIAN_ORIGIN_COUNTRIES:
        return "russian"
    if country in _SANCTIONED_DESTINATIONS:
        return "sanctioned"
    if country in intermediary_countries:
        return "intermediary"
    if port_name and port_name in intermediary_names:
        return "intermediary"

    return "other"


def _find_best_pattern(
    classified: list[tuple[PortCall, str]],
) -> tuple[int, dict] | None:
    """Find the best laundering pattern in the classified port call sequence.

    Returns (hop_count, evidence_dict) or None if no pattern found.
    """
    best: tuple[int, dict] | None = None

    for i, (pc_i, cat_i) in enumerate(classified):
        if cat_i != "russian":
            continue

        # Look for intermediary after Russian port
        for j in range(i + 1, len(classified)):
            pc_j, cat_j = classified[j]
            if cat_j != "intermediary":
                continue

            # Found 2-hop: Russian -> intermediary
            # Look for sanctioned destination after intermediary
            found_3_hop = False
            for k in range(j + 1, len(classified)):
                pc_k, cat_k = classified[k]
                if cat_k == "sanctioned":
                    # 3-hop pattern
                    evidence = {
                        "pattern": "russian_intermediary_sanctioned",
                        "first_call_utc": pc_i.arrival_utc,
                        "last_call_utc": pc_k.arrival_utc,
                        "port_sequence": [
                            {"port_id": pc_i.port_id, "raw_name": pc_i.raw_port_name, "category": "russian"},
                            {"port_id": pc_j.port_id, "raw_name": pc_j.raw_port_name, "category": "intermediary"},
                            {"port_id": pc_k.port_id, "raw_name": pc_k.raw_port_name, "category": "sanctioned"},
                        ],
                    }
                    if best is None or 3 > best[0]:
                        best = (3, evidence)
                    found_3_hop = True
                    break

            if not found_3_hop:
                # 2-hop pattern: Russian -> intermediary
                evidence = {
                    "pattern": "russian_intermediary",
                    "first_call_utc": pc_i.arrival_utc,
                    "last_call_utc": pc_j.arrival_utc,
                    "port_sequence": [
                        {"port_id": pc_i.port_id, "raw_name": pc_i.raw_port_name, "category": "russian"},
                        {"port_id": pc_j.port_id, "raw_name": pc_j.raw_port_name, "category": "intermediary"},
                    ],
                }
                if best is None or (best[0] < 2):
                    best = (2, evidence)

    return best
