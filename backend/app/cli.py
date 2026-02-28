"""RadianceFleet CLI — Typer-based command-line interface.

Usage:
    radiancefleet ingest ais ./data/sample.csv
    radiancefleet corridors import ./config/corridors.yaml
    radiancefleet detect-gaps --from 2026-01-01 --to 2026-02-01
    radiancefleet score-alerts
    radiancefleet serve
"""
from __future__ import annotations

import logging
import typer
from pathlib import Path
from typing import Optional
from datetime import date
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="radiancefleet",
    help="Maritime anomaly detection for shadow fleet triage.",
    no_args_is_help=True,
)
console = Console()


def _parse_date(s: Optional[str]) -> "Optional[date]":
    """Parse YYYY-MM-DD string to date, or return None."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        typer.echo(f"[error] Invalid date '{s}', expected YYYY-MM-DD", err=True)
        raise typer.Exit(1)

ingest_app = typer.Typer(help="Data ingestion commands")
app.add_typer(ingest_app, name="ingest")

corridors_app = typer.Typer(help="Corridor management commands")
app.add_typer(corridors_app, name="corridors")

satellite_app = typer.Typer(help="Satellite check commands")
app.add_typer(satellite_app, name="satellite")

watchlist_app = typer.Typer(help="Watchlist management commands")
app.add_typer(watchlist_app, name="watchlist")

export_app = typer.Typer(help="Evidence export commands")
app.add_typer(export_app, name="export")

gfw_app = typer.Typer(help="Global Fishing Watch data commands")
app.add_typer(gfw_app, name="gfw")

hunt_app = typer.Typer(help="Vessel hunt commands (FR9)")
app.add_typer(hunt_app, name="hunt")

data_app = typer.Typer(help="Data acquisition commands")
app.add_typer(data_app, name="data")


@ingest_app.command("ais")
def ingest_ais(
    filepath: Path = typer.Argument(..., help="Path to AIS CSV file"),
):
    """Ingest AIS records from a CSV file."""
    from app.database import SessionLocal
    from app.modules.ingest import ingest_ais_csv

    console.print(f"[cyan]Ingesting AIS data from {filepath}...[/cyan]")
    db = SessionLocal()
    try:
        with open(filepath, "rb") as f:
            result = ingest_ais_csv(f, db)
        console.print(f"[green]Done.[/green] Accepted: {result['accepted']}, "
                       f"Rejected: {result['rejected']}, Duplicates: {result['duplicates']}")
        if result["errors"]:
            console.print(f"[yellow]First {len(result['errors'])} errors:[/yellow]")
            for err in result["errors"][:10]:
                console.print(f"  [red]• {err}[/red]")
    finally:
        db.close()


@app.command("detect-gaps")
def detect_gaps(
    date_from: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Run AIS gap detection."""
    from app.database import SessionLocal
    from app.modules.gap_detector import run_gap_detection

    console.print("[cyan]Running gap detection...[/cyan]")
    db = SessionLocal()
    try:
        result = run_gap_detection(db, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
        console.print(f"[green]Gaps found: {result['gaps_detected']}[/green] "
                       f"across {result['vessels_processed']} vessels")
    finally:
        db.close()


@app.command("detect-spoofing")
def detect_spoofing(
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
):
    """Run AIS spoofing detection."""
    from app.database import SessionLocal
    from app.modules.gap_detector import run_spoofing_detection

    db = SessionLocal()
    try:
        result = run_spoofing_detection(db, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
        console.print(result)
    finally:
        db.close()


@app.command("score-alerts")
def score_alerts():
    """Score all unscored gap events using the risk scoring engine."""
    from app.database import SessionLocal
    from app.modules.risk_scoring import score_all_alerts

    console.print("[cyan]Scoring alerts...[/cyan]")
    db = SessionLocal()
    try:
        result = score_all_alerts(db)
        console.print(f"[green]Scored: {result['scored']} alerts[/green]")
    finally:
        db.close()


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Start the RadianceFleet API server."""
    import uvicorn
    console.print(f"[cyan]Starting RadianceFleet API at http://{host}:{port}[/cyan]")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@app.command("init-db")
def init_db():
    """Initialize the database schema and seed major ports if table is empty."""
    from app.database import init_db as _init_db, SessionLocal
    from app.models.port import Port
    console.print("[cyan]Initializing database...[/cyan]")
    _init_db()
    console.print("[green]Database initialized.[/green]")
    # Auto-seed ports if table is empty
    db = SessionLocal()
    try:
        port_count = db.query(Port).count()
        if port_count == 0:
            from scripts.seed_ports import seed_ports
            result = seed_ports(db)
            console.print(f"[green]Seeded {result['inserted']} major ports.[/green]")
        else:
            console.print(f"[dim]Ports table already has {port_count} entries — skipping seed.[/dim]")
    except Exception as e:
        console.print(f"[yellow]Port seeding skipped: {e}[/yellow]")
    finally:
        db.close()


@app.command("seed-ports")
def seed_ports_cmd():
    """Seed the ports table with ~50 major global ports (idempotent)."""
    from app.database import SessionLocal
    from scripts.seed_ports import seed_ports

    db = SessionLocal()
    try:
        result = seed_ports(db)
        console.print(f"[green]Ports seeded: {result['inserted']} inserted, {result['skipped']} already present.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@corridors_app.command("import")
def corridors_import(
    filepath: Path = typer.Argument(..., help="Path to corridors YAML file"),
):
    """Import/upsert corridors from YAML file."""
    from app.database import SessionLocal
    from app.models.corridor import Corridor
    from app.models.base import CorridorTypeEnum
    import yaml
    from shapely.geometry import shape

    db = SessionLocal()
    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)

        corridors_data = data.get("corridors", [])
        upserted = 0
        for c_data in corridors_data:
            existing = db.query(Corridor).filter(Corridor.name == c_data["name"]).first()
            geom = None
            raw_geom = c_data.get("geometry")
            if raw_geom:
                if isinstance(raw_geom, str):
                    # WKT string — validate prefix
                    upper = raw_geom.strip().upper()
                    if upper.startswith("POLYGON") or upper.startswith("MULTIPOLYGON"):
                        try:
                            from shapely import wkt as shapely_wkt
                            geom = shapely_wkt.loads(raw_geom).wkt
                        except Exception as exc:
                            logger.warning(
                                "Corridor '%s': invalid WKT geometry — %s", c_data.get("name"), exc
                            )
                    else:
                        logger.warning(
                            "Corridor '%s': geometry WKT must start with POLYGON or MULTIPOLYGON, got: %.40s",
                            c_data.get("name"), raw_geom,
                        )
                elif isinstance(raw_geom, dict):
                    # GeoJSON-like dict → convert to WKT
                    try:
                        geom = shape(raw_geom).wkt
                    except Exception as exc:
                        logger.warning(
                            "Corridor '%s': invalid GeoJSON geometry — %s", c_data.get("name"), exc
                        )

            if existing:
                existing.corridor_type = c_data.get("corridor_type", existing.corridor_type)
                existing.risk_weight = c_data.get("risk_weight", existing.risk_weight)
                existing.is_jamming_zone = c_data.get("is_jamming_zone", existing.is_jamming_zone)
                existing.description = c_data.get("description", existing.description)
                if geom:
                    existing.geometry = geom
            else:
                corridor = Corridor(
                    name=c_data["name"],
                    corridor_type=c_data.get("corridor_type", "export_route"),
                    risk_weight=c_data.get("risk_weight", 1.0),
                    is_jamming_zone=c_data.get("is_jamming_zone", False),
                    description=c_data.get("description"),
                    geometry=geom,
                )
                db.add(corridor)
            upserted += 1

        db.commit()
        console.print(f"[green]Corridors imported: {upserted}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command("correlate-corridors")
def correlate_corridors():
    """Re-run corridor correlation on all uncorrelated gaps."""
    from app.database import SessionLocal
    from app.modules.corridor_correlator import correlate_all_uncorrelated_gaps

    db = SessionLocal()
    try:
        result = correlate_all_uncorrelated_gaps(db)
        console.print(f"[green]Correlated: {result['correlated']} gaps, "
                      f"{result['dark_zone']} in dark zones[/green]")
    finally:
        db.close()


@app.command("detect-loitering")
def detect_loitering(
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
):
    """Detect loitering events and update laid-up vessel flags."""
    from app.database import SessionLocal
    from app.modules.loitering_detector import run_loitering_detection, detect_laid_up_vessels

    db = SessionLocal()
    try:
        result = run_loitering_detection(db, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
        console.print(f"[green]Loitering events: {result['loitering_events_created']} "
                      f"across {result['vessels_processed']} vessels[/green]")
        laid_up = detect_laid_up_vessels(db)
        console.print(f"[green]Laid-up vessels updated: {laid_up['laid_up_updated']}[/green]")
    finally:
        db.close()


@app.command("detect-sts")
def detect_sts(
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
):
    """Detect ship-to-ship transfer events."""
    from app.database import SessionLocal
    from app.modules.sts_detector import detect_sts_events

    db = SessionLocal()
    try:
        result = detect_sts_events(db, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
        console.print(f"[green]STS events detected: {result['sts_events_created']}[/green]")
    finally:
        db.close()


@app.command("detect-port-calls")
def detect_port_calls(
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
):
    """Detect port calls from AIS data (SOG <1kn within 3nm of port for >2h)."""
    from app.database import SessionLocal
    from app.modules.port_detector import run_port_call_detection

    db = SessionLocal()
    try:
        result = run_port_call_detection(db, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
        console.print(f"[green]Port calls detected: {result['port_calls_detected']} "
                      f"across {result['vessels_processed']} vessels[/green]")
    finally:
        db.close()


@satellite_app.command("prepare")
def satellite_prepare(
    alert: str = typer.Option(..., "--alert", help="Alert ID (e.g. ALERT_123 or just 123)"),
):
    """Prepare satellite check package for an alert."""
    from app.database import SessionLocal
    from app.modules.satellite_query import prepare_satellite_check

    # Accept both "ALERT_123" and "123" formats
    alert_id = int(alert.replace("ALERT_", ""))
    db = SessionLocal()
    try:
        result = prepare_satellite_check(alert_id, db)
        if "error" in result:
            console.print(f"[red]Error: {result['error']}[/red]")
        else:
            console.print(f"[green]Satellite check prepared: {result['sat_check_id']}[/green]")
            console.print(f"[cyan]Copernicus URL:[/cyan] {result['copernicus_url']}")
            if result.get("bounding_box"):
                bb = result["bounding_box"]
                console.print(f"[cyan]Bounding box:[/cyan] {bb['min_lat']:.3f},{bb['min_lon']:.3f} -> {bb['max_lat']:.3f},{bb['max_lon']:.3f}")
    finally:
        db.close()


@app.command("list-alerts")
def list_alerts(
    min_score: Optional[int] = typer.Option(None, "--min-score"),
    status: Optional[str] = typer.Option(None, "--status"),
    format: str = typer.Option("table", "--format", help="Output format: table or csv"),
    limit: int = typer.Option(50, "--limit"),
):
    """List alerts for quick terminal triage."""
    from app.database import SessionLocal
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel
    import csv, sys

    db = SessionLocal()
    try:
        q = db.query(AISGapEvent)
        if min_score is not None:
            q = q.filter(AISGapEvent.risk_score >= min_score)
        if status:
            q = q.filter(AISGapEvent.status == status)
        alerts = q.order_by(AISGapEvent.risk_score.desc()).limit(limit).all()

        if format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow(["alert_id", "vessel_id", "risk_score", "status", "gap_start_utc", "duration_h", "corridor_id"])
            for a in alerts:
                writer.writerow([a.gap_event_id, a.vessel_id, a.risk_score, a.status,
                                  a.gap_start_utc, round(a.duration_minutes/60, 1), a.corridor_id])
        else:
            table = Table(title=f"Alerts (top {len(alerts)})")
            table.add_column("ID", style="cyan")
            table.add_column("Score", style="bold red")
            table.add_column("Status")
            table.add_column("Duration")
            table.add_column("Gap Start UTC")
            for a in alerts:
                score_color = "red" if a.risk_score >= 76 else "yellow" if a.risk_score >= 51 else "white"
                table.add_row(
                    str(a.gap_event_id),
                    f"[{score_color}]{a.risk_score}[/{score_color}]",
                    str(a.status),
                    f"{round(a.duration_minutes/60, 1)}h",
                    str(a.gap_start_utc)[:19],
                )
            console.print(table)
    finally:
        db.close()


@app.command("search")
def search_vessel(
    mmsi: Optional[str] = typer.Option(None, "--mmsi"),
    imo: Optional[str] = typer.Option(None, "--imo"),
    name: Optional[str] = typer.Option(None, "--name"),
):
    """Find vessel by MMSI, IMO, or name and show watchlist status."""
    from app.database import SessionLocal
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist
    from app.models.ais_point import AISPoint

    db = SessionLocal()
    try:
        q = db.query(Vessel)
        if mmsi:
            q = q.filter(Vessel.mmsi == mmsi)
        elif imo:
            q = q.filter(Vessel.imo == imo)
        elif name:
            q = q.filter(Vessel.name.ilike(f"%{name}%"))
        else:
            console.print("[red]Provide --mmsi, --imo, or --name[/red]")
            raise typer.Exit(1)

        vessels = q.limit(10).all()
        if not vessels:
            console.print("[yellow]No vessels found[/yellow]")
            return

        for v in vessels:
            watchlist = db.query(VesselWatchlist).filter(
                VesselWatchlist.vessel_id == v.vessel_id, VesselWatchlist.is_active == True
            ).all()
            last_point = db.query(AISPoint).filter(
                AISPoint.vessel_id == v.vessel_id
            ).order_by(AISPoint.timestamp_utc.desc()).first()

            console.print(f"\n[bold cyan]MMSI:[/bold cyan] {v.mmsi}  [bold cyan]IMO:[/bold cyan] {v.imo}  [bold]Name:[/bold] {v.name}")
            console.print(f"  Flag: {v.flag}  Type: {v.vessel_type}  DWT: {v.deadweight}")
            if watchlist:
                sources = ", ".join(w.watchlist_source for w in watchlist)
                console.print(f"  [bold red]WATCHLIST:[/bold red] {sources}")
            if last_point:
                console.print(f"  Last seen: {last_point.timestamp_utc} at ({last_point.lat:.3f}, {last_point.lon:.3f})")
    finally:
        db.close()


@app.command("rescore-all-alerts")
def rescore_all_alerts():
    """Clear and re-compute all risk scores (use after risk_scoring.yaml changes)."""
    from app.database import SessionLocal
    from app.modules.risk_scoring import rescore_all_alerts as _rescore

    console.print("[yellow]Rescoring all alerts (this clears existing scores)...[/yellow]")
    db = SessionLocal()
    try:
        result = _rescore(db)
        console.print(f"[green]Rescored: {result['rescored']} alerts "
                      f"(config hash: {result.get('config_hash', 'n/a')})[/green]")
    finally:
        db.close()


@watchlist_app.command("import")
def watchlist_import(
    source: str = typer.Option(..., "--source", help="Source type: ofac, kse, opensanctions"),
    filepath: Path = typer.Argument(..., help="Path to watchlist file"),
):
    """Import vessels from a sanctions/watchlist file."""
    from app.database import SessionLocal
    from app.modules.watchlist_loader import load_ofac_sdn, load_kse_list, load_opensanctions

    db = SessionLocal()
    try:
        if source.lower() == "ofac":
            result = load_ofac_sdn(db, str(filepath))
        elif source.lower() == "kse":
            result = load_kse_list(db, str(filepath))
        elif source.lower() == "opensanctions":
            result = load_opensanctions(db, str(filepath))
        else:
            console.print(f"[red]Unknown source: {source}. Use: ofac, kse, opensanctions[/red]")
            raise typer.Exit(1)

        console.print(f"[green]Watchlist import complete: {result}[/green]")
    finally:
        db.close()


@export_app.command("evidence")
def cli_export_evidence(
    alert: int = typer.Option(..., "--alert", help="Alert ID (gap_event_id)"),
    format: str = typer.Option("md", "--format", help="Output format: md or json"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to file (default: stdout)"),
):
    """Export evidence card for an alert.

    Alert must not be in 'new' status (requires analyst review first — NFR7).
    """
    from app.modules.evidence_export import export_evidence_card
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = export_evidence_card(alert, format, db)
    finally:
        db.close()

    if "error" in result:
        typer.echo(f"[error] {result['error']}", err=True)
        raise typer.Exit(1)

    content = result["content"]
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        typer.echo(f"[ok] Evidence card written to {output}")
    else:
        typer.echo(content)


@export_app.command("gov-package")
def cli_export_gov_package(
    alert: int = typer.Option(..., "--alert", help="Alert ID (gap_event_id)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to file (default: stdout)"),
):
    """Export gov alert package (evidence card + hunt context) as JSON.

    Alert must not be in 'new' status (requires analyst review first — NFR7).
    """
    from app.modules.evidence_export import export_gov_package
    from app.database import SessionLocal
    import json

    db = SessionLocal()
    try:
        result = export_gov_package(alert, db)
    finally:
        db.close()

    if "error" in result:
        typer.echo(f"[error] {result['error']}", err=True)
        raise typer.Exit(1)

    content = json.dumps(result, indent=2, default=str)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        typer.echo(f"[ok] Gov package written to {output}")
    else:
        typer.echo(content)


@gfw_app.command("import")
def cli_gfw_import(
    filepath: str = typer.Argument(..., help="Path to GFW vessel detections CSV"),
):
    """Import pre-computed GFW vessel detections (FR8).

    Download from: https://globalfishingwatch.org/data-download/
    Expected CSV columns: detect_id, timestamp, lat, lon, vessel_length_m, vessel_score, vessel_type
    """
    from app.modules.gfw_import import ingest_gfw_csv
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        stats = ingest_gfw_csv(db, filepath)
    finally:
        db.close()

    typer.echo(
        f"[ok] GFW import: {stats['total']} rows — "
        f"{stats['matched']} AIS-matched, {stats['dark']} dark ships, "
        f"{stats['rejected']} rejected"
    )


# ---------------------------------------------------------------------------
# Vessel Hunt (FR9)
# ---------------------------------------------------------------------------

@hunt_app.command("create-target")
def hunt_create_target(
    vessel: str = typer.Option(..., "--vessel", help="Vessel ID or MMSI"),
):
    """Register a vessel as a hunt target."""
    from app.database import SessionLocal
    from app.modules.vessel_hunt import create_target_profile
    from app.models.vessel import Vessel as VesselModel

    db = SessionLocal()
    try:
        # Try as vessel_id first, then as MMSI
        try:
            vessel_id = int(vessel)
        except ValueError:
            v = db.query(VesselModel).filter(VesselModel.mmsi == vessel).first()
            if not v:
                console.print(f"[red]Vessel not found: {vessel}[/red]")
                raise typer.Exit(1)
            vessel_id = v.vessel_id

        profile = create_target_profile(vessel_id, db)
        console.print(f"[green]Target profile created: profile_id={profile.profile_id}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@hunt_app.command("create-mission")
def hunt_create_mission(
    target: int = typer.Option(..., "--target", help="Target profile ID"),
    date_from: str = typer.Option(..., "--from", help="Search start (ISO 8601)"),
    date_to: str = typer.Option(..., "--to", help="Search end (ISO 8601)"),
):
    """Create a search mission with drift ellipse for a target."""
    from app.database import SessionLocal
    from app.modules.vessel_hunt import create_search_mission
    from datetime import datetime as dt

    db = SessionLocal()
    try:
        start = dt.fromisoformat(date_from)
        end = dt.fromisoformat(date_to)
        mission = create_search_mission(target, start, end, db)
        console.print(f"[green]Mission created: mission_id={mission.mission_id}, "
                      f"radius={mission.max_radius_nm:.1f} nm, "
                      f"elapsed={mission.elapsed_hours:.1f}h[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@hunt_app.command("find-candidates")
def hunt_find_candidates(
    mission: int = typer.Option(..., "--mission", help="Mission ID"),
):
    """Find and score dark vessel detections within mission drift ellipse."""
    from app.database import SessionLocal
    from app.modules.vessel_hunt import find_hunt_candidates

    db = SessionLocal()
    try:
        candidates = find_hunt_candidates(mission, db)
        console.print(f"[green]Found {len(candidates)} candidates[/green]")
        for c in candidates:
            band = (c.score_breakdown_json or {}).get("band", "?")
            console.print(f"  candidate_id={c.candidate_id} score={c.hunt_score:.1f} band={band}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@hunt_app.command("list-missions")
def hunt_list_missions(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status: pending_imagery, reviewed"),
):
    """List vessel hunt missions."""
    from app.database import SessionLocal
    from app.models.stubs import SearchMission

    db = SessionLocal()
    try:
        q = db.query(SearchMission)
        if status:
            q = q.filter(SearchMission.status == status)
        missions = q.order_by(SearchMission.created_at.desc()).all()

        if not missions:
            console.print("[yellow]No missions found[/yellow]")
            return

        table = Table(title="Hunt Missions")
        table.add_column("ID", style="cyan")
        table.add_column("Vessel ID")
        table.add_column("Status")
        table.add_column("Radius (nm)")
        table.add_column("Created")
        for m in missions:
            table.add_row(
                str(m.mission_id),
                str(m.vessel_id),
                m.status,
                f"{m.max_radius_nm:.1f}" if m.max_radius_nm else "?",
                str(m.created_at)[:19] if m.created_at else "",
            )
        console.print(table)
    finally:
        db.close()


@hunt_app.command("confirm")
def hunt_confirm(
    mission: int = typer.Option(..., "--mission", help="Mission ID"),
    candidate: int = typer.Option(..., "--candidate", help="Candidate ID"),
):
    """Confirm a hunt candidate and finalize the mission."""
    from app.database import SessionLocal
    from app.modules.vessel_hunt import finalize_mission

    db = SessionLocal()
    try:
        result = finalize_mission(mission, candidate, db)
        console.print(f"[green]Mission {result.mission_id} finalized — status: {result.status}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Setup — one command to working state
# ---------------------------------------------------------------------------

@app.command("setup")
def setup(
    with_sample_data: bool = typer.Option(False, "--with-sample-data", help="Load 7 synthetic vessels for demo"),
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Skip downloading watchlists from the internet"),
    stream_duration: str = typer.Option("15m", "--stream-duration", help="AIS stream duration (e.g. 30s, 5m, 1h)."),
):
    """Bootstrap RadianceFleet from scratch: init DB, import corridors, fetch data, run detection.

    Requires free API keys: AISSTREAM_API_KEY, GFW_API_TOKEN, AISHUB_USERNAME,
    COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET. Use --with-sample-data
    to run in demo mode without API keys.

    Non-interactive execution order:
      1. Check Python >= 3.11 + validate required API keys
      2. Init DB + seed ports
      3. Import corridors from config/corridors.yaml
      4. Fetch watchlists (OFAC + OpenSanctions) — unless --skip-fetch
      5. Import watchlists
      6. Stream AIS data from aisstream.io for --stream-duration
      7. Fetch GFW SAR detections for corridors (last 30d)
      8. Fetch AISHub latest positions for corridor areas
      9. Enrich vessels missing DWT/year_built/IMO via GFW
      10. Infer AIS class from transmission intervals
      11. Re-match watchlists against vessels created during streaming
      12. Fetch PSC detention records (FTM + EMSA)
      13. If --with-sample-data AND no AIS data yet → generate/ingest sample data
      14. Run detection pipeline (gaps → spoofing → loitering → STS → corridors → score)
      15. Prepare + enhance satellite checks via Copernicus for high-risk alerts
    """
    import sys

    total_steps = 15
    console.print("[bold cyan]RadianceFleet Setup[/bold cyan]\n")

    # 1. Check Python version
    v = sys.version_info
    if v < (3, 11):
        console.print(f"[red]Python >= 3.11 required (found {v.major}.{v.minor})[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Python {v.major}.{v.minor}.{v.micro}")

    # 1b. Validate required API keys (all are free — no excuse to skip them)
    from app.config import settings as _settings
    if not with_sample_data:
        missing_keys: list[str] = []
        if not _settings.AISSTREAM_API_KEY:
            missing_keys.append("AISSTREAM_API_KEY       (free: https://aisstream.io/)")
        if not _settings.GFW_API_TOKEN:
            missing_keys.append("GFW_API_TOKEN           (free: https://globalfishingwatch.org/our-apis/)")
        if not _settings.COPERNICUS_CLIENT_ID or not _settings.COPERNICUS_CLIENT_SECRET:
            missing_keys.append("COPERNICUS_CLIENT_ID/SECRET (free: https://dataspace.copernicus.eu/)")
        if missing_keys:
            console.print("[red]Missing required API keys:[/red]")
            for mk in missing_keys:
                console.print(f"  [red]✗[/red] {mk}")
            console.print("\n[dim]All data sources are free. Set them in .env or environment variables.[/dim]")
            console.print("[dim]For demo mode without API keys: radiancefleet setup --with-sample-data[/dim]")
            raise typer.Exit(1)
        console.print("[green]✓[/green] All required API keys present")
        if not _settings.AISHUB_USERNAME:
            console.print("[dim]  Optional: AISHUB_USERNAME not set (requires AIS receiver hardware)[/dim]")

    # 2. Initialize database
    console.print(f"\n[cyan]Step 1/{total_steps}: Initializing database...[/cyan]")
    try:
        from app.database import init_db as _init_db, SessionLocal
        _init_db()
        console.print("[green]✓[/green] Database initialized")
    except Exception as e:
        console.print(f"[red]✗ Database init failed: {e}[/red]")
        console.print("[yellow]Hint: Check DATABASE_URL in .env (default: sqlite:///radiancefleet.db)[/yellow]")
        raise typer.Exit(1)

    db = SessionLocal()
    try:
        # 2b. Seed ports if empty
        from app.models.port import Port
        port_count = db.query(Port).count()
        if port_count == 0:
            try:
                from scripts.seed_ports import seed_ports
                result = seed_ports(db)
                console.print(f"[green]✓[/green] Seeded {result['inserted']} ports")
            except Exception as e:
                console.print(f"[yellow]⚠ Port seeding skipped: {e}[/yellow]")
        else:
            console.print(f"[dim]  Ports already seeded ({port_count})[/dim]")

        # 3. Import corridors
        console.print(f"\n[cyan]Step 2/{total_steps}: Importing corridors...[/cyan]")
        try:
            from app.models.corridor import Corridor
            import yaml
            config_path = Path("../config/corridors.yaml")
            if not config_path.exists():
                config_path = Path("config/corridors.yaml")
            if not config_path.exists():
                console.print("[yellow]⚠ corridors.yaml not found, skipping[/yellow]")
            else:
                with open(config_path) as f:
                    data = yaml.safe_load(f)
                corridors_data = data.get("corridors", [])
                existing_count = db.query(Corridor).count()
                if existing_count >= len(corridors_data):
                    console.print(f"[dim]  Corridors already imported ({existing_count})[/dim]")
                else:
                    # Delegate to the corridors import command logic
                    from shapely.geometry import shape as shapely_shape
                    upserted = 0
                    for c_data in corridors_data:
                        existing = db.query(Corridor).filter(Corridor.name == c_data["name"]).first()
                        geom = None
                        raw_geom = c_data.get("geometry")
                        if raw_geom and isinstance(raw_geom, dict):
                            try:
                                geom = shapely_shape(raw_geom).wkt
                            except Exception:
                                pass
                        elif raw_geom and isinstance(raw_geom, str):
                            try:
                                from shapely import wkt as shapely_wkt
                                geom = shapely_wkt.loads(raw_geom).wkt
                            except Exception:
                                pass

                        if not existing:
                            corridor = Corridor(
                                name=c_data["name"],
                                corridor_type=c_data.get("corridor_type", "export_route"),
                                risk_weight=c_data.get("risk_weight", 1.0),
                                is_jamming_zone=c_data.get("is_jamming_zone", False),
                                description=c_data.get("description"),
                                geometry=geom,
                            )
                            db.add(corridor)
                            upserted += 1
                    db.commit()
                    console.print(f"[green]✓[/green] Imported {upserted} corridors")
        except Exception as e:
            console.print(f"[yellow]⚠ Corridor import failed: {e}[/yellow]")

        # 4. Fetch watchlists
        if not skip_fetch:
            console.print(f"\n[cyan]Step 3/{total_steps}: Downloading watchlists...[/cyan]")
            try:
                from app.modules.data_fetcher import fetch_all
                results = fetch_all()
                for source, res in [("OFAC", results["ofac"]), ("OpenSanctions", results["opensanctions"])]:
                    if res["status"] == "downloaded":
                        console.print(f"[green]✓[/green] {source} downloaded to {res['path']}")
                    elif res["status"] == "up_to_date":
                        console.print(f"[dim]  {source} already up to date[/dim]")
                    elif res["status"] == "error":
                        console.print(f"[yellow]⚠ {source}: {res['error']}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠ Data fetch failed: {e}[/yellow]")
                console.print("[dim]  You can fetch later with: radiancefleet data fetch[/dim]")

            # 5. Import downloaded watchlists
            console.print(f"\n[cyan]Step 4/{total_steps}: Importing watchlists...[/cyan]")
            try:
                from app.modules.data_fetcher import _find_latest
                from app.modules.watchlist_loader import load_ofac_sdn, load_opensanctions
                from app.config import settings as _settings

                data_dir = Path(_settings.DATA_DIR)
                ofac_file = _find_latest(data_dir, "ofac_sdn_")
                if ofac_file:
                    result = load_ofac_sdn(db, str(ofac_file))
                    console.print(f"[green]✓[/green] OFAC: {result.get('matched', 0)} matched")

                os_file = _find_latest(data_dir, "opensanctions_vessels_")
                if os_file:
                    result = load_opensanctions(db, str(os_file))
                    console.print(f"[green]✓[/green] OpenSanctions: {result.get('matched', 0)} matched")

                if not ofac_file and not os_file:
                    console.print("[dim]  No watchlist files found to import[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠ Watchlist import failed: {e}[/yellow]")
        else:
            console.print(f"\n[dim]Step 3/{total_steps}: Skipping data fetch (--skip-fetch)[/dim]")
            console.print(f"[dim]Step 4/{total_steps}: Skipping watchlist import (--skip-fetch)[/dim]")

        # 6. Stream AIS data from aisstream.io
        console.print(f"\n[cyan]Step 5/{total_steps}: AIS data streaming (aisstream.io)...[/cyan]")
        if _settings.AISSTREAM_API_KEY:
            try:
                import asyncio
                from app.modules.aisstream_client import stream_ais, get_corridor_bounding_boxes

                boxes = get_corridor_bounding_boxes(db)
                duration_s = _parse_duration(stream_duration)
                console.print(f"  Streaming for {stream_duration} ({duration_s}s) across {len(boxes)} corridor boxes...")

                ais_result = asyncio.run(stream_ais(
                    api_key=_settings.AISSTREAM_API_KEY,
                    bounding_boxes=boxes,
                    duration_seconds=duration_s,
                    batch_interval=_settings.AISSTREAM_BATCH_INTERVAL,
                ))
                points_stored = ais_result['points_stored']
                vessels_seen = ais_result['vessels_seen']
                static_vessels = ais_result.get('static_vessels', 0)
                console.print(
                    f"[green]✓[/green] Streamed {points_stored} AIS points "
                    f"from {vessels_seen} vessels"
                    + (f" ({static_vessels} metadata-only)" if static_vessels else "")
                )
                if points_stored == 0 and ais_result.get('messages_received', 0) > 0:
                    console.print("[yellow]  ⚠ Received messages but stored 0 position reports[/yellow]")
                    console.print(
                        f"[dim]    Total messages: {ais_result['messages_received']}, "
                        f"Position reports: {ais_result.get('position_reports', 0)}, "
                        f"Static data: {ais_result.get('static_data_msgs', 0)}, "
                        f"Batch errors: {ais_result.get('batch_errors', 0)}[/dim]"
                    )
            except Exception as e:
                console.print(f"[yellow]⚠ AIS streaming failed: {e}[/yellow]")
        else:
            console.print("[dim]  Skipping AIS streaming (demo mode)[/dim]")

        # 7. Fetch GFW SAR detections
        console.print(f"\n[cyan]Step 6/{total_steps}: GFW SAR detections...[/cyan]")
        if _settings.GFW_API_TOKEN:
            try:
                from app.modules.gfw_client import get_sar_detections, import_sar_detections_to_db
                from app.modules.aisstream_client import get_corridor_bounding_boxes

                boxes = get_corridor_bounding_boxes(db)
                if boxes:
                    all_lats = [b[0][0] for b in boxes] + [b[1][0] for b in boxes]
                    all_lons = [b[0][1] for b in boxes] + [b[1][1] for b in boxes]
                    merged_bbox = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
                else:
                    merged_bbox = (54.0, 10.0, 66.0, 30.0)

                detections = get_sar_detections(merged_bbox, _settings.GFW_API_TOKEN)
                if detections:
                    gfw_result = import_sar_detections_to_db(detections, db)
                    console.print(
                        f"[green]✓[/green] GFW: {gfw_result['dark']} dark detections, "
                        f"{gfw_result['matched']} matched"
                    )
                else:
                    console.print("[dim]  No GFW detections found for corridor areas[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠ GFW fetch failed: {e}[/yellow]")
        else:
            console.print("[dim]  Skipping GFW SAR detections (demo mode)[/dim]")

        # 8. Fetch AISHub positions
        console.print(f"\n[cyan]Step 7/{total_steps}: AISHub batch positions...[/cyan]")
        if _settings.AISHUB_USERNAME:
            try:
                from app.modules.aishub_client import fetch_area_positions, ingest_aishub_positions
                from app.modules.aisstream_client import get_corridor_bounding_boxes

                boxes = get_corridor_bounding_boxes(db)
                if boxes:
                    all_lats = [b[0][0] for b in boxes] + [b[1][0] for b in boxes]
                    all_lons = [b[0][1] for b in boxes] + [b[1][1] for b in boxes]
                    merged_bbox = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
                else:
                    merged_bbox = (54.0, 10.0, 66.0, 30.0)

                positions = fetch_area_positions(merged_bbox, _settings.AISHUB_USERNAME)
                if positions:
                    hub_result = ingest_aishub_positions(positions, db)
                    console.print(
                        f"[green]✓[/green] AISHub: {hub_result['stored']} positions stored "
                        f"({hub_result['vessels_created']} new vessels)"
                    )
                else:
                    console.print("[dim]  No AISHub positions found for corridor areas[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠ AISHub fetch failed: {e}[/yellow]")
        else:
            console.print("[dim]  AISHUB_USERNAME not set — skipping (requires AIS receiver: https://www.aishub.net/)[/dim]")

        # 8. Enrich vessel metadata via GFW
        console.print(f"\n[cyan]Step 8/{total_steps}: Vessel metadata enrichment...[/cyan]")
        if _settings.GFW_API_TOKEN:
            try:
                from app.modules.vessel_enrichment import enrich_vessels_from_gfw
                enrich_result = enrich_vessels_from_gfw(db, limit=50)
                console.print(
                    f"[green]✓[/green] Enriched {enrich_result['enriched']} vessels "
                    f"(skipped {enrich_result['skipped']}, failed {enrich_result['failed']})"
                )
                # Auto-rescore if enrichment updated vessel metadata
                if enrich_result.get("enriched", 0) > 0:
                    from app.modules.risk_scoring import score_all_alerts
                    rescore = score_all_alerts(db)
                    console.print(f"  Re-scored {rescore.get('scored', 0)} alerts with enriched data")
            except Exception as e:
                console.print(f"[yellow]⚠ Vessel enrichment failed: {e}[/yellow]")
        else:
            console.print("[dim]  Skipping vessel enrichment (demo mode)[/dim]")

        # 9. Infer AIS class from transmission intervals
        console.print(f"\n[cyan]Step 9/{total_steps}: Inferring AIS class from transmission intervals...[/cyan]")
        try:
            from app.modules.vessel_enrichment import infer_ais_class_batch
            ais_class_result = infer_ais_class_batch(db)
            console.print(
                f"[green]✓[/green] AIS class: updated {ais_class_result['updated']}, "
                f"skipped {ais_class_result['skipped']}"
            )
        except Exception as e:
            console.print(f"[yellow]⚠ AIS class inference failed: {e}[/yellow]")

        # 10. Re-match watchlists against vessels created during AIS streaming
        if not skip_fetch:
            from app.models.vessel import Vessel as _VesselCount
            vessel_count = db.query(_VesselCount).count()
            console.print(f"\n[cyan]Step 10/{total_steps}: Re-matching watchlists ({vessel_count} vessels)...[/cyan]")
            try:
                from app.modules.data_fetcher import _find_latest
                from app.modules.watchlist_loader import load_ofac_sdn, load_opensanctions
                from app.config import settings as _wl_settings

                data_dir = Path(_wl_settings.DATA_DIR)
                ofac_file = _find_latest(data_dir, "ofac_sdn_")
                if ofac_file:
                    result = load_ofac_sdn(db, str(ofac_file))
                    console.print(f"[green]✓[/green] OFAC re-match: {result.get('matched', 0)} matched")

                os_file = _find_latest(data_dir, "opensanctions_vessels_")
                if os_file:
                    result = load_opensanctions(db, str(os_file))
                    console.print(f"[green]✓[/green] OpenSanctions re-match: {result.get('matched', 0)} matched")

                if not ofac_file and not os_file:
                    console.print("[dim]  No watchlist files to re-match[/dim]")

                # 7b: Re-score after watchlist re-match (watchlist matches don't update scores otherwise)
                try:
                    from app.modules.risk_scoring import score_all_alerts as _score_wl
                    wl_rescore = _score_wl(db)
                    if wl_rescore.get("scored", 0) > 0:
                        console.print(f"  Re-scored {wl_rescore['scored']} alerts after watchlist re-match")
                except Exception:
                    pass  # Non-fatal
            except Exception as e:
                console.print(f"[yellow]⚠ Watchlist re-match failed: {e}[/yellow]")
        else:
            console.print(f"\n[dim]Step 10/{total_steps}: Skipping watchlist re-match (--skip-fetch)[/dim]")

        # 11. PSC data download and import
        console.print(f"\n[cyan]Step 11/{total_steps}: PSC data (detention records)...[/cyan]")
        try:
            from app.modules.data_fetcher import fetch_psc_ftm, fetch_emsa_bans
            psc_result = fetch_psc_ftm()
            if psc_result.get("files"):
                from app.modules.psc_loader import load_psc_ftm
                for source_key, psc_path in psc_result["files"].items():
                    if psc_path:
                        ftm_result = load_psc_ftm(db, str(psc_path), source=source_key)
                        console.print(
                            f"[green]✓[/green] PSC {source_key}: "
                            f"{ftm_result.get('matched', 0)} matched, "
                            f"{ftm_result.get('recent', 0)} recent"
                        )
            if psc_result.get("errors"):
                for err in psc_result["errors"]:
                    console.print(f"[dim]  PSC warning: {err}[/dim]")

            emsa_result = fetch_emsa_bans()
            if emsa_result.get("path"):
                from app.modules.psc_loader import load_emsa_bans
                emsa_load = load_emsa_bans(db, str(emsa_result["path"]))
                console.print(
                    f"[green]✓[/green] EMSA bans: "
                    f"{emsa_load.get('matched', 0)} matched, "
                    f"{emsa_load.get('flagged', 0)} flagged"
                )
            elif emsa_result.get("error"):
                console.print(f"[dim]  EMSA: {emsa_result['error']}[/dim]")
        except Exception as exc:
            console.print(f"[dim]  PSC data skipped: {exc}[/dim]")

        # 12. Sample data fallback (if no AIS data yet)
        from app.models.ais_point import AISPoint
        ais_count = db.query(AISPoint).count()
        console.print(f"\n[cyan]Step 12/{total_steps}: Sample data...[/cyan]")
        if with_sample_data and ais_count == 0:
            try:
                from app.modules.ingest import ingest_ais_csv
                sample_path = Path("scripts/sample_ais.csv")
                if not sample_path.exists():
                    import subprocess
                    subprocess.run(
                        [sys.executable, "scripts/generate_sample_data.py"],
                        check=True, capture_output=True,
                    )
                if sample_path.exists():
                    with open(sample_path, "rb") as f:
                        result = ingest_ais_csv(f, db)
                    console.print(
                        f"[green]✓[/green] Ingested {result['accepted']} sample AIS points "
                        f"(7 vessels)"
                    )
                else:
                    console.print("[yellow]⚠ Sample data script not found[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠ Sample data failed: {e}[/yellow]")
        elif with_sample_data and ais_count > 0:
            console.print(f"[dim]  Skipping sample data — {ais_count} AIS points already in DB[/dim]")
        elif not with_sample_data and ais_count == 0:
            console.print("[yellow]⚠ No AIS data after streaming — APIs may have returned no data for configured corridors[/yellow]")
            console.print("[dim]  Try: radiancefleet setup --stream-duration 30m (longer streaming window)[/dim]")
        else:
            console.print("[dim]  No sample data requested[/dim]")

        # 13. Run detection pipeline (only if we have AIS data)
        ais_count = db.query(AISPoint).count()  # re-check after possible ingest
        console.print(f"\n[cyan]Step 13/{total_steps}: Running detection pipeline...[/cyan]")
        if ais_count == 0:
            console.print("[bold yellow]⚠ WARNING: No AIS data ingested after all steps.[/bold yellow]")
            console.print("[yellow]  The detection pipeline cannot run without AIS data.[/yellow]")
            console.print("[dim]  Try a longer streaming window: radiancefleet setup --stream-duration 30m[/dim]")
        else:
            try:
                from app.modules.gap_detector import run_gap_detection, run_spoofing_detection
                from app.modules.loitering_detector import run_loitering_detection
                from app.modules.sts_detector import detect_sts_events
                from app.modules.corridor_correlator import correlate_all_uncorrelated_gaps
                from app.modules.risk_scoring import score_all_alerts

                # 7d: Watchlist staleness check before scoring
                try:
                    from app.modules.data_fetcher import _load_metadata
                    _wl_meta = _load_metadata(Path(_settings.DATA_DIR))
                    from datetime import datetime as _dt_check, timedelta as _td_check
                    for _src_key in ("ofac", "opensanctions"):
                        _dl_at = _wl_meta.get(_src_key, {}).get("downloaded_at")
                        if _dl_at:
                            _dl_date = _dt_check.fromisoformat(_dl_at)
                            _age_days = (_dt_check.now() - _dl_date).days
                            if _age_days > 7:
                                console.print(f"[yellow]⚠ {_src_key} watchlist is {_age_days} days old — consider: radiancefleet data fetch[/yellow]")
                except Exception:
                    pass  # Non-fatal

                gaps = run_gap_detection(db)
                console.print(f"  Gaps: {gaps['gaps_detected']}")

                spoof = run_spoofing_detection(db)
                console.print(f"  Spoofing: {spoof}")

                loiter = run_loitering_detection(db)
                console.print(f"  Loitering: {loiter['loitering_events_created']}")

                sts = detect_sts_events(db)
                console.print(f"  STS: {sts['sts_events_created']}")

                corr = correlate_all_uncorrelated_gaps(db)
                console.print(f"  Corridors: {corr['correlated']} correlated")

                scored = score_all_alerts(db)
                console.print(f"  Scored: {scored['scored']} alerts")

                # 7a: Alert summary
                from app.models.gap_event import AISGapEvent as _GapSummary
                all_alerts = db.query(_GapSummary).filter(_GapSummary.risk_score > 0).all()
                publishable = sum(1 for a in all_alerts if 51 <= a.risk_score <= 75)
                critical = sum(1 for a in all_alerts if a.risk_score >= 76)
                if publishable or critical:
                    console.print(f"\n  [bold]Alert summary:[/bold] {critical} critical (76+), {publishable} publishable (51-75)")
                if ais_count > 100 and gaps.get("gaps_detected", 0) == 0:
                    console.print("[yellow]⚠ {0} AIS points but 0 gaps detected — check gap detector thresholds[/yellow]".format(ais_count))

                # Identity merge detection + MMSI cloning (after scoring, before satellite)
                try:
                    from app.modules.identity_resolver import detect_merge_candidates as _detect_merges
                    merge_result = _detect_merges(db)
                    console.print(
                        f"  Merge candidates: {merge_result.get('candidates_created', 0)} created, "
                        f"{merge_result.get('auto_merged', 0)} auto-merged"
                    )
                except Exception as e:
                    console.print(f"[dim]  Merge detection skipped: {e}[/dim]")

                try:
                    from app.modules.mmsi_cloning_detector import detect_mmsi_cloning as _detect_cloning
                    clone_result = _detect_cloning(db)
                    if clone_result:
                        console.print(f"  MMSI cloning: {len(clone_result)} anomalies detected")
                    else:
                        console.print("[dim]  No MMSI cloning detected[/dim]")
                except Exception as e:
                    console.print(f"[dim]  Cloning detection skipped: {e}[/dim]")

                console.print("[green]✓[/green] Detection pipeline complete")
            except Exception as e:
                console.print(f"[yellow]⚠ Detection pipeline error: {e}[/yellow]")

        # 15. Copernicus satellite checks for high-risk alerts
        console.print(f"\n[cyan]Step 14/{total_steps}: Satellite checks (Copernicus Sentinel-1)...[/cyan]")
        if _settings.COPERNICUS_CLIENT_ID and _settings.COPERNICUS_CLIENT_SECRET:
            try:
                from app.models.gap_event import AISGapEvent as _GapSat
                from app.modules.satellite_query import prepare_satellite_check
                from app.modules.copernicus_client import enhance_satellite_check

                high_risk = (
                    db.query(_GapSat)
                    .filter(_GapSat.risk_score >= 51)
                    .order_by(_GapSat.risk_score.desc())
                    .limit(20)
                    .all()
                )
                if high_risk:
                    prepared = 0
                    enhanced = 0
                    for alert in high_risk:
                        try:
                            prepare_satellite_check(alert.gap_event_id, db)
                            prepared += 1
                            result = enhance_satellite_check(alert.gap_event_id, db)
                            if result.get("scenes"):
                                enhanced += 1
                        except Exception:
                            continue
                    db.commit()
                    console.print(
                        f"[green]✓[/green] Satellite: {prepared} checks prepared, "
                        f"{enhanced} with Sentinel-1 scenes found"
                    )
                else:
                    console.print("[dim]  No high-risk alerts (score >= 51) to check[/dim]")
            except Exception as e:
                console.print(f"[yellow]⚠ Satellite check failed: {e}[/yellow]")
        else:
            console.print("[dim]  Skipping satellite checks (demo mode)[/dim]")
    finally:
        db.close()

    # Summary
    console.print(f"\n[cyan]Step 15/{total_steps}: Summary[/cyan]")
    console.print("─" * 50)
    console.print("[bold green]Setup complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  1. Start the API server:   [cyan]radiancefleet serve[/cyan]")
    console.print("  2. Start the frontend:     [cyan]cd frontend && npm install && npm run dev[/cyan]")
    console.print("  3. Open the web UI:        [cyan]http://localhost:5173[/cyan]")


# ---------------------------------------------------------------------------
# Data acquisition commands
# ---------------------------------------------------------------------------

@data_app.command("fetch")
def data_fetch(
    source: str = typer.Option("all", "--source", help="Source to fetch: ofac, opensanctions, or all"),
    output_dir: str = typer.Option(None, "--output-dir", help="Download directory (default: DATA_DIR setting)"),
    force: bool = typer.Option(False, "--force", help="Skip ETag check, re-download even if unchanged"),
):
    """Download watchlist data from public URLs.

    Uses conditional GET (ETag/Last-Modified) to skip downloads when the
    remote file hasn't changed. Pass --force to re-download regardless.
    """
    from app.modules.data_fetcher import fetch_ofac_sdn, fetch_opensanctions_vessels, fetch_all

    out = Path(output_dir) if output_dir else None

    if source.lower() == "all":
        results = fetch_all(out, force=force)
        for src, res in [("OFAC SDN", results["ofac"]), ("OpenSanctions", results["opensanctions"])]:
            _print_fetch_result(src, res)
        if results["errors"]:
            raise typer.Exit(1)
    elif source.lower() == "ofac":
        res = fetch_ofac_sdn(out, force=force)
        _print_fetch_result("OFAC SDN", res)
        if res["status"] == "error":
            raise typer.Exit(1)
    elif source.lower() == "opensanctions":
        res = fetch_opensanctions_vessels(out, force=force)
        _print_fetch_result("OpenSanctions", res)
        if res["status"] == "error":
            raise typer.Exit(1)
    else:
        console.print(f"[red]Unknown source: {source}. Use: ofac, opensanctions, or all[/red]")
        raise typer.Exit(1)


def _print_fetch_result(label: str, result: dict) -> None:
    """Print a human-readable download result."""
    status = result["status"]
    if status == "downloaded":
        console.print(f"[green]✓ {label}:[/green] Downloaded to {result['path']}")
    elif status == "up_to_date":
        console.print(f"[dim]  {label}: Already up to date (last: {result.get('last_download', '?')})[/dim]")
    elif status == "error":
        console.print(f"[red]✗ {label}:[/red] {result['error']}")


@data_app.command("refresh")
def data_refresh(
    source: str = typer.Option("all", "--source", help="Source to refresh: ofac, opensanctions, or all"),
    detect: bool = typer.Option(True, "--detect/--no-detect", help="Run detection pipeline after import"),
):
    """Fetch latest watchlists, import them, and optionally run detection.

    One-command workflow: download → import → detect → score.
    """
    from app.database import SessionLocal
    from app.modules.data_fetcher import fetch_all, _find_latest
    from app.modules.watchlist_loader import load_ofac_sdn, load_opensanctions
    from app.config import settings

    # 1. Fetch
    console.print("[cyan]Fetching latest data...[/cyan]")
    results = fetch_all()
    for src, res in [("OFAC", results["ofac"]), ("OpenSanctions", results["opensanctions"])]:
        _print_fetch_result(src, res)

    # 2. Import
    console.print("\n[cyan]Importing watchlists...[/cyan]")
    db = SessionLocal()
    try:
        data_dir = Path(settings.DATA_DIR)

        if source.lower() in ("all", "ofac"):
            ofac_file = _find_latest(data_dir, "ofac_sdn_")
            if ofac_file:
                result = load_ofac_sdn(db, str(ofac_file))
                console.print(f"[green]OFAC:[/green] {result}")
            else:
                console.print("[yellow]No OFAC file found to import[/yellow]")

        if source.lower() in ("all", "opensanctions"):
            os_file = _find_latest(data_dir, "opensanctions_vessels_")
            if os_file:
                result = load_opensanctions(db, str(os_file))
                console.print(f"[green]OpenSanctions:[/green] {result}")
            else:
                console.print("[yellow]No OpenSanctions file found to import[/yellow]")

        # 3. Detection pipeline
        if detect:
            from app.models.ais_point import AISPoint
            ais_count = db.query(AISPoint).count()
            if ais_count == 0:
                console.print("\n[yellow]No AIS data in database — skipping detection pipeline.[/yellow]")
                console.print("[dim]Import AIS data first: radiancefleet ingest ais <file>[/dim]")
            else:
                console.print("\n[cyan]Running detection pipeline...[/cyan]")
                # Seed ports if needed
                from app.models.port import Port
                if db.query(Port).count() == 0:
                    try:
                        from scripts.seed_ports import seed_ports
                        seed_ports(db)
                    except Exception:
                        pass

                from app.modules.gap_detector import run_gap_detection, run_spoofing_detection
                from app.modules.loitering_detector import run_loitering_detection
                from app.modules.sts_detector import detect_sts_events
                from app.modules.corridor_correlator import correlate_all_uncorrelated_gaps
                from app.modules.risk_scoring import score_all_alerts

                gaps = run_gap_detection(db)
                console.print(f"  Gaps: {gaps['gaps_detected']}")
                run_spoofing_detection(db)
                run_loitering_detection(db)
                detect_sts_events(db)
                correlate_all_uncorrelated_gaps(db)
                scored = score_all_alerts(db)
                console.print(f"  Scored: {scored['scored']} alerts")
                console.print("[green]Detection pipeline complete.[/green]")
    finally:
        db.close()


@data_app.command("status")
def data_status():
    """Show data freshness and record counts at a glance."""
    from app.database import SessionLocal
    from sqlalchemy import func

    db = SessionLocal()
    try:
        table = Table(title="Data Status")
        table.add_column("Source", style="cyan")
        table.add_column("Last Import", style="white")
        table.add_column("Records", style="green", justify="right")

        # AIS Positions
        from app.models.ais_point import AISPoint
        ais_count = db.query(AISPoint).count()
        ais_latest = db.query(func.max(AISPoint.created_at)).scalar()
        table.add_row(
            "AIS Positions",
            str(ais_latest)[:19] if ais_latest else "never",
            f"{ais_count:,}",
        )

        # OFAC / OpenSanctions / KSE watchlists
        from app.models.vessel_watchlist import VesselWatchlist
        for source_label, source_key in [
            ("OFAC SDN", "OFAC_SDN"),
            ("OpenSanctions", "OPENSANCTIONS"),
            ("KSE Shadow Fleet", "KSE_INSTITUTE"),
        ]:
            count = db.query(VesselWatchlist).filter(
                VesselWatchlist.watchlist_source == source_key,
                VesselWatchlist.is_active == True,
            ).count()
            latest = db.query(func.max(VesselWatchlist.matched_at)).filter(
                VesselWatchlist.watchlist_source == source_key,
            ).scalar()
            table.add_row(
                source_label,
                str(latest)[:19] if latest else "never",
                str(count),
            )

        # GFW Detections
        try:
            from app.models.stubs import DarkVesselDetection
            gfw_count = db.query(DarkVesselDetection).count()
            gfw_latest = db.query(func.max(DarkVesselDetection.detection_time_utc)).scalar()
            table.add_row(
                "GFW Detections",
                str(gfw_latest)[:19] if gfw_latest else "never",
                str(gfw_count),
            )
        except Exception:
            table.add_row("GFW Detections", "never", "0")

        # Corridors
        from app.models.corridor import Corridor
        corr_count = db.query(Corridor).count()
        table.add_row(
            "Corridors",
            "config" if corr_count > 0 else "never",
            str(corr_count),
        )

        # Ports
        from app.models.port import Port
        port_count = db.query(Port).count()
        table.add_row(
            "Ports",
            "seeded" if port_count > 0 else "never",
            str(port_count),
        )

        # Alerts (scored gaps)
        from app.models.gap_event import AISGapEvent
        alert_count = db.query(AISGapEvent).count()
        scored_count = db.query(AISGapEvent).filter(AISGapEvent.risk_score.isnot(None)).count()
        table.add_row(
            "Alerts (gaps)",
            f"{scored_count} scored" if alert_count > 0 else "never",
            str(alert_count),
        )

        console.print(table)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Live data streaming and API commands
# ---------------------------------------------------------------------------


@data_app.command("stream")
def data_stream(
    duration: str = typer.Option("5m", "--duration", help="Stream duration (e.g. 30s, 5m, 1h). 0 = unlimited."),
    bbox: str = typer.Option(None, "--bbox", help="Bounding box: 'lat_min,lon_min,lat_max,lon_max'. Default: corridor bboxes."),
    api_key: str = typer.Option(None, "--api-key", help="aisstream.io API key (default: AISSTREAM_API_KEY env)"),
):
    """Stream real-time AIS data from aisstream.io.

    Connects to the aisstream.io WebSocket, filters by corridor bounding boxes,
    and ingests PositionReport + ShipStaticData into the database.
    """
    import asyncio
    from app.config import settings as _settings
    from app.database import SessionLocal

    key = api_key or _settings.AISSTREAM_API_KEY
    if not key:
        console.print(
            "[red]AISSTREAM_API_KEY not set.[/red]\n"
            "Get a free API key at: https://aisstream.io/\n"
            "Then set AISSTREAM_API_KEY in your .env file."
        )
        raise typer.Exit(1)

    # Parse duration
    duration_s = _parse_duration(duration)

    # Parse bounding boxes
    if bbox:
        parts = [float(x.strip()) for x in bbox.split(",")]
        if len(parts) != 4:
            console.print("[red]bbox must be 4 values: lat_min,lon_min,lat_max,lon_max[/red]")
            raise typer.Exit(1)
        boxes = [[[parts[0], parts[1]], [parts[2], parts[3]]]]
    else:
        db = SessionLocal()
        try:
            from app.modules.aisstream_client import get_corridor_bounding_boxes
            boxes = get_corridor_bounding_boxes(db)
        finally:
            db.close()

    console.print(f"[cyan]Streaming AIS data from aisstream.io[/cyan]")
    console.print(f"  Duration: {duration} ({duration_s}s)")
    console.print(f"  Bounding boxes: {len(boxes)}")

    from rich.live import Live
    from rich.text import Text

    status_text = Text("Connecting...")
    live = Live(status_text, console=console, refresh_per_second=2)

    def _progress(stats: dict):
        status_text.plain = (
            f"  {stats['elapsed_s']}s elapsed | "
            f"{stats['messages']} msgs ({stats['msg_per_s']}/s) | "
            f"{stats['points_stored']} points stored | "
            f"{stats['vessels_seen']} vessels"
        )

    from app.modules.aisstream_client import stream_ais
    with live:
        result = asyncio.run(stream_ais(
            api_key=key,
            bounding_boxes=boxes,
            duration_seconds=duration_s,
            batch_interval=_settings.AISSTREAM_BATCH_INTERVAL,
            progress_callback=_progress,
        ))

    console.print(f"\n[green]Stream complete:[/green]")
    console.print(f"  Messages: {result['messages_received']}")
    console.print(f"  Points stored: {result['points_stored']}")
    console.print(f"  Vessels seen: {result['vessels_seen']}")
    console.print(f"  Duration: {result.get('actual_duration_s', '?')}s")
    if result.get("error"):
        console.print(f"  [yellow]Error: {result['error']}[/yellow]")


def _parse_duration(s: str) -> int:
    """Parse duration string (30s, 5m, 1h) to seconds."""
    s = s.strip().lower()
    if s == "0":
        return 0
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    try:
        return int(s)
    except ValueError:
        return 300  # default 5 min


@data_app.command("gfw-events")
def data_gfw_events(
    vessel: str = typer.Option(..., "--vessel", help="Vessel MMSI or name to search GFW"),
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Fetch vessel events from Global Fishing Watch API.

    Searches GFW for the vessel, then retrieves encounters, loitering, and port visits.
    """
    from app.config import settings as _settings

    token = _settings.GFW_API_TOKEN
    if not token:
        console.print(
            "[red]GFW_API_TOKEN not set.[/red]\n"
            "Get a free token at: https://globalfishingwatch.org/our-apis/\n"
            "Then set GFW_API_TOKEN in your .env file."
        )
        raise typer.Exit(1)

    from app.modules.gfw_client import search_vessel, get_vessel_events

    # Search for vessel
    console.print(f"[cyan]Searching GFW for '{vessel}'...[/cyan]")
    results = search_vessel(vessel, token)
    if not results:
        console.print(f"[yellow]No vessel found for '{vessel}'[/yellow]")
        raise typer.Exit(1)

    # Use first match
    v = results[0]
    console.print(f"[green]Found:[/green] {v['name']} (MMSI: {v['mmsi']}, IMO: {v['imo']}, Flag: {v['flag']})")

    if not v.get("gfw_id"):
        console.print("[yellow]No GFW vessel ID available[/yellow]")
        raise typer.Exit(1)

    # Fetch events
    console.print(f"[cyan]Fetching events...[/cyan]")
    events = get_vessel_events(v["gfw_id"], token, start_date=from_date, end_date=to_date)

    if not events:
        console.print("[dim]No events found[/dim]")
        return

    table = Table(title=f"Events for {v['name']}")
    table.add_column("Type", style="cyan")
    table.add_column("Start", style="white")
    table.add_column("End", style="white")
    table.add_column("Lat", style="green", justify="right")
    table.add_column("Lon", style="green", justify="right")

    for ev in events[:50]:
        table.add_row(
            ev["type"],
            str(ev.get("start", ""))[:19],
            str(ev.get("end", ""))[:19],
            f"{ev.get('lat', 0):.4f}" if ev.get("lat") else "",
            f"{ev.get('lon', 0):.4f}" if ev.get("lon") else "",
        )

    console.print(table)
    console.print(f"\n[dim]Total events: {len(events)}[/dim]")


@data_app.command("gfw-detections")
def data_gfw_detections(
    bbox: str = typer.Option(None, "--bbox", help="Bounding box: 'lat_min,lon_min,lat_max,lon_max'"),
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
    import_db: bool = typer.Option(True, "--import/--no-import", help="Import detections into DB"),
):
    """Fetch SAR vessel detections from Global Fishing Watch.

    Queries GFW for dark vessel candidates in the specified area and time range.
    """
    from app.config import settings as _settings

    token = _settings.GFW_API_TOKEN
    if not token:
        console.print(
            "[red]GFW_API_TOKEN not set.[/red]\n"
            "Get a free token at: https://globalfishingwatch.org/our-apis/\n"
            "Then set GFW_API_TOKEN in your .env file."
        )
        raise typer.Exit(1)

    from app.modules.gfw_client import get_sar_detections, import_sar_detections_to_db

    if bbox:
        parts = [float(x.strip()) for x in bbox.split(",")]
        if len(parts) != 4:
            console.print("[red]bbox must be 4 values: lat_min,lon_min,lat_max,lon_max[/red]")
            raise typer.Exit(1)
        bbox_tuple = tuple(parts)
    else:
        # Default: use corridor bounding boxes
        from app.database import SessionLocal
        from app.modules.aisstream_client import get_corridor_bounding_boxes

        db = SessionLocal()
        try:
            boxes = get_corridor_bounding_boxes(db)
        finally:
            db.close()
        if boxes:
            # Merge all corridor boxes into one encompassing box
            all_lats = [b[0][0] for b in boxes] + [b[1][0] for b in boxes]
            all_lons = [b[0][1] for b in boxes] + [b[1][1] for b in boxes]
            bbox_tuple = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
        else:
            bbox_tuple = (54.0, 10.0, 66.0, 30.0)  # Baltic default

    console.print(f"[cyan]Fetching GFW SAR detections...[/cyan]")
    console.print(f"  Bbox: {bbox_tuple}")

    detections = get_sar_detections(bbox_tuple, token, start_date=from_date, end_date=to_date)
    console.print(f"[green]Found {len(detections)} detections[/green]")

    if import_db and detections:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = import_sar_detections_to_db(detections, db)
            console.print(
                f"  Imported: {result['matched']} matched, {result['dark']} dark, "
                f"{result['rejected']} rejected"
            )
        finally:
            db.close()


@data_app.command("copernicus-check")
def data_copernicus_check(
    alert_id: int = typer.Option(..., "--alert", help="Alert (gap event) ID to check"),
):
    """Check Sentinel-1 scene availability for an alert via Copernicus CDSE.

    Queries the Copernicus catalog for SAR scenes covering the gap event's
    bounding box and time window.
    """
    from app.config import settings as _settings

    if not _settings.COPERNICUS_CLIENT_ID or not _settings.COPERNICUS_CLIENT_SECRET:
        console.print(
            "[red]COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET not set.[/red]\n"
            "Register at: https://dataspace.copernicus.eu/\n"
            "Then set both values in your .env file."
        )
        raise typer.Exit(1)

    from app.database import SessionLocal
    from app.modules.copernicus_client import enhance_satellite_check

    db = SessionLocal()
    try:
        result = enhance_satellite_check(alert_id, db)
    finally:
        db.close()

    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Sentinel-1 scenes found: {result['scenes_found']}[/green]")
    for scene in result.get("scenes", [])[:10]:
        console.print(f"  {scene['name']}")
        console.print(f"    Acquired: {scene['acquisition_time']}")
        console.print(f"    Size: {scene.get('size_mb', '?')} MB")


@data_app.command("aishub-fetch")
def data_aishub_fetch(
    bbox: str = typer.Option(None, "--bbox", help="Bounding box: 'lat_min,lon_min,lat_max,lon_max'"),
    import_db: bool = typer.Option(True, "--import/--no-import", help="Import positions into DB"),
):
    """Fetch latest AIS positions from AISHub.

    Retrieves current vessel positions for the specified area.
    Rate limit: 1 request per minute.
    """
    from app.config import settings as _settings

    if not _settings.AISHUB_USERNAME:
        console.print(
            "[red]AISHUB_USERNAME not set.[/red]\n"
            "Join at: https://www.aishub.net/\n"
            "Then set AISHUB_USERNAME in your .env file."
        )
        raise typer.Exit(1)

    from app.modules.aishub_client import fetch_area_positions, ingest_aishub_positions

    if bbox:
        parts = [float(x.strip()) for x in bbox.split(",")]
        if len(parts) != 4:
            console.print("[red]bbox must be 4 values: lat_min,lon_min,lat_max,lon_max[/red]")
            raise typer.Exit(1)
        bbox_tuple = tuple(parts)
    else:
        # Default: use corridor bounding boxes
        from app.database import SessionLocal
        from app.modules.aisstream_client import get_corridor_bounding_boxes

        db = SessionLocal()
        try:
            boxes = get_corridor_bounding_boxes(db)
        finally:
            db.close()
        if boxes:
            all_lats = [b[0][0] for b in boxes] + [b[1][0] for b in boxes]
            all_lons = [b[0][1] for b in boxes] + [b[1][1] for b in boxes]
            bbox_tuple = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
        else:
            bbox_tuple = (54.0, 10.0, 66.0, 30.0)  # Baltic default

    console.print(f"[cyan]Fetching AISHub positions...[/cyan]")
    positions = fetch_area_positions(bbox_tuple)
    console.print(f"[green]Fetched {len(positions)} positions[/green]")

    if import_db and positions:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = ingest_aishub_positions(positions, db)
            console.print(
                f"  Stored: {result['stored']}, Skipped: {result['skipped']}, "
                f"New vessels: {result['vessels_created']}"
            )
        finally:
            db.close()


@data_app.command("enrich-vessels")
def data_enrich_vessels(
    limit: int = typer.Option(50, "--limit", help="Max vessels to enrich"),
):
    """Enrich vessel metadata (DWT, year_built, IMO) via GFW vessel search.

    Queries GFW for vessels that are missing critical scoring fields.
    Requires GFW_API_TOKEN.
    """
    from app.config import settings as _settings

    if not _settings.GFW_API_TOKEN:
        console.print(
            "[red]GFW_API_TOKEN not set.[/red]\n"
            "Get a free token at: https://globalfishingwatch.org/our-apis/\n"
            "Then set GFW_API_TOKEN in your .env file."
        )
        raise typer.Exit(1)

    from app.database import SessionLocal
    from app.modules.vessel_enrichment import enrich_vessels_from_gfw

    db = SessionLocal()
    try:
        console.print(f"[cyan]Enriching up to {limit} vessels via GFW...[/cyan]")
        result = enrich_vessels_from_gfw(db, limit=limit)
        console.print(
            f"[green]Done.[/green] Enriched: {result['enriched']}, "
            f"Failed: {result['failed']}, Skipped: {result['skipped']}"
        )
    finally:
        db.close()


@data_app.command("psc")
def data_psc():
    """Fetch PSC detention records (FTM + EMSA) and import into database."""
    from app.modules.data_fetcher import fetch_psc_ftm, fetch_emsa_bans
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        psc_result = fetch_psc_ftm()
        if psc_result.get("files"):
            from app.modules.psc_loader import load_psc_ftm
            for source_key, psc_path in psc_result["files"].items():
                if psc_path:
                    result = load_psc_ftm(db, str(psc_path), source=source_key)
                    console.print(f"PSC {source_key}: {result}")
                else:
                    console.print(f"[dim]PSC {source_key}: no file available[/dim]")
        if psc_result.get("errors"):
            for err in psc_result["errors"]:
                console.print(f"[yellow]PSC warning: {err}[/yellow]")

        emsa_result = fetch_emsa_bans()
        if emsa_result.get("path"):
            from app.modules.psc_loader import load_emsa_bans
            result = load_emsa_bans(db, str(emsa_result["path"]))
            console.print(f"EMSA bans: {result}")
        elif emsa_result.get("error"):
            console.print(f"[yellow]EMSA: {emsa_result['error']}[/yellow]")
        else:
            console.print("[dim]EMSA bans: up to date[/dim]")
    finally:
        db.close()


# ── GFW Gap Events + SAR Sweep + Dark Vessel Discovery ─────────────────────

@data_app.command("fetch-noaa")
def data_fetch_noaa(
    from_date: str = typer.Option(..., "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option(..., "--to", help="End date (YYYY-MM-DD)"),
    import_db: bool = typer.Option(True, "--import/--no-import", help="Import into DB after download"),
    corridor_filter: bool = typer.Option(True, "--corridor-filter/--no-corridor-filter", help="Filter by corridor bboxes"),
):
    """Download and optionally import NOAA historical AIS data."""
    from app.database import SessionLocal
    from app.modules.noaa_client import fetch_and_import_noaa
    from datetime import date as _date

    start = _date.fromisoformat(from_date)
    end = _date.fromisoformat(to_date)

    console.print(f"[cyan]NOAA AIS data ({from_date} → {to_date})...[/cyan]")
    db = SessionLocal()
    try:
        result = fetch_and_import_noaa(
            db, start_date=start, end_date=end,
            corridor_filter=corridor_filter, import_data=import_db,
        )
        console.print(
            f"[green]Done.[/green] Dates: {result['dates_downloaded']}/{result['dates_attempted']} downloaded, "
            f"Rows: {result['total_rows']}, Accepted: {result['total_accepted']}"
        )
        if result["dates_failed"]:
            for fail in result["dates_failed"]:
                console.print(f"  [yellow]Failed: {fail['date']} — {fail['error']}[/yellow]")
    finally:
        db.close()


@data_app.command("gfw-gaps")
def data_gfw_gaps(
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD). Default: 90 days ago."),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD). Default: today."),
    limit: int = typer.Option(None, "--limit", help="Max vessels to query (default: all)"),
    resume_from: int = typer.Option(None, "--resume-from-vessel-id", help="Resume from vessel_id (checkpoint)"),
):
    """Import GFW intentional AIS-disabling gap events for vessels in the DB."""
    from app.database import SessionLocal
    from app.modules.gfw_client import import_gfw_gap_events
    from datetime import date as _date, timedelta as _td

    start = from_date or (_date.today() - _td(days=90)).isoformat()
    end = to_date or _date.today().isoformat()

    console.print(f"[cyan]Importing GFW gap events ({start} → {end})...[/cyan]")
    db = SessionLocal()
    try:
        result = import_gfw_gap_events(
            db, start_date=start, end_date=end,
            limit=limit, resume_from_vessel_id=resume_from,
        )
        console.print(
            f"[green]Done.[/green] Vessels queried: {result['vessels_queried']}, "
            f"Events imported: {result['imported']}, "
            f"Skipped (dup): {result['skipped_dup']}, "
            f"In corridors: {result['in_corridor']}"
        )
        if result.get("partial"):
            console.print(
                f"[yellow]Partial results — resume with: --resume-from-vessel-id {result['last_vessel_id']}[/yellow]"
            )
    finally:
        db.close()


@data_app.command("sar-sweep")
def data_sar_sweep(
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD). Default: 30 days ago."),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD). Default: today."),
    corridors: str = typer.Option("all", "--corridors", help="Corridor filter: all, export_route, sts_zone, dark_zone"),
):
    """Sweep all corridors for SAR vessel detections (dark vessel candidates)."""
    from app.database import SessionLocal
    from app.modules.gfw_client import sweep_corridors_sar
    from datetime import date as _date, timedelta as _td

    start = from_date or (_date.today() - _td(days=30)).isoformat()
    end = to_date or _date.today().isoformat()

    corridor_types = None
    if corridors != "all":
        corridor_types = [corridors]

    console.print(f"[cyan]SAR corridor sweep ({start} → {end}, corridors={corridors})...[/cyan]")
    db = SessionLocal()
    try:
        result = sweep_corridors_sar(db, start_date=start, end_date=end, corridor_types=corridor_types)
        console.print(
            f"[green]Done.[/green] Corridors queried: {result['corridors_queried']}, "
            f"Detections: {result['total_detections']}, "
            f"Dark vessels: {result['dark_vessels']}, "
            f"Matched: {result['matched']}"
        )
        if result.get("partial"):
            console.print("[yellow]Partial results — some corridors failed[/yellow]")
    finally:
        db.close()


@data_app.command("gfw-encounters")
def gfw_encounters_cmd(
    limit: int = typer.Option(100, help="Max vessels to query"),
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Import GFW encounter events as STS transfer records."""
    from app.database import SessionLocal
    from app.modules.gfw_client import import_gfw_encounters

    db = SessionLocal()
    try:
        result = import_gfw_encounters(db, date_from=from_date, date_to=to_date, limit=limit)
        console.print(f"GFW encounters imported: {result['created']} created, {result['errors']} errors")
    finally:
        db.close()


@data_app.command("gfw-port-visits")
def gfw_port_visits_cmd(
    limit: int = typer.Option(100, help="Max vessels to query"),
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Import GFW port visit events as PortCall records."""
    from app.database import SessionLocal
    from app.modules.gfw_client import import_gfw_port_visits

    db = SessionLocal()
    try:
        result = import_gfw_port_visits(db, date_from=from_date, date_to=to_date, limit=limit)
        console.print(f"GFW port visits imported: {result['created']} created, {result['errors']} errors")
    finally:
        db.close()


@data_app.command("kystverket-stream")
def kystverket_stream_cmd(
    duration: int = typer.Option(300, help="Stream duration in seconds"),
):
    """Stream AIS data from Kystverket (Norway) TCP feed."""
    from app.database import SessionLocal
    from app.modules.kystverket_client import stream_kystverket

    db = SessionLocal()
    try:
        result = stream_kystverket(db, duration_seconds=duration)
        console.print(
            f"Kystverket: {result['points_ingested']} points, "
            f"{result['vessels_seen']} vessels, {result['errors']} errors"
        )
    finally:
        db.close()


@data_app.command("digitraffic-fetch")
def digitraffic_fetch_cmd():
    """Fetch latest AIS positions from Digitraffic (Finland)."""
    from app.database import SessionLocal
    from app.modules.digitraffic_client import fetch_digitraffic_ais

    db = SessionLocal()
    try:
        result = fetch_digitraffic_ais(db)
        console.print(
            f"Digitraffic: {result['points_ingested']} points, "
            f"{result['vessels_seen']} vessels"
        )
    finally:
        db.close()


@data_app.command("digitraffic-port-calls")
def digitraffic_port_calls_cmd():
    """Fetch port call data from Digitraffic (Finland)."""
    from app.database import SessionLocal
    from app.modules.digitraffic_client import fetch_digitraffic_port_calls

    db = SessionLocal()
    try:
        result = fetch_digitraffic_port_calls(db)
        console.print(f"Digitraffic port calls: {result['port_calls_created']} created")
    finally:
        db.close()


@data_app.command("fleetleaks-import")
def fleetleaks_import_cmd(
    path: str = typer.Argument(..., help="Path to FleetLeaks JSON"),
):
    """Import FleetLeaks vessel database into watchlist."""
    from app.database import SessionLocal
    from app.modules.watchlist_loader import load_fleetleaks

    db = SessionLocal()
    try:
        result = load_fleetleaks(db, path)
        console.print(
            f"FleetLeaks: {result['matched']} matched, {result['unmatched']} unmatched"
        )
    finally:
        db.close()


@data_app.command("gur-import")
def gur_import_cmd(
    path: str = typer.Argument(..., help="Path to GUR CSV"),
):
    """Import Ukraine GUR shadow fleet database into watchlist."""
    from app.database import SessionLocal
    from app.modules.watchlist_loader import load_gur_list

    db = SessionLocal()
    try:
        result = load_gur_list(db, path)
        console.print(
            f"GUR: {result['matched']} matched, {result['unmatched']} unmatched"
        )
    finally:
        db.close()


@data_app.command("crea-fetch")
def crea_fetch_cmd(
    limit: int = typer.Option(100, help="Max vessels to query"),
):
    """Fetch CREA Russia Fossil Tracker data for known vessels."""
    from app.database import SessionLocal
    from app.modules.crea_client import import_crea_data

    db = SessionLocal()
    try:
        result = import_crea_data(db, limit=limit)
        console.print(
            f"CREA: {result['queried']} queried, {result['enriched']} enriched"
        )
    finally:
        db.close()


@app.command("discover-dark-vessels")
def discover_dark_vessels_cmd(
    from_date: str = typer.Option(None, "--from", help="Start date (YYYY-MM-DD). Default: 90 days ago."),
    to_date: str = typer.Option(None, "--to", help="End date (YYYY-MM-DD). Default: today."),
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Skip GFW gap import + SAR sweep (use existing data)"),
    min_score: int = typer.Option(50, "--min-score", help="Min gap risk score for auto-hunt"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed step output"),
):
    """Run the full dark vessel discovery pipeline.

    Orchestrates: GFW gaps → SAR sweep → local detection → scoring → clustering → auto-hunt.
    """
    from app.database import SessionLocal
    from app.modules.dark_vessel_discovery import discover_dark_vessels
    from datetime import date as _date, timedelta as _td

    start = from_date or (_date.today() - _td(days=90)).isoformat()
    end = to_date or _date.today().isoformat()

    console.print(f"[bold cyan]Dark Vessel Discovery Pipeline[/bold cyan] ({start} → {end})\n")
    db = SessionLocal()
    try:
        result = discover_dark_vessels(
            db, start_date=start, end_date=end,
            skip_fetch=skip_fetch, min_gap_score=min_score,
        )

        # Summary report
        status = result.get("run_status", "unknown")
        status_color = "green" if status == "complete" else "yellow" if status == "partial" else "red"
        console.print(f"\n[bold]═══ Dark Vessel Discovery Report ═══[/bold]")
        console.print(f"Status: [{status_color}]{status}[/{status_color}]")
        console.print(f"Date range: {start} → {end}")

        steps = result.get("steps", {})
        for step_name, step_data in steps.items():
            if isinstance(step_data, dict):
                step_status = step_data.get("status", "?")
                s_color = "green" if step_status == "ok" else "yellow" if step_status == "skipped" else "red"
                detail = step_data.get("detail", "")
                console.print(f"  [{s_color}]{step_name}[/{s_color}]: {detail}")

        # Top alerts
        top_alerts = result.get("top_alerts", [])
        if top_alerts:
            console.print("\n[bold]Top alerts:[/bold]")
            for a in top_alerts[:5]:
                console.print(f"  #{a['gap_event_id']} MMSI {a['mmsi']} score={a['risk_score']}")
    finally:
        db.close()


# ── Vessel Identity Merge Commands ──────────────────────────────────────────

@app.command("detect-merges")
def detect_merges(
    max_gap_days: int = typer.Option(30, help="Max gap days between disappearance and reappearance"),
):
    """Detect potential same-vessel pairs across MMSI changes using speed-feasibility matching."""
    from app.database import SessionLocal
    from app.modules.identity_resolver import detect_merge_candidates

    db = SessionLocal()
    try:
        result = detect_merge_candidates(db, max_gap_days=max_gap_days)
        console.print(f"[green]Merge detection complete.[/green]")
        console.print(f"  Candidates created: {result['candidates_created']}")
        console.print(f"  Auto-merged:        {result['auto_merged']}")
        console.print(f"  Below threshold:    {result['skipped']}")
    finally:
        db.close()


@app.command("detect-cloning")
def detect_cloning():
    """Detect MMSI cloning — same MMSI at impossible distances within 1 hour."""
    from app.database import SessionLocal
    from app.modules.mmsi_cloning_detector import detect_mmsi_cloning

    db = SessionLocal()
    try:
        results = detect_mmsi_cloning(db)
        console.print(f"[green]MMSI cloning detection complete.[/green] Found {len(results)} cloning events.")
        if results:
            table = Table(title="MMSI Cloning Events")
            table.add_column("MMSI")
            table.add_column("Vessel ID")
            table.add_column("Distance (nm)")
            table.add_column("Implied Speed (kn)")
            for r in results[:20]:
                table.add_row(
                    r["mmsi"], str(r["vessel_id"]),
                    str(r["distance_nm"]), str(r["implied_speed_kn"]),
                )
            console.print(table)
    finally:
        db.close()


@app.command("merge-vessels")
def merge_vessels(
    vessel_a_id: int = typer.Argument(..., help="First vessel ID"),
    vessel_b_id: int = typer.Argument(..., help="Second vessel ID"),
    reason: str = typer.Option("", help="Reason for merge"),
):
    """Manually merge two vessels into one canonical identity."""
    from app.database import SessionLocal
    from app.modules.identity_resolver import execute_merge

    db = SessionLocal()
    try:
        result = execute_merge(db, vessel_a_id, vessel_b_id, reason=reason, merged_by="analyst_cli")
        if result.get("success"):
            console.print(f"[green]Merged vessel {vessel_b_id} into {vessel_a_id}.[/green]")
            console.print(f"  Merge operation ID: {result['merge_op_id']}")
        else:
            console.print(f"[red]Merge failed: {result.get('error')}[/red]")
    finally:
        db.close()


@app.command("reverse-merge")
def reverse_merge_cmd(
    merge_op_id: int = typer.Argument(..., help="Merge operation ID to reverse"),
):
    """Undo a vessel merge operation."""
    from app.database import SessionLocal
    from app.modules.identity_resolver import reverse_merge

    db = SessionLocal()
    try:
        result = reverse_merge(db, merge_op_id)
        if result.get("success"):
            console.print(f"[green]Merge operation {merge_op_id} reversed.[/green]")
        else:
            console.print(f"[red]Reversal failed: {result.get('error')}[/red]")
    finally:
        db.close()


@app.command("list-merge-candidates")
def list_merge_candidates_cmd(
    status: str = typer.Option("pending", help="Filter by status: pending, auto_merged, analyst_merged, rejected"),
    limit: int = typer.Option(20, help="Max candidates to show"),
):
    """List merge candidates, optionally filtered by status."""
    from app.database import SessionLocal
    from app.models.merge_candidate import MergeCandidate
    from app.models.vessel import Vessel

    db = SessionLocal()
    try:
        q = db.query(MergeCandidate).order_by(MergeCandidate.confidence_score.desc())
        if status:
            q = q.filter(MergeCandidate.status == status)
        candidates = q.limit(limit).all()

        if not candidates:
            console.print(f"[dim]No merge candidates with status '{status}'.[/dim]")
            return

        table = Table(title=f"Merge Candidates ({status})")
        table.add_column("ID", style="cyan")
        table.add_column("Vessel A")
        table.add_column("Vessel B")
        table.add_column("Distance (nm)")
        table.add_column("Gap (h)")
        table.add_column("Confidence")
        table.add_column("Status")

        for c in candidates:
            va = db.query(Vessel).get(c.vessel_a_id)
            vb = db.query(Vessel).get(c.vessel_b_id)
            table.add_row(
                str(c.candidate_id),
                f"{va.mmsi if va else '?'} ({va.name or '?' if va else '?'})",
                f"{vb.mmsi if vb else '?'} ({vb.name or '?' if vb else '?'})",
                f"{c.distance_nm:.1f}" if c.distance_nm else "?",
                f"{c.time_delta_hours:.1f}" if c.time_delta_hours else "?",
                str(c.confidence_score),
                c.status,
            )
        console.print(table)
    finally:
        db.close()


@app.command("detect-cross-receiver")
def detect_cross_receiver_cmd(
    date_from: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Detect cross-receiver AIS position disagreements."""
    from app.database import SessionLocal
    from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

    console.print("[cyan]Running cross-receiver detection...[/cyan]")
    db = SessionLocal()
    try:
        result = detect_cross_receiver_anomalies(
            db, date_from=_parse_date(date_from), date_to=_parse_date(date_to)
        )
        console.print(
            f"[green]Cross-receiver:[/green] {result['anomalies_created']} anomalies "
            f"from {result['mmsis_checked']} MMSIs"
        )
    finally:
        db.close()


@app.command("detect-handshakes")
def detect_handshakes_cmd(
    date_from: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Detect AIS identity swaps (handshakes) between vessel pairs."""
    from app.database import SessionLocal
    from app.modules.handshake_detector import detect_handshakes

    console.print("[cyan]Running handshake detection...[/cyan]")
    db = SessionLocal()
    try:
        result = detect_handshakes(
            db, date_from=_parse_date(date_from), date_to=_parse_date(date_to)
        )
        console.print(
            f"[green]Handshakes:[/green] {result['handshakes_detected']} detected "
            f"from {result['pairs_checked']} pairs"
        )
    finally:
        db.close()


@app.command("detect-fake-positions")
def detect_fake_positions_cmd(
    date_from: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD)"),
):
    """Detect kinematically impossible position sequences (fake port calls)."""
    from app.database import SessionLocal
    from app.modules.fake_position_detector import detect_fake_positions

    console.print("[cyan]Running fake position detection...[/cyan]")
    db = SessionLocal()
    try:
        result = detect_fake_positions(
            db, date_from=_parse_date(date_from), date_to=_parse_date(date_to)
        )
        console.print(
            f"[green]Fake positions:[/green] {result['fake_positions_detected']} "
            f"from {result['vessels_checked']} vessels"
        )
    finally:
        db.close()
