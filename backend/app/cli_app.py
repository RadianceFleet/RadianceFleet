"""Core CLI objects — no dependencies on cli_helpers or sub-command modules."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="radiancefleet",
    help="Maritime anomaly detection for shadow fleet triage.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

history_app = typer.Typer(
    name="history",
    help="Historical data coverage and backfill management.",
    no_args_is_help=True,
)
app.add_typer(history_app, name="history")

# ---------------------------------------------------------------------------
# Satellite imagery order management
# ---------------------------------------------------------------------------

satellite_app = typer.Typer(
    name="satellite",
    help="Satellite imagery order management.",
    no_args_is_help=True,
)


@satellite_app.command("search")
def satellite_search(
    alert_id: int = typer.Option(..., help="Alert ID to search imagery for"),
    provider: str = typer.Option("planet", help="Provider name"),
) -> None:
    """Search satellite archive for an alert."""
    from app.database import SessionLocal
    from app.modules.satellite_order_manager import search_archive_for_alert

    db = SessionLocal()
    try:
        result = search_archive_for_alert(db, alert_id, provider)
        typer.echo(f"Order {result['order_id']} created with {result['scenes_found']} scenes")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        db.close()


@satellite_app.command("submit")
def satellite_submit(
    order_id: int = typer.Option(..., help="Draft order ID"),
    scene_ids: str = typer.Option(..., help="Comma-separated scene IDs"),
) -> None:
    """Submit a draft satellite order."""
    from app.database import SessionLocal
    from app.modules.satellite_order_manager import submit_order

    db = SessionLocal()
    try:
        ids = [s.strip() for s in scene_ids.split(",")]
        result = submit_order(db, order_id, ids)
        typer.echo(f"Order {result['order_id']} submitted: {result['external_order_id']}")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        db.close()


@satellite_app.command("poll-orders")
def satellite_poll() -> None:
    """Poll status of active satellite orders (designed for cron)."""
    from app.database import SessionLocal
    from app.modules.satellite_order_manager import poll_order_status

    db = SessionLocal()
    try:
        results = poll_order_status(db)
        for r in results:
            typer.echo(f"  Order {r.get('order_id')}: {r.get('status', r.get('error', 'unknown'))}")
    finally:
        db.close()


@satellite_app.command("budget")
def satellite_budget() -> None:
    """Show current satellite imagery budget."""
    from app.database import SessionLocal
    from app.modules.satellite_order_manager import get_satellite_budget_status

    db = SessionLocal()
    try:
        b = get_satellite_budget_status(db)
        typer.echo(
            f"Budget: ${b['budget_usd']:.2f}, "
            f"Spent: ${b['spent_usd']:.2f}, "
            f"Remaining: ${b['remaining_usd']:.2f}"
        )
    finally:
        db.close()


app.add_typer(satellite_app, name="satellite")

# ---------------------------------------------------------------------------
# Flag risk profile management
# ---------------------------------------------------------------------------

flag_risk_app = typer.Typer(
    name="flag-risk",
    help="Flag state risk profile management (v2 data-driven scoring).",
    no_args_is_help=True,
)


@flag_risk_app.command("update")
def flag_risk_update(
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute but don't persist"),
) -> None:
    """Recompute all flag risk profiles from current DB data."""
    from app.database import SessionLocal
    from app.modules.flag_risk_analyzer import compute_flag_risk_profiles, persist_profiles

    db = SessionLocal()
    try:
        profiles = compute_flag_risk_profiles(db)
        typer.echo(f"Computed {len(profiles)} flag risk profiles")
        for p in profiles:
            typer.echo(f"  {p.flag_code}: composite={p.composite_score:.1f} tier={p.risk_tier}")
        if dry_run:
            typer.echo("Dry run — not persisted.")
        else:
            count = persist_profiles(db, profiles)
            typer.echo(f"Persisted {count} profiles.")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        db.close()


app.add_typer(flag_risk_app, name="flag-risk")

# ---------------------------------------------------------------------------
# Spire Maritime AIS management
# ---------------------------------------------------------------------------

spire_app = typer.Typer(
    name="spire",
    help="Spire Maritime satellite AIS management (Persian Gulf).",
    no_args_is_help=True,
)


@spire_app.command("status")
def spire_status() -> None:
    """Show Spire AIS quota and circuit breaker state."""
    from app.config import settings
    from app.modules.circuit_breakers import breakers

    typer.echo("Spire Maritime AIS Status")
    typer.echo(f"  API key configured: {bool(settings.SPIRE_AIS_API_KEY)}")
    typer.echo(f"  Collection enabled: {settings.SPIRE_AIS_COLLECTION_ENABLED}")
    typer.echo(f"  Monthly quota: {settings.SPIRE_MONTHLY_QUOTA}")
    typer.echo(f"  Lookback hours: {settings.SPIRE_LOOKBACK_HOURS}")
    typer.echo(f"  Collection interval: {settings.COLLECT_SPIRE_INTERVAL}s")

    cb = breakers.get("spire_ais")
    if cb:
        typer.echo(f"  Circuit breaker: {cb.current_state} (fails: {cb.fail_counter})")

    # Show quota usage from DB
    try:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            from app.modules.spire_ais_collector import _get_quota_used_this_month

            used = _get_quota_used_this_month(db)
            typer.echo(f"  Quota used this month: {used}/{settings.SPIRE_MONTHLY_QUOTA}")
        finally:
            db.close()
    except Exception:
        typer.echo("  Quota usage: unable to query DB")


@spire_app.command("test-connection")
def spire_test_connection() -> None:
    """Test Spire Maritime API connectivity."""
    from app.modules.spire_ais_client import SpireAisClient

    client = SpireAisClient()
    result = client.test_connection()
    if result["status"] == "ok":
        typer.echo(f"Connection OK: {result['detail']}")
    else:
        typer.echo(f"Connection FAILED: {result['detail']}", err=True)
        raise typer.Exit(1)


app.add_typer(spire_app, name="spire")
