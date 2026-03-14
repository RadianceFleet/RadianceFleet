"""CLI commands: history status, gaps, backfill, schedule."""

from __future__ import annotations

import logging

import typer

logger = logging.getLogger(__name__)
from datetime import UTC, date, timedelta

from rich.table import Table

from app.cli_app import console, history_app

_HISTORY_SOURCES = [
    "noaa",
    "dma",
    "barentswatch",
    "gfw",
    "gfw-gaps",
    "gfw-encounters",
    "gfw-port-visits",
]


@history_app.command("status")
def history_status():
    """Show coverage status for each historical data source."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        table = Table(title="Historical Data Coverage")
        table.add_column("Source", style="cyan")
        table.add_column("Earliest")
        table.add_column("Latest")
        table.add_column("Windows", justify="right")
        table.add_column("Points", justify="right")
        table.add_column("Next Gap")

        try:
            from app.modules.coverage_tracker import coverage_summary

            all_summaries = coverage_summary(db)
            for source in _HISTORY_SOURCES:
                info = all_summaries.get(source, {})
                next_gap = info.get("next_gap")
                next_gap_str = str(next_gap) if next_gap else "[green]none[/green]"
                table.add_row(
                    source,
                    str(info.get("earliest", "-")),
                    str(info.get("latest", "-")),
                    str(info.get("completed_windows", 0)),
                    f"{info.get('total_points', 0):,}",
                    next_gap_str,
                )
        except (ImportError, AttributeError):
            for source in _HISTORY_SOURCES:
                table.add_row(source, "-", "-", "0", "0", "[dim]tracker unavailable[/dim]")

        console.print(table)

        # Recent collection runs (from CollectionScheduler / update)
        try:
            from datetime import datetime

            from app.models.collection_run import CollectionRun

            cutoff = datetime.now(UTC) - timedelta(days=7)
            runs = (
                db.query(CollectionRun)
                .filter(CollectionRun.started_at >= cutoff)
                .order_by(CollectionRun.started_at.desc())
                .limit(20)
                .all()
            )
            if runs:
                runs_table = Table(title="Recent Collection Runs (last 7 days)")
                runs_table.add_column("Source", style="cyan")
                runs_table.add_column("Started")
                runs_table.add_column("Status")
                runs_table.add_column("Points", justify="right")
                runs_table.add_column("Errors", justify="right")
                for run in runs:
                    status_style = {"completed": "green", "running": "yellow", "failed": "red"}.get(
                        run.status, "white"
                    )
                    runs_table.add_row(
                        run.source,
                        str(run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "?"),
                        f"[{status_style}]{run.status}[/{status_style}]",
                        str(run.points_imported or 0),
                        str(run.errors or 0),
                    )
                console.print(runs_table)
        except Exception as e:
            logger.error("Failed to fetch ingestion history: %s", e)
    finally:
        db.close()


@history_app.command("gaps")
def history_gaps(
    source: str = typer.Option("noaa", "--source", help="Data source"),
    limit: int = typer.Option(20, "--limit", help="Max gaps to show"),
):
    """List uncovered date ranges for a source."""
    if source not in _HISTORY_SOURCES:
        console.print(f"[red]Unknown source: {source}[/red]")
        console.print(f"[dim]Valid sources: {', '.join(_HISTORY_SOURCES)}[/dim]")
        raise typer.Exit(1)

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        try:
            from app.modules.coverage_tracker import find_coverage_gaps

            to_date = date.today() - timedelta(days=1)
            from_date = to_date - timedelta(days=730)
            gaps = find_coverage_gaps(db, source, from_date, to_date)
        except (ImportError, AttributeError):
            console.print("[yellow]Coverage tracker not available[/yellow]")
            return

        if not gaps:
            console.print(f"[green]No coverage gaps found for {source}[/green]")
            return

        table = Table(title=f"Coverage Gaps — {source}")
        table.add_column("Start", style="cyan")
        table.add_column("End", style="cyan")
        table.add_column("Days", justify="right")

        for gap_start, gap_end in gaps[:limit]:
            gap_days = (gap_end - gap_start).days + 1
            table.add_row(str(gap_start), str(gap_end), str(gap_days))

        console.print(table)
        if len(gaps) > limit:
            console.print(f"[dim]... and {len(gaps) - limit} more gaps[/dim]")
    finally:
        db.close()


@history_app.command("backfill")
def history_backfill(
    source: str = typer.Option(
        ..., "--source", help=f"Data source ({', '.join(_HISTORY_SOURCES)})"
    ),
    start: str = typer.Option(None, "--start", help="Start date (ISO format)"),
    end: str = typer.Option(None, "--end", help="End date (ISO format)"),
    days: int = typer.Option(0, "--days", help="Alternative to --start/--end: import last N days"),
    detect: bool = typer.Option(True, "--detect/--no-detect", help="Run detection after import"),
    corridor_filter: bool = typer.Option(
        True,
        "--corridor-filter/--no-corridor-filter",
        help="Only import within corridor bounding boxes (NOAA)",
    ),
):
    """Backfill historical data for a specific source and date range."""
    if source not in _HISTORY_SOURCES:
        console.print(f"[red]Unknown source: {source}[/red]")
        console.print(f"[dim]Valid sources: {', '.join(_HISTORY_SOURCES)}[/dim]")
        raise typer.Exit(1)

    # Resolve date range
    if days > 0:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
    elif start and end:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as e:
            console.print(f"[red]Invalid date format: {e}[/red]")
            raise typer.Exit(1) from None
    else:
        console.print("[red]Provide --start/--end or --days[/red]")
        raise typer.Exit(1)

    if start_date > end_date:
        console.print("[red]--start must be before or equal to --end[/red]")
        raise typer.Exit(1)

    # BarentsWatch: enforce 14-day max
    if source == "barentswatch" and (end_date - start_date).days > 14:
        console.print("[yellow]BarentsWatch max history is 14 days. Clamping start date.[/yellow]")
        start_date = end_date - timedelta(days=14)

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        console.print(f"[bold]Importing {source} data from {start_date} to {end_date}...[/bold]")

        if source == "noaa":
            from app.modules.noaa_client import fetch_and_import_noaa

            stats = fetch_and_import_noaa(
                db,
                start_date=start_date,
                end_date=end_date,
                corridor_filter=corridor_filter,
            )
            console.print(
                f"  Downloaded {stats['dates_downloaded']}/{stats['dates_attempted']} days, "
                f"{stats['total_accepted']:,} positions imported"
            )
            if stats["dates_failed"]:
                console.print(f"  [yellow]{len(stats['dates_failed'])} days failed[/yellow]")

        elif source == "dma":
            from app.modules.dma_client import fetch_and_import_dma

            stats = fetch_and_import_dma(db, start_date, end_date)
            console.print(
                f"  {stats['days_processed']} days processed, "
                f"{stats['points_imported']:,} points imported, "
                f"{stats['vessels_created']} vessels created"
            )

        elif source == "barentswatch":
            from app.modules.barentswatch_client import fetch_barentswatch_tracks

            stats = fetch_barentswatch_tracks(
                db,
                mmsis=[],
                start_date=start_date,
                end_date=end_date,
            )
            console.print(
                f"  {stats['points_imported']:,} points imported, "
                f"{stats['vessels_seen']} vessels seen"
            )

        elif source == "gfw":
            from app.modules.gfw_client import import_sar_detections_to_db

            stats = import_sar_detections_to_db(
                db,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            console.print(f"  GFW SAR: {stats}")

        elif source == "gfw-gaps":
            from app.modules.gfw_client import import_gfw_gap_events

            stats = import_gfw_gap_events(
                db,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            console.print(f"  GFW gaps: {stats}")

        elif source == "gfw-encounters":
            from app.modules.gfw_client import import_gfw_encounters

            stats = import_gfw_encounters(
                db,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            console.print(f"  GFW encounters: {stats}")

        elif source == "gfw-port-visits":
            from app.modules.gfw_client import import_gfw_port_visits

            stats = import_gfw_port_visits(
                db,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            console.print(f"  GFW port visits: {stats}")

        # Record coverage window
        try:
            from app.modules.coverage_tracker import record_coverage_window

            record_coverage_window(
                db,
                source,
                start_date,
                end_date,
                status="completed",
            )
            db.commit()
        except (ImportError, AttributeError, TypeError) as e:
            logger.error("Failed to record coverage window: %s", e)

        if detect:
            lookback_start = start_date - timedelta(days=90)
            with console.status("[bold]Analyzing vessel behavior..."):
                from app.modules.dark_vessel_discovery import discover_dark_vessels

                discover_dark_vessels(
                    db,
                    start_date=lookback_start.isoformat(),
                    end_date=end_date.isoformat(),
                    skip_fetch=True,
                )

        db.commit()
        console.print("[green]Backfill complete![/green]")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Backfill failed: {e}[/red]")
        raise typer.Exit(1) from None
    finally:
        db.close()


@history_app.command("schedule")
def history_schedule(
    dry_run: bool = typer.Option(
        True, "--dry-run/--execute", help="Show planned actions without executing"
    ),
):
    """Run the history scheduler once (or preview planned actions)."""
    from app.database import SessionLocal
    from app.modules.history_scheduler import HistoryScheduler

    scheduler = HistoryScheduler(db_factory=SessionLocal)
    enabled = scheduler._get_enabled_sources()

    if not enabled:
        console.print("[yellow]No backfill sources enabled.[/yellow]")
        console.print(
            "[dim]Enable sources via NOAA_BACKFILL_ENABLED, DMA_BACKFILL_ENABLED, etc.[/dim]"
        )
        return

    console.print(f"[bold]Enabled sources:[/bold] {', '.join(enabled)}")

    if dry_run:
        db = SessionLocal()
        try:
            table = Table(title="Planned Backfill Actions (dry run)")
            table.add_column("Source", style="cyan")
            table.add_column("Action")
            table.add_column("Date Range")
            table.add_column("Max Days", justify="right")

            from app.modules.history_scheduler import _SOURCE_MAX_DAYS

            for source in enabled:
                max_days = _SOURCE_MAX_DAYS.get(source, 30)
                gaps: list = []
                try:
                    from app.modules.coverage_tracker import find_coverage_gaps

                    to_date = date.today() - timedelta(days=1)
                    from_date = to_date - timedelta(days=730)
                    gaps = find_coverage_gaps(db, source, from_date, to_date)
                except (ImportError, AttributeError) as e:
                    logger.error("Failed to find coverage gaps for %s: %s", source, e)

                if gaps:
                    # Show first gap that would be filled
                    gap_start, gap_end = gaps[0]
                    clamped_end = min(
                        gap_end,
                        gap_start + timedelta(days=max_days - 1),
                    )
                    table.add_row(
                        source,
                        "Fill gap",
                        f"{gap_start} to {clamped_end}",
                        str(max_days),
                    )
                else:
                    end_d = date.today() - timedelta(days=1)
                    start_d = end_d - timedelta(days=max_days - 1)
                    table.add_row(
                        source,
                        "Fallback window",
                        f"{start_d} to {end_d}",
                        str(max_days),
                    )

            console.print(table)
        finally:
            db.close()
    else:
        db = SessionLocal()
        try:
            with console.status("[bold]Running history backfill..."):
                results = scheduler.run_now(db)
            for source, result in results.items():
                console.print(f"  {source}: {result}")
            console.print("[green]Schedule run complete.[/green]")
        finally:
            db.close()
