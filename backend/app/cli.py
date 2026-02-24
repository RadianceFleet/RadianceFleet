"""RadianceFleet CLI — Typer-based command-line interface.

Usage:
    radiancefleet ingest ais ./data/sample.csv
    radiancefleet corridors import ./config/corridors.yaml
    radiancefleet detect-gaps --from 2026-01-01 --to 2026-02-01
    radiancefleet score-alerts
    radiancefleet serve
"""
from __future__ import annotations

import typer
from pathlib import Path
from typing import Optional
from datetime import date
from rich.console import Console
from rich.table import Table

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
    from geoalchemy2.shape import from_shape
    from shapely.geometry import shape

    db = SessionLocal()
    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)

        corridors_data = data.get("corridors", [])
        upserted = 0
        for c_data in corridors_data:
            existing = db.query(Corridor).filter(Corridor.name == c_data["name"]).first()
            # Build geometry if provided as GeoJSON-like dict
            geom = None
            if c_data.get("geometry"):
                try:
                    geom = from_shape(shape(c_data["geometry"]), srid=4326)
                except Exception:
                    pass

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
    """Re-run ST_Intersects corridor correlation on all uncorrelated gaps."""
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
