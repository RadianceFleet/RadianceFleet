"""Evidence card generation and export.

Generates structured evidence cards in JSON and Markdown formats.
See PRD §7.7 for evidence card specification.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.evidence_card import EvidenceCard
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# PRD §4.12, NFR7: honest regional AIS coverage displayed in every evidence card
_REGIONAL_COVERAGE: dict[str, tuple[str, str]] = {
    "Baltic": ("GOOD", "DMA CSV + aisstream.io — good terrestrial coverage"),
    "Turkish Straits": ("GOOD", "aisstream.io — well-monitored chokepoint"),
    "Black Sea": ("POOR", "No adequate free source; AIS heavily falsified in Russian-controlled areas"),
    "Persian Gulf": ("NONE", "No free AIS source; commercial subscription (Spire/exactEarth) required"),
    "Singapore": ("PARTIAL", "aisstream.io partial — gaps in outer anchorage areas"),
    "Mediterranean": ("MODERATE", "aisstream.io partial — good near ports, sparse open sea"),
    "Far East": ("PARTIAL", "aisstream.io — limited coverage outside port approaches"),
    "Nakhodka": ("PARTIAL", "aisstream.io — limited coverage outside port approaches"),
}


def _corridor_coverage(corridor_name: str | None) -> tuple[str, str]:
    """Return (quality, description) for the corridor's region."""
    if not corridor_name:
        return ("UNKNOWN", "No corridor assigned — coverage quality not determined")
    name_lower = corridor_name.lower()
    for key, val in _REGIONAL_COVERAGE.items():
        if key.lower() in name_lower:
            return val
    return ("UNKNOWN", "Region not in coverage database — verify AIS source manually")


DISCLAIMER = (
    "DISCLAIMER: This is investigative triage, not a legal determination. "
    "This tool identifies patterns warranting further investigation. "
    "No conclusions about sanctions violations or criminal activity should be drawn "
    "from this output without independent expert verification."
)


def export_evidence_card(alert_id: int, format: str, db: Session) -> dict[str, Any]:
    """
    Export an evidence card for an AIS gap event.

    Analyst review status must be set before export (NFR7).
    """
    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        return {"error": "Alert not found"}

    if gap.status == "new":
        return {
            "error": "Evidence card cannot be exported without analyst review. "
                     "Set alert status before exporting."
        }

    vessel = db.query(Vessel).filter(Vessel.vessel_id == gap.vessel_id).first()
    from app.models.corridor import Corridor
    corridor = (
        db.query(Corridor).filter(Corridor.corridor_id == gap.corridor_id).first()
        if gap.corridor_id else None
    )
    card_data = _build_card(gap, vessel, corridor=corridor, db=db)

    if format == "json":
        content = json.dumps(card_data, indent=2, default=str)
        media_type = "application/json"
    elif format == "md":
        content = _render_markdown(card_data)
        media_type = "text/markdown"
    else:
        return {"error": f"Unsupported format: {format}. Use 'json' or 'md'."}

    # Persist record with score snapshot (so rescoring won't retroactively alter exports)
    card = EvidenceCard(
        gap_event_id=alert_id,
        version=1,
        export_format=format,
        created_at=datetime.now(timezone.utc),
        score_snapshot=gap.risk_score,
        breakdown_snapshot=gap.risk_breakdown_json,
    )
    db.add(card)
    db.commit()

    return {"content": content, "media_type": media_type, "evidence_card_id": card.evidence_card_id}


def _build_card(gap: AISGapEvent, vessel: Vessel | None, corridor=None, db: Session = None) -> dict[str, Any]:
    # PRD §7.7 mandatory fields: last/first AIS points + satellite check status
    last_point = None
    first_point_after = None
    sat_check = None

    if db is not None:
        from app.models.ais_point import AISPoint
        from app.models.satellite_check import SatelliteCheck

        last_point = (
            db.query(AISPoint)
            .filter(
                AISPoint.vessel_id == gap.vessel_id,
                AISPoint.timestamp_utc <= gap.gap_start_utc,
            )
            .order_by(AISPoint.timestamp_utc.desc())
            .first()
        )
        first_point_after = (
            db.query(AISPoint)
            .filter(
                AISPoint.vessel_id == gap.vessel_id,
                AISPoint.timestamp_utc >= gap.gap_end_utc,
            )
            .order_by(AISPoint.timestamp_utc.asc())
            .first()
        )
        sat_check = (
            db.query(SatelliteCheck)
            .filter(SatelliteCheck.gap_event_id == gap.gap_event_id)
            .order_by(SatelliteCheck.sat_check_id.desc())
            .first()
        )

    return {
        "alert_id": gap.gap_event_id,
        "vessel": {
            "mmsi": vessel.mmsi if vessel else None,
            "imo": vessel.imo if vessel else None,
            "name": vessel.name if vessel else None,
            "flag": vessel.flag if vessel else None,
            "vessel_type": vessel.vessel_type if vessel else None,
        },
        "gap": {
            "start_utc": gap.gap_start_utc,
            "end_utc": gap.gap_end_utc,
            "duration_minutes": gap.duration_minutes,
        },
        "risk": {
            "score": gap.risk_score,
            "breakdown": gap.risk_breakdown_json,
        },
        "movement_envelope": {
            "max_plausible_distance_nm": gap.max_plausible_distance_nm,
            "actual_gap_distance_nm": gap.actual_gap_distance_nm,
            "velocity_plausibility_ratio": gap.velocity_plausibility_ratio,
            "impossible_speed_flag": gap.impossible_speed_flag,
        },
        "last_known_position": {
            "lat": last_point.lat,
            "lon": last_point.lon,
            "timestamp_utc": last_point.timestamp_utc.isoformat(),
            "sog": last_point.sog,
            "cog": last_point.cog,
        } if last_point else None,
        "first_position_after_gap": {
            "lat": first_point_after.lat,
            "lon": first_point_after.lon,
            "timestamp_utc": first_point_after.timestamp_utc.isoformat(),
            "sog": first_point_after.sog,
            "cog": first_point_after.cog,
        } if first_point_after else None,
        "satellite_check_status": sat_check.review_status if sat_check else "not_checked",
        "satellite_scene_refs": sat_check.scene_refs_json if sat_check else [],
        "data_sources": {
            "ais_source": (vessel.ais_source if vessel and hasattr(vessel, "ais_source") else None) or "unknown",
            "satellite_scene_refs": sat_check.scene_refs_json if sat_check else [],
            "provider": sat_check.provider if sat_check else None,
        },
        "analyst_notes": gap.analyst_notes,
        "status": gap.status,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "corridor_name": corridor.name if corridor else None,
        "corridor_type": str(corridor.corridor_type.value if hasattr(corridor.corridor_type, "value") else corridor.corridor_type) if corridor else None,
        "coverage": _corridor_coverage(corridor.name if corridor else None),
    }


def _render_markdown(card: dict[str, Any]) -> str:
    v = card["vessel"]
    g = card["gap"]
    r = card["risk"]
    env = card["movement_envelope"]

    lines = [
        f"# RadianceFleet Evidence Card — Alert #{card['alert_id']}",
        "",
        f"**Exported:** {card['exported_at']}",
        f"**Status:** {card['status']}",
        "",
        "## Vessel",
        f"- **MMSI:** {v['mmsi']}",
        f"- **IMO:** {v['imo']}",
        f"- **Name:** {v['name']}",
        f"- **Flag:** {v['flag']}",
        f"- **Type:** {v['vessel_type']}",
        "",
        "## AIS Gap",
        f"- **Start:** {g['start_utc']}",
        f"- **End:** {g['end_utc']}",
        f"- **Duration:** {g['duration_minutes']} minutes ({g['duration_minutes']/60:.1f}h)",
        "",
        "## Risk Score",
        f"**Total:** {r['score']}",
        "",
        "### Score Breakdown",
    ]
    for signal, pts in (r.get("breakdown") or {}).items():
        lines.append(f"- `{signal}`: +{pts}")

    max_d = env.get("max_plausible_distance_nm")
    act_d = env.get("actual_gap_distance_nm")
    vel_r = env.get("velocity_plausibility_ratio")
    lines += [
        "",
        "## Movement Envelope",
        f"- Max plausible distance: {max_d:.1f} nm" if max_d is not None else "- Max plausible distance: N/A",
        f"- Actual gap distance: {act_d:.1f} nm" if act_d is not None else "- Actual gap distance: N/A",
        f"- Velocity ratio: {vel_r:.2f}" if vel_r is not None else "- Velocity ratio: N/A",
        f"- Impossible speed flag: {env['impossible_speed_flag']}",
        "",
        "## AIS Boundary Points",
    ]
    lkp = card.get("last_known_position")
    if lkp:
        lines.append(f"**Last known position** (before gap):")
        lines.append(f"- Lat/Lon: {lkp['lat']}, {lkp['lon']}")
        lines.append(f"- Timestamp: {lkp['timestamp_utc']}")
        lines.append(f"- SOG: {lkp['sog']} kn  COG: {lkp['cog']}°")
    else:
        lines.append("_Last known position: unavailable_")

    fpa = card.get("first_position_after_gap")
    if fpa:
        lines.append(f"**First position after gap**:")
        lines.append(f"- Lat/Lon: {fpa['lat']}, {fpa['lon']}")
        lines.append(f"- Timestamp: {fpa['timestamp_utc']}")
        lines.append(f"- SOG: {fpa['sog']} kn  COG: {fpa['cog']}°")
    else:
        lines.append("_First position after gap: unavailable_")

    sat_status = card.get("satellite_check_status", "not_checked")
    scene_refs = card.get("satellite_scene_refs") or []
    lines += [
        "",
        "## Satellite Check",
        f"- Status: `{sat_status}`",
    ]
    if scene_refs:
        lines.append("- Scene references:")
        for ref in scene_refs:
            lines.append(f"  - {ref}")

    quality, coverage_desc = card.get("coverage", ("UNKNOWN", "No coverage data"))
    corridor_name = card.get("corridor_name") or "Unknown"
    lines += [
        "",
        "## Data Source Coverage",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Corridor / Region | {corridor_name} |",
        f"| AIS Coverage Quality | **{quality}** |",
        f"| Notes | {coverage_desc} |",
        "",
        "> ⚠ AIS coverage varies by region. Missing data does not equal suspicious behavior.",
        "> Verify independently before publishing.",
        "",
        "## Analyst Notes",
        card.get("analyst_notes") or "_No notes_",
        "",
        "---",
        "",
        f"> {card['disclaimer']}",
    ]
    return "\n".join(lines)


def export_gov_package(
    alert_id: int, db: Session, include_hunt_context: bool = True
) -> dict[str, Any]:
    """FR10: Export a structured package combining evidence card + hunt context.

    Returns a JSON-serializable dict with:
      - evidence_card: the full card dict
      - hunt_context: mission/candidate data if available
      - package_metadata: timestamps, version, disclaimer
    """
    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        return {"error": "Alert not found"}

    if gap.status == "new":
        return {
            "error": "Package cannot be exported without analyst review. "
                     "Set alert status before exporting."
        }

    vessel = db.query(Vessel).filter(Vessel.vessel_id == gap.vessel_id).first()
    from app.models.corridor import Corridor
    corridor = (
        db.query(Corridor).filter(Corridor.corridor_id == gap.corridor_id).first()
        if gap.corridor_id else None
    )
    card_data = _build_card(gap, vessel, corridor=corridor, db=db)

    hunt_context = None
    if include_hunt_context and vessel:
        hunt_context = _build_hunt_context(vessel.vessel_id, db)

    return {
        "evidence_card": card_data,
        "hunt_context": hunt_context,
        "package_metadata": {
            "package_version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "alert_id": alert_id,
            "vessel_mmsi": vessel.mmsi if vessel else None,
            "disclaimer": DISCLAIMER,
        },
    }


def _build_hunt_context(vessel_id: int, db: Session) -> dict[str, Any] | None:
    """Gather hunt mission/candidate data for a vessel, if any exists."""
    from app.models.stubs import VesselTargetProfile, SearchMission, HuntCandidate

    profile = (
        db.query(VesselTargetProfile)
        .filter(VesselTargetProfile.vessel_id == vessel_id)
        .first()
    )
    if not profile:
        return None

    missions = (
        db.query(SearchMission)
        .filter(SearchMission.vessel_id == vessel_id)
        .order_by(SearchMission.created_at.desc())
        .all()
    )
    if not missions:
        return {"profile_id": profile.profile_id, "missions": []}

    mission_data = []
    for m in missions:
        candidates = (
            db.query(HuntCandidate)
            .filter(HuntCandidate.mission_id == m.mission_id)
            .all()
        )
        cand_list = [
            {
                "candidate_id": c.candidate_id,
                "hunt_score": c.hunt_score,
                "score_breakdown": c.score_breakdown_json,
                "detection_lat": c.detection_lat,
                "detection_lon": c.detection_lon,
                "analyst_review_status": c.analyst_review_status,
            }
            for c in candidates
        ]
        mission_data.append({
            "mission_id": m.mission_id,
            "status": m.status,
            "max_radius_nm": m.max_radius_nm,
            "elapsed_hours": m.elapsed_hours,
            "center_lat": m.center_lat,
            "center_lon": m.center_lon,
            "candidates": cand_list,
        })

    return {
        "profile_id": profile.profile_id,
        "missions": mission_data,
    }
