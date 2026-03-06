"""Shared CLI helper functions."""
from __future__ import annotations

import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table


def _is_interactive() -> bool:
    """Check if stdin is a terminal (mockable for testing)."""
    return sys.stdin.isatty()


def _is_first_run() -> bool:
    """Check if RadianceFleet has been set up (corridors exist in DB)."""
    try:
        from app.database import SessionLocal
        from app.models.corridor import Corridor
        db = SessionLocal()
        try:
            return db.query(Corridor).count() == 0
        finally:
            db.close()
    except Exception:
        return True


def _import_corridors(db) -> None:
    """Import corridors from config/corridors.yaml. Uses flush (not commit)
    so corridors are visible in-session but rolled back atomically if a
    later step fails."""
    import yaml
    from app.models.corridor import Corridor

    # Lazy import console from cli to avoid circular import at module level
    from app.cli_app import console

    config_path = Path("../config/corridors.yaml")
    if not config_path.exists():
        config_path = Path("config/corridors.yaml")
    if not config_path.exists():
        console.print("[yellow]corridors.yaml not found, skipping[/yellow]")
        return

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        corridors_data = data.get("corridors", [])

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
        db.flush()
        console.print(f"  Imported {upserted} corridors")
    except Exception as e:
        db.rollback()
        console.print(f"[yellow]Corridor import failed: {e}[/yellow]")


def _load_sample_data(db) -> None:
    """Generate and ingest sample AIS data for demo mode."""
    from app.modules.ingest import ingest_ais_csv
    from app.models.ais_point import AISPoint
    from app.cli_app import console

    ais_count = db.query(AISPoint).count()
    if ais_count > 0:
        console.print(f"[dim]Skipping sample data — {ais_count} AIS points already in DB[/dim]")
        return

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
        console.print(f"  Ingested {result['accepted']} sample AIS points")
    else:
        console.print("[yellow]Sample data script not found[/yellow]")


def _enrich_vessels(db) -> None:
    """Enrich vessel metadata via GFW and infer AIS class."""
    from app.config import settings

    if settings.GFW_API_TOKEN:
        from app.modules.vessel_enrichment import enrich_vessels_from_gfw
        enrich_vessels_from_gfw(db, limit=50)

    from app.modules.vessel_enrichment import infer_ais_class_batch
    infer_ais_class_batch(db)


def _print_summary(con: Console) -> None:
    """Print a brief summary of database contents."""
    try:
        from app.database import SessionLocal
        from app.models.vessel import Vessel
        from app.models.gap_event import AISGapEvent

        db = SessionLocal()
        try:
            vessels = db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).count()
            alerts = db.query(AISGapEvent).filter(AISGapEvent.risk_score.isnot(None)).count()
            con.print(f"  Vessels: {vessels:,}  |  Scored alerts: {alerts:,}")
        finally:
            db.close()
    except Exception:
        pass


def _print_next_steps(con: Console, after: str = "start") -> None:
    """Print what the user should do next."""
    con.print("\n[bold]What to do next:[/bold]")
    if after == "start":
        con.print("  [cyan]radiancefleet open[/cyan]       — view the dashboard")
        con.print("  [cyan]radiancefleet status[/cyan]     — check system health")
        con.print("  [cyan]radiancefleet update[/cyan]     — refresh data tomorrow")
    elif after == "update":
        con.print("  [cyan]radiancefleet open[/cyan]           — view the dashboard")
        con.print("  [cyan]radiancefleet check-vessels[/cyan]  — review identity issues")
        con.print("  [cyan]radiancefleet status[/cyan]         — check system health")


def _print_candidates_table(con: Console, db, candidates) -> None:
    """Print a Rich table of merge candidates."""
    from app.models.vessel import Vessel

    table = Table(title=f"Pending Merge Candidates ({len(candidates)})")
    table.add_column("ID", style="cyan")
    table.add_column("Vessel A")
    table.add_column("Vessel B")
    table.add_column("Gap (h)")
    table.add_column("Distance (nm)")
    table.add_column("Confidence")

    for c in candidates:
        va = db.query(Vessel).get(c.vessel_a_id)
        vb = db.query(Vessel).get(c.vessel_b_id)
        table.add_row(
            str(c.candidate_id),
            f"{va.mmsi if va else '?'} ({va.name or '?' if va else '?'})",
            f"{vb.mmsi if vb else '?'} ({vb.name or '?' if vb else '?'})",
            f"{c.time_delta_hours:.1f}" if c.time_delta_hours else "?",
            f"{c.distance_nm:.1f}" if c.distance_nm else "?",
            str(c.confidence_score),
        )
    con.print(table)


def _update_fetch_watchlists(db) -> None:
    """Fetch and import watchlists."""
    from app.modules.data_fetcher import fetch_all, _find_latest
    from app.modules.watchlist_loader import load_ofac_sdn, load_opensanctions, load_fleetleaks, load_gur_list, load_kse_list
    from app.config import settings

    fetch_all()

    data_dir = Path(settings.DATA_DIR)
    ofac_file = _find_latest(data_dir, "ofac_sdn_")
    if ofac_file:
        load_ofac_sdn(db, str(ofac_file))

    os_file = _find_latest(data_dir, "opensanctions_vessels_")
    if os_file:
        load_opensanctions(db, str(os_file))

    fl_file = _find_latest(data_dir, "fleetleaks_")
    if fl_file:
        load_fleetleaks(db, str(fl_file))

    gur_file = _find_latest(data_dir, "gur_shadow_")
    if gur_file:
        load_gur_list(db, str(gur_file))

    kse_file = _find_latest(data_dir, "kse_")
    if kse_file:
        load_kse_list(db, str(kse_file))


def _parse_duration(s: str) -> int:
    """Parse duration string (30s, 5m, 1h, 7d) to seconds."""
    s = s.strip().lower()
    if s == "0":
        return 0
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    try:
        return int(s)
    except ValueError:
        return 300
