"""Fleet-level behavioural analysis — detect coordinated patterns across owner clusters.

Checks for:
1. Fleet STS concentration: 3+ cluster vessels in same STS zone within 30d
2. Fleet dark coordination: 3+ vessels going dark within 48h window
3. Fleet flag diversity: 4+ different flags in one cluster
4. Fleet high risk average: cluster average risk score >50
5. Shared manager different owners: same owner_name on vessel but different VesselOwner names
6. Shared P&I coverage status with high risk: vessels sharing same pi_coverage_status
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import or_

from app.config import settings
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.vessel_owner import VesselOwner
from app.models.vessel import Vessel
from app.models.gap_event import AISGapEvent
from app.models.sts_transfer import StsTransferEvent
from app.models.fleet_alert import FleetAlert

logger = logging.getLogger(__name__)

# Thresholds
STS_CONCENTRATION_MIN_VESSELS = 3
STS_CONCENTRATION_DAYS = 30
DARK_COORDINATION_MIN_VESSELS = 3
DARK_COORDINATION_HOURS = 48
FLAG_DIVERSITY_MIN = 4
HIGH_RISK_AVG_THRESHOLD = 50
MAX_CLUSTERS_PER_RUN = 500

# Score components
SCORE_STS_CONCENTRATION = 30
SCORE_DARK_COORDINATION = 25
SCORE_FLAG_DIVERSITY = 20
SCORE_HIGH_RISK_AVERAGE = 15
SCORE_SHARED_MANAGER = 15
SCORE_SHARED_PI_CLUB = 10


def _get_cluster_vessels(db, cluster: OwnerCluster) -> List[Vessel]:
    """Get all vessels associated with an owner cluster via ownership links."""
    members = (
        db.query(OwnerClusterMember)
        .filter(OwnerClusterMember.cluster_id == cluster.cluster_id)
        .all()
    )
    owner_ids = [m.owner_id for m in members]
    if not owner_ids:
        return []

    owners = db.query(VesselOwner).filter(VesselOwner.owner_id.in_(owner_ids)).all()
    vessel_ids = list({o.vessel_id for o in owners})
    if not vessel_ids:
        return []

    return db.query(Vessel).filter(Vessel.vessel_id.in_(vessel_ids)).all()


def _check_sts_concentration(db, cluster: OwnerCluster, vessels: List[Vessel]) -> Optional[FleetAlert]:
    """3+ cluster vessels in same STS corridor within 30 days."""
    if len(vessels) < STS_CONCENTRATION_MIN_VESSELS:
        return None

    vessel_ids = [v.vessel_id for v in vessels]

    sts_events = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id.in_(vessel_ids),
                StsTransferEvent.vessel_2_id.in_(vessel_ids),
            )
        )
        .all()
    )
    if not sts_events:
        return None

    # Group by corridor_id, check time window
    corridor_events: Dict[int, list] = defaultdict(list)
    for ev in sts_events:
        cid = ev.corridor_id
        if cid is not None:
            corridor_events[cid].append(ev)

    for corridor_id, events in corridor_events.items():
        events.sort(key=lambda e: e.start_time_utc)
        # Sliding window: check if 3+ cluster vessels appear within 30d
        for i in range(len(events)):
            window_start = events[i].start_time_utc
            window_end = window_start + timedelta(days=STS_CONCENTRATION_DAYS)
            window_vessel_ids = set()
            for j in range(i, len(events)):
                if events[j].start_time_utc > window_end:
                    break
                # Only count vessels that belong to this cluster
                if events[j].vessel_1_id in vessel_ids:
                    window_vessel_ids.add(events[j].vessel_1_id)
                if events[j].vessel_2_id in vessel_ids:
                    window_vessel_ids.add(events[j].vessel_2_id)
            if len(window_vessel_ids) >= STS_CONCENTRATION_MIN_VESSELS:
                return FleetAlert(
                    owner_cluster_id=cluster.cluster_id,
                    alert_type="fleet_sts_concentration",
                    vessel_ids_json=list(window_vessel_ids),
                    evidence_json={
                        "corridor_id": corridor_id,
                        "vessel_count": len(window_vessel_ids),
                        "window_days": STS_CONCENTRATION_DAYS,
                    },
                    risk_score_component=SCORE_STS_CONCENTRATION,
                )
    return None


def _check_dark_coordination(db, cluster: OwnerCluster, vessels: List[Vessel]) -> Optional[FleetAlert]:
    """3+ vessels going dark (AIS gaps) within a 48h window."""
    if len(vessels) < DARK_COORDINATION_MIN_VESSELS:
        return None

    vessel_ids = [v.vessel_id for v in vessels]

    gaps = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.vessel_id.in_(vessel_ids))
        .order_by(AISGapEvent.gap_start_utc)
        .all()
    )
    if len(gaps) < DARK_COORDINATION_MIN_VESSELS:
        return None

    # Sliding window: check if 3+ different vessels have gaps starting within 48h
    for i in range(len(gaps)):
        window_start = gaps[i].gap_start_utc
        window_end = window_start + timedelta(hours=DARK_COORDINATION_HOURS)
        window_vessels = set()
        for j in range(i, len(gaps)):
            if gaps[j].gap_start_utc > window_end:
                break
            window_vessels.add(gaps[j].vessel_id)
        if len(window_vessels) >= DARK_COORDINATION_MIN_VESSELS:
            return FleetAlert(
                owner_cluster_id=cluster.cluster_id,
                alert_type="fleet_dark_coordination",
                vessel_ids_json=list(window_vessels),
                evidence_json={
                    "vessel_count": len(window_vessels),
                    "window_hours": DARK_COORDINATION_HOURS,
                },
                risk_score_component=SCORE_DARK_COORDINATION,
            )
    return None


def _check_flag_diversity(cluster: OwnerCluster, vessels: List[Vessel]) -> Optional[FleetAlert]:
    """4+ different flags in one cluster."""
    flags = {v.flag for v in vessels if v.flag}
    if len(flags) >= FLAG_DIVERSITY_MIN:
        return FleetAlert(
            owner_cluster_id=cluster.cluster_id,
            alert_type="fleet_flag_diversity",
            vessel_ids_json=[v.vessel_id for v in vessels],
            evidence_json={
                "flags": sorted(flags),
                "flag_count": len(flags),
            },
            risk_score_component=SCORE_FLAG_DIVERSITY,
        )
    return None


def _check_high_risk_average(cluster: OwnerCluster, vessels: List[Vessel], db) -> Optional[FleetAlert]:
    """Cluster average risk score >50."""
    if not vessels:
        return None

    # Get the latest gap event risk scores for each vessel as a proxy for risk
    vessel_ids = [v.vessel_id for v in vessels]
    gaps = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.vessel_id.in_(vessel_ids))
        .all()
    )

    # Group by vessel, take max risk_score per vessel
    vessel_risk: Dict[int, int] = {}
    for g in gaps:
        vid = g.vessel_id
        if vid not in vessel_risk or g.risk_score > vessel_risk[vid]:
            vessel_risk[vid] = g.risk_score

    if not vessel_risk:
        return None

    avg_risk = sum(vessel_risk.values()) / len(vessel_risk)
    if avg_risk > HIGH_RISK_AVG_THRESHOLD:
        return FleetAlert(
            owner_cluster_id=cluster.cluster_id,
            alert_type="fleet_high_risk_average",
            vessel_ids_json=list(vessel_risk.keys()),
            evidence_json={
                "average_risk_score": round(avg_risk, 1),
                "vessel_count": len(vessel_risk),
            },
            risk_score_component=SCORE_HIGH_RISK_AVERAGE,
        )
    return None


def _check_shared_manager_different_owners(
    cluster: OwnerCluster, vessels: List[Vessel], db
) -> Optional[FleetAlert]:
    """Vessels with same owner_name on vessel record but different VesselOwner names.

    This detects cases where vessels share a manager/operator (vessel.owner_name)
    but have different registered owners in the ownership table — a common
    beneficial ownership obfuscation pattern.
    """
    if len(vessels) < 2:
        return None

    # Group vessels by their manager (vessel.owner_name field)
    manager_vessels: Dict[str, List[Vessel]] = defaultdict(list)
    for v in vessels:
        if v.owner_name:
            manager_vessels[v.owner_name.upper().strip()].append(v)

    # For each manager group, check if the registered owners differ
    for manager_name, mgr_vessels in manager_vessels.items():
        if len(mgr_vessels) < 2:
            continue
        vessel_ids = [v.vessel_id for v in mgr_vessels]
        owners = (
            db.query(VesselOwner)
            .filter(VesselOwner.vessel_id.in_(vessel_ids))
            .all()
        )
        unique_owner_names = {o.owner_name.upper().strip() for o in owners if o.owner_name}
        if len(unique_owner_names) >= 2:
            return FleetAlert(
                owner_cluster_id=cluster.cluster_id,
                alert_type="shared_manager_different_owners",
                vessel_ids_json=vessel_ids,
                evidence_json={
                    "manager_name": manager_name,
                    "distinct_owners": sorted(unique_owner_names),
                },
                risk_score_component=SCORE_SHARED_MANAGER,
            )
    return None


def _check_shared_pi_club(
    cluster: OwnerCluster, vessels: List[Vessel], db
) -> Optional[FleetAlert]:
    """Vessels sharing same P&I coverage status when cluster has high-risk signals.

    Uses pi_coverage_status on the Vessel model as a proxy for P&I club grouping.
    Only triggers when the cluster is sanctioned or has multiple risk signals.
    """
    if not cluster.is_sanctioned:
        return None
    if len(vessels) < 2:
        return None

    # Group by pi_coverage_status
    pi_groups: Dict[str, List[int]] = defaultdict(list)
    for v in vessels:
        status = getattr(v, "pi_coverage_status", None)
        if status and str(status) != "unknown":
            pi_groups[str(status)].append(v.vessel_id)

    for status, vids in pi_groups.items():
        if len(vids) >= 2:
            return FleetAlert(
                owner_cluster_id=cluster.cluster_id,
                alert_type="shared_pi_club_high_risk",
                vessel_ids_json=vids,
                evidence_json={
                    "pi_coverage_status": status,
                    "vessel_count": len(vids),
                },
                risk_score_component=SCORE_SHARED_PI_CLUB,
            )
    return None


def detect_ism_pi_continuity(db) -> dict:
    """Detect ISM manager or P&I club persistence across ownership changes.

    For each vessel with >1 owner record, checks if ism_manager or pi_club_name
    stayed the same across different owners. If so, creates a FleetAlert.

    Returns dict with {status, alerts_created}.
    """
    if not getattr(settings, "ISM_CONTINUITY_DETECTION_ENABLED", False):
        logger.info("ISM continuity detection disabled — skipping")
        return {"status": "disabled", "alerts_created": 0}

    from sqlalchemy import func as _func

    # Find vessels with more than one ownership record
    vessel_groups = (
        db.query(VesselOwner.vessel_id)
        .group_by(VesselOwner.vessel_id)
        .having(_func.count(VesselOwner.owner_id) > 1)
        .all()
    )

    alerts_created = 0
    for (vessel_id,) in vessel_groups:
        owners = (
            db.query(VesselOwner)
            .filter(VesselOwner.vessel_id == vessel_id)
            .order_by(VesselOwner.owner_id)
            .all()
        )
        if len(owners) < 2:
            continue

        # Check ISM manager continuity
        ism_values = [
            (o.ism_manager or "").strip().upper()
            for o in owners
            if (o.ism_manager or "").strip()
        ]
        owner_names = [
            (o.owner_name or "").strip().upper()
            for o in owners
        ]
        unique_owners = set(owner_names)

        if len(ism_values) >= 2 and len(unique_owners) >= 2:
            # Check if same ISM manager appears across different owners
            for i in range(len(owners) - 1):
                ism_a = (owners[i].ism_manager or "").strip().upper()
                ism_b = (owners[i + 1].ism_manager or "").strip().upper()
                owner_a = (owners[i].owner_name or "").strip().upper()
                owner_b = (owners[i + 1].owner_name or "").strip().upper()
                if ism_a and ism_b and ism_a == ism_b and owner_a != owner_b:
                    # Check dedup
                    existing = (
                        db.query(FleetAlert)
                        .filter(
                            FleetAlert.alert_type == "ism_continuity",
                            FleetAlert.vessel_ids_json.contains(vessel_id),
                        )
                        .first()
                    )
                    if existing is None:
                        alert = FleetAlert(
                            alert_type="ism_continuity",
                            vessel_ids_json=[vessel_id],
                            evidence_json={
                                "vessel_id": vessel_id,
                                "ism_manager": ism_a,
                                "owner_a": owner_a,
                                "owner_b": owner_b,
                            },
                            risk_score_component=20,
                        )
                        db.add(alert)
                        alerts_created += 1
                    break

        # Check P&I club continuity
        pi_values = [
            (o.pi_club_name or "").strip().upper()
            for o in owners
            if (o.pi_club_name or "").strip()
        ]
        if len(pi_values) >= 2 and len(unique_owners) >= 2:
            for i in range(len(owners) - 1):
                pi_a = (owners[i].pi_club_name or "").strip().upper()
                pi_b = (owners[i + 1].pi_club_name or "").strip().upper()
                owner_a = (owners[i].owner_name or "").strip().upper()
                owner_b = (owners[i + 1].owner_name or "").strip().upper()
                if pi_a and pi_b and pi_a == pi_b and owner_a != owner_b:
                    existing = (
                        db.query(FleetAlert)
                        .filter(
                            FleetAlert.alert_type == "pi_continuity",
                            FleetAlert.vessel_ids_json.contains(vessel_id),
                        )
                        .first()
                    )
                    if existing is None:
                        alert = FleetAlert(
                            alert_type="pi_continuity",
                            vessel_ids_json=[vessel_id],
                            evidence_json={
                                "vessel_id": vessel_id,
                                "pi_club_name": pi_a,
                                "owner_a": owner_a,
                                "owner_b": owner_b,
                            },
                            risk_score_component=15,
                        )
                        db.add(alert)
                        alerts_created += 1
                    break

    if alerts_created > 0:
        db.commit()

    logger.info("ISM/P&I continuity detection: %d alerts created", alerts_created)
    return {"status": "ok", "alerts_created": alerts_created}


def detect_batch_renames(db) -> dict:
    """Detect batch vessel renames — same owner renaming 3+ vessels within 30 days.

    Groups VesselHistory name changes by owner_name + 30-day window.
    If >3 vessels from the same owner renamed within 30d → FleetAlert.

    Returns dict with {status, alerts_created}.
    """
    if not getattr(settings, "RENAME_VELOCITY_DETECTION_ENABLED", False):
        logger.info("Rename velocity detection disabled — skipping")
        return {"status": "disabled", "alerts_created": 0}

    from app.models.vessel_history import VesselHistory

    # Get all name changes
    name_changes = (
        db.query(VesselHistory)
        .filter(VesselHistory.field_changed == "name")
        .order_by(VesselHistory.observed_at)
        .all()
    )

    if not name_changes:
        return {"status": "ok", "alerts_created": 0}

    # For each name change, resolve the vessel's owner at that time
    change_with_owner = []
    for ch in name_changes:
        owner = (
            db.query(VesselOwner)
            .filter(VesselOwner.vessel_id == ch.vessel_id)
            .order_by(VesselOwner.owner_id.desc())
            .first()
        )
        if owner and owner.owner_name:
            change_with_owner.append({
                "vessel_id": ch.vessel_id,
                "owner_name": owner.owner_name.strip().upper(),
                "observed_at": ch.observed_at,
            })

    # Group by owner_name
    owner_changes: Dict[str, list] = defaultdict(list)
    for item in change_with_owner:
        owner_changes[item["owner_name"]].append(item)

    alerts_created = 0
    for owner_name, changes in owner_changes.items():
        changes.sort(key=lambda x: x["observed_at"])
        # Sliding 30-day window
        for i in range(len(changes)):
            window_start = changes[i]["observed_at"]
            window_end = window_start + timedelta(days=30)
            window_vessels = set()
            for j in range(i, len(changes)):
                if changes[j]["observed_at"] > window_end:
                    break
                window_vessels.add(changes[j]["vessel_id"])
            if len(window_vessels) > 3:
                # Check dedup
                existing = (
                    db.query(FleetAlert)
                    .filter(
                        FleetAlert.alert_type == "batch_rename",
                    )
                    .first()
                )
                if existing is None:
                    alert = FleetAlert(
                        alert_type="batch_rename",
                        vessel_ids_json=list(window_vessels),
                        evidence_json={
                            "owner_name": owner_name,
                            "vessel_count": len(window_vessels),
                            "window_days": 30,
                        },
                        risk_score_component=25,
                    )
                    db.add(alert)
                    alerts_created += 1
                break  # One alert per owner is sufficient

    if alerts_created > 0:
        db.commit()

    logger.info("Batch rename detection: %d alerts created", alerts_created)
    return {"status": "ok", "alerts_created": alerts_created}


def _alert_exists(db, cluster_id: int, alert_type: str) -> bool:
    """Check if a fleet alert already exists for this cluster + type."""
    existing = (
        db.query(FleetAlert)
        .filter(
            FleetAlert.owner_cluster_id == cluster_id,
            FleetAlert.alert_type == alert_type,
        )
        .first()
    )
    return existing is not None


def run_fleet_analysis(db) -> dict:
    """Analyze owner clusters for fleet-level behavioural patterns.

    Returns dict with {status, clusters_analyzed, alerts_created}.
    """
    if not getattr(settings, "FLEET_ANALYSIS_ENABLED", False):
        logger.info("Fleet analysis disabled — skipping fleet analysis")
        return {"status": "disabled", "clusters_analyzed": 0, "alerts_created": 0}

    clusters = db.query(OwnerCluster).limit(MAX_CLUSTERS_PER_RUN).all()

    alerts_created = 0
    clusters_analyzed = 0

    for cluster in clusters:
        clusters_analyzed += 1
        vessels = _get_cluster_vessels(db, cluster)
        if not vessels:
            continue

        checks = [
            _check_sts_concentration(db, cluster, vessels),
            _check_dark_coordination(db, cluster, vessels),
            _check_flag_diversity(cluster, vessels),
            _check_high_risk_average(cluster, vessels, db),
            _check_shared_manager_different_owners(cluster, vessels, db),
            _check_shared_pi_club(cluster, vessels, db),
        ]

        for alert in checks:
            if alert is None:
                continue
            # Dedup: skip if alert already exists for this cluster+type
            if _alert_exists(db, cluster.cluster_id, alert.alert_type):
                continue
            db.add(alert)
            alerts_created += 1

    db.commit()

    logger.info(
        "Fleet analysis complete: %d clusters analyzed, %d alerts created",
        clusters_analyzed,
        alerts_created,
    )
    return {
        "status": "ok",
        "clusters_analyzed": clusters_analyzed,
        "alerts_created": alerts_created,
    }
