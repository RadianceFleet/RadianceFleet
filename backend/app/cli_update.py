"""CLI commands: start, update, stream."""

from __future__ import annotations

import logging
from datetime import UTC, date, timedelta

import typer

logger = logging.getLogger(__name__)

import app.cli_helpers as _h
from app.cli_app import app, console


@app.command("start")
def start(
    demo: bool = typer.Option(False, "--demo", help="Load sample data (no API keys needed)"),
    stream_time: str = typer.Option(
        "15m", "--stream-time", help="AIS stream duration (e.g. 30s, 5m, 1h)"
    ),
):
    """Set up RadianceFleet for the first time."""
    if _h._is_first_run() is False:
        console.print(
            "[yellow]RadianceFleet is already set up.[/yellow]\n"
            "Run [cyan]radiancefleet update[/cyan] to refresh data instead."
        )
        raise typer.Exit(0)

    try:
        from app.database import SessionLocal, init_db

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
                _h._import_corridors(db)

            # 4. Load data
            if demo:
                with console.status("[bold]Loading sample data..."):
                    _h._load_sample_data(db)
            else:
                # Fetch watchlists
                with console.status("[bold]Downloading watchlists..."):
                    try:
                        _h._update_fetch_watchlists(db)
                    except Exception as e:
                        console.print(f"[yellow]Watchlist download had issues: {e}[/yellow]")
                        db.rollback()

                # Collect AIS from all enabled sources
                console.print("[bold]Collecting AIS data...[/bold]")
                try:
                    from app.modules.collection_scheduler import CollectionScheduler

                    scheduler = CollectionScheduler(db_factory=SessionLocal)
                    scheduler.start(duration_seconds=_h._parse_duration(stream_time))
                except Exception as e:
                    console.print(f"[yellow]AIS collection had issues: {e}[/yellow]")

                # Enrich vessel metadata
                with console.status("[bold]Enriching vessel metadata..."):
                    try:
                        _h._enrich_vessels(db)
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
            _h._print_summary(console)
            _h._print_next_steps(console, after="start")
        finally:
            db.close()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Setup failed: {e}[/red]")
        raise typer.Exit(1) from None


@app.command("update")
def update(
    stream_time: str = typer.Option(
        "15m", "--stream-time", help="AIS stream duration (e.g. 30s, 5m, 1h)"
    ),
    offline: bool = typer.Option(False, "--offline", help="Skip all network operations"),
    days: int = typer.Option(90, "--days", help="Analysis window (days back from today)"),
    check_identity: bool = typer.Option(
        False, "--check-identity", help="Show merge readiness diagnostic after detection"
    ),
):
    """Refresh data and re-run analysis (daily)."""
    import time as _time

    from app.config import settings
    from app.database import SessionLocal, init_db
    from app.models.worker_heartbeat import upsert_heartbeat

    # Ensure tables exist (cron service may start before web on Railway)
    for attempt in range(1, 6):
        try:
            init_db()
            break
        except Exception as exc:
            if attempt == 5:
                raise
            console.print(f"[yellow]DB init attempt {attempt}/5 failed ({exc}), retrying...[/yellow]")
            _time.sleep(attempt * 2)

    end = date.today()
    start_date = end - timedelta(days=days)

    db = SessionLocal()
    try:
        upsert_heartbeat(db, "cron-updater", status="running")
        db.commit()
        # Phase 1: Fetch & import watchlists
        if not offline:
            with console.status("[bold]Downloading latest data..."):
                try:
                    _h._update_fetch_watchlists(db)
                except Exception as e:
                    console.print(f"[yellow]Watchlist update had issues: {e}[/yellow]")
                    console.print("[dim]Continuing with existing data...[/dim]")
                    db.rollback()

        # Phase 2: Collect AIS from all enabled sources
        if not offline:
            console.print("[bold]Collecting AIS data...[/bold]")
            try:
                from app.modules.collection_scheduler import CollectionScheduler

                scheduler = CollectionScheduler(db_factory=SessionLocal)
                scheduler.start(duration_seconds=_h._parse_duration(stream_time))
            except Exception as e:
                console.print(f"[yellow]AIS collection had issues: {e}[/yellow]")
                console.print("[dim]Continuing with existing data...[/dim]")

        # Phase 2b: Enrich vessel metadata
        if not offline:
            with console.status("[bold]Enriching vessel metadata..."):
                try:
                    _h._enrich_vessels(db)
                except Exception as e:
                    console.print(f"[yellow]Vessel enrichment: {e}[/yellow]")

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

        # Phase 3b: Purge stale AIS observations (rolling window)
        try:
            from app.models.ais_observation import AISObservation

            deleted = AISObservation.purge_old(db)
            if deleted:
                db.commit()
                console.print(f"[dim]Purged {deleted} stale AIS observation(s)[/dim]")
        except Exception as e:
            console.print(f"[yellow]Observation purge: {e}[/yellow]")
            db.rollback()

        # Optional identity diagnostic
        if check_identity:
            try:
                from app.modules.identity_resolver import diagnose_merge_readiness

                diag = diagnose_merge_readiness(db)
                console.print("\n[bold]Merge Readiness Diagnostic[/bold]")
                for key, val in diag.items():
                    console.print(f"  {key}: {val}")
            except (ImportError, AttributeError):
                console.print("[dim]Merge diagnostic not available[/dim]")

        # Phase 4: Send pending email alert notifications
        with console.status("[bold]Sending alert notifications..."):
            try:
                from datetime import datetime

                from sqlalchemy import select as sa_select

                from app.models.alert_subscription import AlertSubscription
                from app.models.gap_event import AISGapEvent
                from app.models.vessel import Vessel
                from app.modules.email_notifier import send_alert_notification

                confirmed_subs = (
                    db.execute(sa_select(AlertSubscription).where(AlertSubscription.confirmed))
                    .scalars()
                    .all()
                )
                sent = 0
                cutoff = datetime.now(UTC) - timedelta(hours=6)
                for sub in confirmed_subs:
                    if not sub.mmsi:
                        continue
                    if sub.last_notified_at and sub.last_notified_at > cutoff.replace(tzinfo=None):
                        continue
                    # Find vessel by MMSI
                    vessel = db.execute(
                        sa_select(Vessel).where(Vessel.mmsi == sub.mmsi)
                    ).scalar_one_or_none()
                    if not vessel:
                        continue
                    # Find recent gap events for this vessel
                    recent_gap = db.execute(
                        sa_select(AISGapEvent)
                        .where(
                            AISGapEvent.vessel_id == vessel.vessel_id,
                            AISGapEvent.gap_start_utc >= cutoff.replace(tzinfo=None),
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    if recent_gap:
                        vessel_name = vessel.name or sub.mmsi
                        alert_url = f"{settings.PUBLIC_URL}/alerts/{recent_gap.gap_event_id}"
                        unsub_url = f"{settings.PUBLIC_URL}/api/v1/unsubscribe?token={sub.token}&email={sub.email}"
                        ok = send_alert_notification(
                            sub.email, vessel_name, "AIS Gap", alert_url, unsub_url
                        )
                        if ok:
                            sub.last_notified_at = datetime.now(UTC).replace(tzinfo=None)
                            sent += 1
                if sent:
                    db.commit()
                    console.print(f"[dim]Sent {sent} alert notification(s)[/dim]")
            except Exception as e:
                console.print(f"[yellow]Email notifications: {e}[/yellow]")

        upsert_heartbeat(db, "cron-updater", status="idle")
        db.commit()

        console.print("[green]Update complete![/green]")
        _h._print_summary(console)
        _h._print_next_steps(console, after="update")
    except Exception as e:
        try:
            upsert_heartbeat(db, "cron-updater", status="error", error=str(e))
            db.commit()
        except Exception:
            logger.debug("Failed to record heartbeat error", exc_info=True)
        raise
    finally:
        db.close()


@app.command("stream")
def stream(
    batch_interval: int = typer.Option(
        30, "--batch-interval", help="Seconds between batch DB writes"
    ),
):
    """Run aisstream.io WebSocket consumer continuously (dedicated worker)."""
    import asyncio
    import signal
    import time as _time

    from app.config import settings
    from app.database import SessionLocal, init_db
    from app.models.ingestion_status import update_ingestion_status
    from app.models.worker_heartbeat import upsert_heartbeat
    from app.modules.aisstream_client import get_corridor_bounding_boxes, stream_ais

    # Ensure tables exist (worker may start before web service on Railway)
    for attempt in range(1, 6):
        try:
            init_db()
            break
        except Exception as exc:
            if attempt == 5:
                raise
            console.print(f"[yellow]DB init attempt {attempt}/5 failed ({exc}), retrying...[/yellow]")
            _time.sleep(attempt * 2)

    api_key = settings.AISSTREAM_API_KEY
    if not api_key:
        console.print("[red]AISSTREAM_API_KEY is required[/red]")
        raise typer.Exit(1)

    db = SessionLocal()
    try:
        boxes = get_corridor_bounding_boxes(db)
    finally:
        db.close()

    cumulative_records = 0

    def on_batch(stats: dict):
        nonlocal cumulative_records
        cumulative_records += stats.get("points_stored", 0)
        batch_db = SessionLocal()
        try:
            update_ingestion_status(
                batch_db,
                source="aisstream-worker",
                records=stats.get("points_stored", 0),
                status="running",
            )
            upsert_heartbeat(
                batch_db,
                "ws-worker",
                status="running",
                records=cumulative_records,
                metadata=stats,
            )
            batch_db.commit()
        except Exception:
            batch_db.rollback()
        finally:
            batch_db.close()

    def _write_stopped_heartbeat():
        stop_db = SessionLocal()
        try:
            upsert_heartbeat(stop_db, "ws-worker", status="stopped")
            stop_db.commit()
        except Exception:
            stop_db.rollback()
        finally:
            stop_db.close()

    def _sigterm_handler(signum, frame):
        console.print("\n[yellow]SIGTERM received, shutting down...[/yellow]")
        _write_stopped_heartbeat()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    console.print("[bold]Continuous aisstream.io WebSocket consumer[/bold]")
    console.print(f"  Bounding boxes: {len(boxes)}, Batch interval: {batch_interval}s")

    # Write starting heartbeat
    start_db = SessionLocal()
    try:
        upsert_heartbeat(start_db, "ws-worker", status="starting")
        start_db.commit()
    except Exception:
        start_db.rollback()
    finally:
        start_db.close()

    try:
        while True:  # Outer loop: survive circuit breaker trips
            result = asyncio.run(
                stream_ais(
                    api_key=api_key,
                    bounding_boxes=boxes,
                    duration_seconds=0,
                    batch_interval=batch_interval,
                    db_factory=SessionLocal,
                    progress_callback=on_batch,
                )
            )
            if result.get("error") == "circuit breaker open":
                console.print("[yellow]Circuit breaker open, waiting 60s...[/yellow]")
                wait_db = SessionLocal()
                try:
                    upsert_heartbeat(
                        wait_db, "ws-worker",
                        status="waiting",
                        records=cumulative_records,
                        error="circuit breaker open",
                    )
                    wait_db.commit()
                except Exception:
                    wait_db.rollback()
                finally:
                    wait_db.close()
                _time.sleep(60)
                continue
            break  # Normal exit (shouldn't happen with duration=0)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user[/yellow]")
        _write_stopped_heartbeat()
    except Exception as e:
        err_db = SessionLocal()
        try:
            upsert_heartbeat(err_db, "ws-worker", status="error", error=str(e))
            err_db.commit()
        except Exception:
            err_db.rollback()
        finally:
            err_db.close()
        raise
