"""RadianceFleet CLI — maritime anomaly detection for shadow fleet triage.

Commands:
  start              — first-time setup
  update             — daily data refresh + detection
  check-vessels      — vessel identity merge workflow
  open               — launch web dashboard
  status             — system health check
  search             — vessel lookup by MMSI/IMO/name
  rescore            — re-run scoring without re-running detectors
  evaluate-detector  — sample anomalies for holdout review
  confirm-detector   — re-enable scoring after drift review
"""
from __future__ import annotations

import sys
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from datetime import date, timedelta


app = typer.Typer(
    name="radiancefleet",
    help="Maritime anomaly detection for shadow fleet triage.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("start")
def start(
    demo: bool = typer.Option(False, "--demo", help="Load sample data (no API keys needed)"),
    stream_time: str = typer.Option("15m", "--stream-time", help="AIS stream duration (e.g. 30s, 5m, 1h)"),
):
    """Set up RadianceFleet for the first time."""
    if _is_first_run() is False:
        console.print(
            "[yellow]RadianceFleet is already set up.[/yellow]\n"
            "Run [cyan]radiancefleet update[/cyan] to refresh data instead."
        )
        raise typer.Exit(0)

    try:
        from app.database import init_db, SessionLocal

        # 1. Initialize database
        with console.status("[bold]Creating database..."):
            init_db()

        db = SessionLocal()
        try:
            # 2. Seed ports
            from app.models.port import Port
            port_count = db.query(Port).count()
            if port_count == 0:
                with console.status("[bold]Seeding ports..."):
                    from scripts.seed_ports import seed_ports
                    seed_ports(db)

            # 3. Import corridors (uses flush, not commit)
            with console.status("[bold]Importing corridors..."):
                _import_corridors(db)

            # 4. Load data
            if demo:
                with console.status("[bold]Loading sample data..."):
                    _load_sample_data(db)
            else:
                # Fetch watchlists
                with console.status("[bold]Downloading watchlists..."):
                    try:
                        _update_fetch_watchlists(db)
                    except Exception as e:
                        console.print(f"[yellow]Watchlist download had issues: {e}[/yellow]")

                # Stream AIS
                from app.config import settings
                if settings.AISSTREAM_API_KEY:
                    console.print("[bold]Collecting ship positions...[/bold]")
                    try:
                        _update_stream_ais(db, stream_time)
                    except Exception as e:
                        console.print(f"[yellow]AIS streaming had issues: {e}[/yellow]")
                else:
                    console.print(
                        "[yellow]No AISSTREAM_API_KEY — skipping live AIS collection[/yellow]"
                    )

                # Enrich vessel metadata
                with console.status("[bold]Enriching vessel metadata..."):
                    try:
                        _enrich_vessels(db)
                    except Exception as e:
                        console.print(f"[yellow]Vessel enrichment had issues: {e}[/yellow]")

            # 5. Run detection (always)
            end = date.today()
            start_date = end - timedelta(days=90)
            with console.status("[bold]Analyzing vessel behavior..."):
                from app.modules.dark_vessel_discovery import discover_dark_vessels
                discover_dark_vessels(
                    db,
                    start_date=start_date.isoformat(),
                    end_date=end.isoformat(),
                    skip_fetch=True,
                )

            db.commit()
            console.print("[green]Setup complete![/green]")
            _print_summary(console)
            _print_next_steps(console, after="start")
        finally:
            db.close()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)


@app.command("update")
def update(
    stream_time: str = typer.Option("15m", "--stream-time", help="AIS stream duration (e.g. 30s, 5m, 1h)"),
    offline: bool = typer.Option(False, "--offline", help="Skip all network operations"),
    days: int = typer.Option(90, "--days", help="Analysis window (days back from today)"),
):
    """Refresh data and re-run analysis (daily)."""
    from app.database import SessionLocal

    end = date.today()
    start_date = end - timedelta(days=days)

    db = SessionLocal()
    try:
        # Phase 1: Fetch & import watchlists
        if not offline:
            with console.status("[bold]Downloading latest data..."):
                try:
                    _update_fetch_watchlists(db)
                except Exception as e:
                    console.print(f"[yellow]Watchlist update had issues: {e}[/yellow]")
                    console.print("[dim]Continuing with existing data...[/dim]")

        # Phase 2: Stream AIS
        if not offline:
            from app.config import settings
            if settings.AISSTREAM_API_KEY:
                console.print("[bold]Collecting ship positions...[/bold]")
                try:
                    _update_stream_ais(db, stream_time)
                except Exception as e:
                    console.print(f"[yellow]AIS streaming had issues: {e}[/yellow]")
                    console.print("[dim]Continuing with existing data...[/dim]")
            else:
                console.print(
                    "[yellow]No AISSTREAM_API_KEY — skipping live AIS collection[/yellow]"
                )

        # Phase 3: Detection (always runs)
        with console.status("[bold]Analyzing vessel behavior..."):
            try:
                from app.modules.dark_vessel_discovery import discover_dark_vessels
                discover_dark_vessels(
                    db,
                    start_date=start_date.isoformat(),
                    end_date=end.isoformat(),
                    skip_fetch=True,
                )
            except Exception as e:
                console.print(f"[yellow]Detection had issues: {e}[/yellow]")

        console.print("[green]Update complete![/green]")
        _print_summary(console)
        _print_next_steps(console, after="update")
    finally:
        db.close()


@app.command("check-vessels")
def check_vessels(
    auto: bool = typer.Option(False, "--auto", help="Only show auto-merge results"),
    list_mode: bool = typer.Option(False, "--list", help="List pending candidates without interactive review"),
):
    """Review and fix vessel identity issues."""
    from app.database import SessionLocal
    from app.modules.identity_resolver import detect_merge_candidates, execute_merge
    from app.models.merge_candidate import MergeCandidate
    from app.models.base import MergeCandidateStatusEnum
    from app.models.vessel import Vessel

    db = SessionLocal()
    try:
        # Step 1: Run detection
        with console.status("[bold]Scanning for vessel identity changes..."):
            result = detect_merge_candidates(db)

        console.print(
            f"Auto-merged: {result['auto_merged']} pairs  |  "
            f"Needs review: {result['candidates_created']} pairs"
        )

        if auto:
            return

        # Load pending candidates
        candidates = (
            db.query(MergeCandidate)
            .filter(MergeCandidate.status == MergeCandidateStatusEnum.PENDING)
            .order_by(MergeCandidate.confidence_score.desc())
            .all()
        )

        if not candidates:
            console.print("[green]No vessel identity issues need review.[/green]")
            return

        # List mode or non-TTY fallback
        if list_mode or not _is_interactive():
            if not _is_interactive() and not list_mode:
                console.print(
                    "[dim]Interactive mode requires a terminal. "
                    "Use --auto or --list instead.[/dim]"
                )
            _print_candidates_table(console, db, candidates)
            return

        # Interactive review
        console.print(f"\n[bold]Reviewing {len(candidates)} candidates:[/bold]\n")
        from datetime import datetime

        for c in candidates:
            va = db.query(Vessel).get(c.vessel_a_id)
            vb = db.query(Vessel).get(c.vessel_b_id)

            console.print(f"  Vessel A: {va.mmsi if va else '?'} ({va.name or '?' if va else '?'}, {va.flag or '?' if va else '?'})")
            if c.vessel_a_last_time:
                console.print(f"    Last seen: {str(c.vessel_a_last_time)[:10]}")
            console.print(f"  Vessel B: {vb.mmsi if vb else '?'} ({vb.name or '?' if vb else '?'}, {vb.flag or '?' if vb else '?'})")
            if c.vessel_b_first_time:
                console.print(f"    First seen: {str(c.vessel_b_first_time)[:10]}")
            if c.time_delta_hours is not None:
                console.print(f"  Gap: {c.time_delta_hours:.1f} hours, {c.distance_nm:.1f}nm apart" if c.distance_nm else f"  Gap: {c.time_delta_hours:.1f} hours")
            console.print(f"  Confidence: {c.confidence_score}/100\n")

            choice = typer.prompt("  [m]erge  [s]kip  [r]eject  [q]uit", default="s")
            choice = choice.strip().lower()

            if choice == "q":
                console.print("[dim]Exiting review.[/dim]")
                break
            elif choice == "m":
                merge_result = execute_merge(
                    db, c.vessel_a_id, c.vessel_b_id,
                    candidate_id=c.candidate_id,
                    merged_by="analyst_cli",
                )
                if merge_result.get("success"):
                    c.status = MergeCandidateStatusEnum.ANALYST_MERGED
                    c.resolved_at = datetime.utcnow()
                    c.resolved_by = "analyst_cli"
                    db.commit()
                    console.print("  [green]Merged.[/green]\n")
                else:
                    console.print(
                        f"  [yellow]Could not merge: {merge_result.get('error', 'unknown')}[/yellow]\n"
                    )
            elif choice == "r":
                c.status = MergeCandidateStatusEnum.REJECTED
                c.resolved_at = datetime.utcnow()
                c.resolved_by = "analyst_cli"
                db.commit()
                console.print("  [dim]Rejected.[/dim]\n")
            else:
                console.print("  [dim]Skipped.[/dim]\n")

    finally:
        db.close()


@app.command("open")
def open_dashboard(
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    host: str = typer.Option("127.0.0.1", "--host", hidden=True),
    port: int = typer.Option(8000, "--port", hidden=True),
):
    """Launch the web dashboard."""
    import threading
    import time
    import webbrowser
    import uvicorn

    url = f"http://{host}:{port}"

    if not no_browser:
        def _open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    console.print(f"Dashboard running at [cyan]{url}[/cyan] — press Ctrl+C to stop")
    uvicorn.run("app.main:app", host=host, port=port)


@app.command("status")
def status():
    """Show system health and data freshness."""
    from app.database import SessionLocal
    from sqlalchemy import func

    db = SessionLocal()
    try:
        # System checks
        console.print("[bold]System[/bold]")

        from app.models.corridor import Corridor
        from app.models.port import Port
        corr_count = db.query(Corridor).count()
        port_count = db.query(Port).count()

        console.print(f"  Database: [green]OK[/green]")
        console.print(
            f"  Corridors: {'[green]' + str(corr_count) + ' loaded[/green]' if corr_count else '[yellow]not loaded[/yellow]'}"
        )
        console.print(
            f"  Ports: {'[green]' + str(port_count) + ' seeded[/green]' if port_count else '[yellow]not seeded[/yellow]'}"
        )

        # Data freshness
        console.print("\n[bold]Data Freshness[/bold]")

        from app.models.ais_point import AISPoint
        ais_count = db.query(AISPoint).count()
        ais_latest = db.query(func.max(AISPoint.timestamp_utc)).scalar()

        if ais_latest:
            from datetime import datetime
            age = datetime.utcnow() - ais_latest
            age_hours = age.total_seconds() / 3600
            age_str = f"{age_hours:.0f} hours ago" if age_hours < 48 else f"{age.days} days ago"
            freshness_color = "green" if age_hours < 24 else "yellow" if age_hours < 72 else "red"
            console.print(f"  AIS data: [{freshness_color}]Last import {age_str}[/{freshness_color}] ({ais_count:,} positions)")
        else:
            console.print("  AIS data: [red]No data yet[/red]")

        from app.models.vessel_watchlist import VesselWatchlist
        wl_count = db.query(VesselWatchlist).filter(VesselWatchlist.is_active == True).count()
        wl_latest = db.query(func.max(VesselWatchlist.date_listed)).scalar()
        if wl_count > 0:
            if wl_latest:
                console.print(f"  Watchlists: [green]{wl_count} active entries[/green] (latest listing: {wl_latest})")
            else:
                console.print(f"  Watchlists: [green]{wl_count} active entries[/green]")
        else:
            console.print("  Watchlists: [dim]Never imported[/dim]")

        # Results
        console.print("\n[bold]Results[/bold]")

        from app.models.vessel import Vessel
        from app.models.gap_event import AISGapEvent
        vessel_count = db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).count()
        alert_count = db.query(AISGapEvent).count()
        scored_count = db.query(AISGapEvent).filter(AISGapEvent.risk_score.isnot(None)).count()

        console.print(f"  Vessels tracked: {vessel_count:,}")
        console.print(f"  Alerts: {alert_count:,} ({scored_count:,} scored)")

        if scored_count > 0:
            critical = db.query(AISGapEvent).filter(AISGapEvent.risk_score >= 76).count()
            high = db.query(AISGapEvent).filter(
                AISGapEvent.risk_score >= 51, AISGapEvent.risk_score < 76
            ).count()
            medium = db.query(AISGapEvent).filter(
                AISGapEvent.risk_score >= 26, AISGapEvent.risk_score < 51
            ).count()
            console.print(
                f"    [red]{critical} critical[/red]  "
                f"[yellow]{high} high[/yellow]  "
                f"[dim]{medium} medium[/dim]"
            )

        # Suggestion
        if ais_latest:
            from datetime import datetime
            age = datetime.utcnow() - ais_latest
            if age.total_seconds() > 86400:
                console.print(
                    f"\n[yellow]Your data is {age.days} day{'s' if age.days != 1 else ''} old. "
                    f"Run [cyan]radiancefleet update[/cyan] to refresh.[/yellow]"
                )
        elif corr_count == 0:
            console.print(
                "\n[yellow]Not set up yet. Run [cyan]radiancefleet start[/cyan] to begin.[/yellow]"
            )

    finally:
        db.close()


@app.command("rescore")
def rescore():
    """Re-run scoring without re-running detectors."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        from app.modules.risk_scoring import score_all_alerts, rescore_all_alerts
        with console.status("[bold]Re-scoring all alerts..."):
            result = rescore_all_alerts(db)
        console.print(
            f"[green]Rescored {result.get('rescored', 0)} alerts[/green] "
            f"(config hash: {result.get('config_hash', '?')})"
        )

        # Run confidence classification after rescore
        try:
            from app.modules.confidence_classifier import classify_all_vessels
            with console.status("[bold]Classifying vessel confidence..."):
                cls_result = classify_all_vessels(db)
            by_level = cls_result.get("by_level", {})
            console.print(
                f"  Classified {cls_result.get('classified', 0)} vessels: "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_level.items()))
            )
        except ImportError:
            pass
    finally:
        db.close()


@app.command("evaluate-detector")
def evaluate_detector(
    name: str = typer.Argument(..., help="Detector name (e.g. gap_detector, spoofing_detector)"),
    sample_size: int = typer.Option(50, "--sample-size", help="Number of anomalies to sample"),
):
    """Sample anomalies from a detector for holdout review.

    Outputs CSV to stdout: vessel_id, mmsi, anomaly_type, evidence_json,
    score_contribution, created_at, verdict (empty — operator fills in).
    """
    import csv
    import io

    from app.database import SessionLocal
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.vessel import Vessel

    # Map detector name to anomaly types
    _DETECTOR_TYPE_MAP = {
        "gap_detector": ["AIS_GAP"],
        "spoofing_detector": ["ERRATIC_NAV_STATUS", "IMPOSSIBLE_POSITION", "CROSS_RECEIVER_DISAGREEMENT",
                              "IDENTITY_SWAP", "FAKE_PORT_CALL"],
        "track_naturalness": ["SYNTHETIC_TRACK"],
        "stateless_mmsi": ["STATELESS_MMSI"],
        "flag_hopping": ["FLAG_HOPPING"],
        "imo_fraud": ["IMO_FRAUD"],
        "draught": ["DRAUGHT_CHANGE"],
        "destination": ["DESTINATION_MISMATCH"],
        "sts_chain": ["STS_CHAIN"],
        "scrapped_registry": ["SCRAPPED_IMO_REUSE", "TRACK_REPLAY"],
        "fleet_analyzer": ["FLEET_PATTERN"],
        "convoy": ["CONVOY", "FLOATING_STORAGE", "ARCTIC_NO_ICE_CLASS"],
        "ownership_graph": ["SHELL_CHAIN", "CIRCULAR_OWNERSHIP", "SANCTIONS_PROPAGATION"],
    }

    anomaly_types = _DETECTOR_TYPE_MAP.get(name)
    if anomaly_types is None:
        console.print(f"[red]Unknown detector: {name}[/red]")
        console.print(f"[dim]Known detectors: {', '.join(sorted(_DETECTOR_TYPE_MAP))}[/dim]")
        raise typer.Exit(1)

    db = SessionLocal()
    try:
        query = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type.in_(anomaly_types)
        ).order_by(SpoofingAnomaly.spoofing_id.desc()).limit(sample_size)
        anomalies = query.all()

        if not anomalies:
            console.print(f"[yellow]No anomalies found for detector: {name}[/yellow]")
            raise typer.Exit(0)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "vessel_id", "mmsi", "anomaly_type", "evidence_json",
            "score_contribution", "created_at", "verdict",
        ])

        for a in anomalies:
            vessel = db.query(Vessel).filter(Vessel.vessel_id == a.vessel_id).first()
            mmsi = vessel.mmsi if vessel else "?"
            writer.writerow([
                a.vessel_id,
                mmsi,
                a.anomaly_type,
                str(a.evidence_json) if a.evidence_json else "",
                getattr(a, "risk_score_component", ""),
                str(getattr(a, "created_at", "")) if getattr(a, "created_at", None) else "",
                "",  # verdict — operator fills in
            ])

        # Print CSV to stdout (not through Rich — raw output for piping)
        print(output.getvalue(), end="")
    finally:
        db.close()


@app.command("confirm-detector")
def confirm_detector(
    name: str = typer.Argument(..., help="Detector name"),
    holdout_csv: str = typer.Option(..., "--holdout-csv", help="Path to reviewed CSV with verdicts"),
):
    """Re-enable scoring after drift holdout review.

    Parses the CSV, computes precision = TP / (TP + FP) from the 'verdict'
    column. If precision >= 70%, clears the detector from drift-disabled list.
    """
    import csv

    from app.database import SessionLocal
    from app.models.pipeline_run import PipelineRun

    csv_path = Path(holdout_csv)
    if not csv_path.exists():
        console.print(f"[red]File not found: {holdout_csv}[/red]")
        raise typer.Exit(1)

    # Parse verdicts
    tp = fp = 0
    total_rows = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            verdict = row.get("verdict", "").strip().upper()
            if verdict == "TP":
                tp += 1
                total_rows += 1
            elif verdict == "FP":
                fp += 1
                total_rows += 1
            # Skip rows without verdict

    if total_rows == 0:
        console.print("[red]No TP/FP verdicts found in CSV.[/red]")
        console.print("[dim]Mark the 'verdict' column as TP or FP for each row.[/dim]")
        raise typer.Exit(1)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    console.print(f"  TP={tp}  FP={fp}  Total={total_rows}  Precision={precision:.1%}")

    db = SessionLocal()
    try:
        if precision >= 0.70:
            # Clear detector from drift-disabled list in latest pipeline run
            latest_run = (
                db.query(PipelineRun)
                .order_by(PipelineRun.run_id.desc())
                .first()
            )
            if latest_run and latest_run.drift_disabled_detectors_json:
                disabled = list(latest_run.drift_disabled_detectors_json)
                if name in disabled:
                    disabled.remove(name)
                    latest_run.drift_disabled_detectors_json = disabled
                    db.commit()
                    console.print(f"[green]Scoring re-enabled for {name}[/green]")
                else:
                    console.print(f"[dim]{name} was not in drift-disabled list.[/dim]")
            else:
                console.print(f"[dim]No pipeline run found or no disabled detectors.[/dim]")

            console.print(
                f"[green]Precision {precision:.1%} >= 70% threshold — {name} confirmed.[/green]"
            )
        else:
            console.print(
                f"[red]Precision {precision:.1%} below 70% threshold — "
                f"scoring stays disabled for {name}.[/red]\n"
                f"[dim]Investigate detector logic before re-enabling.[/dim]"
            )
            raise typer.Exit(1)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    from app.modules.watchlist_loader import load_ofac_sdn, load_opensanctions
    from app.config import settings

    fetch_all()

    data_dir = Path(settings.DATA_DIR)
    ofac_file = _find_latest(data_dir, "ofac_sdn_")
    if ofac_file:
        load_ofac_sdn(db, str(ofac_file))

    os_file = _find_latest(data_dir, "opensanctions_vessels_")
    if os_file:
        load_opensanctions(db, str(os_file))


def _update_stream_ais(db, stream_time: str) -> None:
    """Stream AIS data from aisstream.io."""
    import asyncio
    from app.config import settings
    from app.modules.aisstream_client import stream_ais, get_corridor_bounding_boxes

    boxes = get_corridor_bounding_boxes(db)
    duration_s = _parse_duration(stream_time)
    asyncio.run(stream_ais(
        api_key=settings.AISSTREAM_API_KEY,
        bounding_boxes=boxes,
        duration_seconds=duration_s,
        batch_interval=settings.AISSTREAM_BATCH_INTERVAL,
    ))


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
        return 300
