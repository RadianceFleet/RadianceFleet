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
        raise typer.Exit(1)
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
        raise typer.Exit(1)
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
