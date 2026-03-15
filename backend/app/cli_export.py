"""CLI commands for bulk export subscription management."""

from __future__ import annotations

import typer

from app.cli_app import app

export_app = typer.Typer(
    name="export",
    help="Bulk data export subscription management.",
    no_args_is_help=True,
)


@export_app.command("list")
def export_list() -> None:
    """List all export subscriptions."""
    from app.database import SessionLocal
    from app.models.export_subscription import ExportSubscription

    db = SessionLocal()
    try:
        subs = db.query(ExportSubscription).order_by(ExportSubscription.subscription_id).all()
        if not subs:
            typer.echo("No export subscriptions found.")
            return
        for s in subs:
            status = "active" if s.is_active else "inactive"
            last_run = s.last_run_at.isoformat() if s.last_run_at else "never"
            typer.echo(
                f"  [{s.subscription_id}] {s.name} — {s.schedule} {s.export_type} "
                f"({s.format}) via {s.delivery_method} [{status}] last_run={last_run}"
            )
    finally:
        db.close()


@export_app.command("run")
def export_run(
    subscription_id: int = typer.Argument(..., help="Subscription ID to run"),
) -> None:
    """Manually trigger an export for a subscription."""
    from datetime import UTC, datetime
    from pathlib import Path

    from app.config import settings
    from app.database import SessionLocal
    from app.models.export_run import ExportRun
    from app.models.export_subscription import ExportSubscription
    from app.modules.export_delivery import deliver
    from app.modules.export_engine import generate_export

    db = SessionLocal()
    try:
        sub = (
            db.query(ExportSubscription)
            .filter(ExportSubscription.subscription_id == subscription_id)
            .first()
        )
        if not sub:
            typer.echo(f"ERROR: Subscription {subscription_id} not found", err=True)
            raise typer.Exit(1)

        typer.echo(f"Running export for subscription: {sub.name}")
        now = datetime.now(UTC)
        run = ExportRun(
            subscription_id=sub.subscription_id,
            started_at=now,
            status="running",
        )
        db.add(run)
        db.flush()

        file_bytes, filename, row_count = generate_export(db, sub)

        export_dir = Path(settings.EXPORT_TEMP_DIR)
        export_dir.mkdir(parents=True, exist_ok=True)
        file_path = export_dir / filename
        file_path.write_bytes(file_bytes)

        run.row_count = row_count
        run.file_size_bytes = len(file_bytes)
        run.file_path = str(file_path)

        delivery_config = sub.delivery_config_json or {}
        delivery_result = deliver(file_bytes, filename, sub.delivery_method, delivery_config)

        run.delivery_status = delivery_result.get("status", "failed")
        run.status = "completed" if delivery_result.get("status") == "sent" else "failed"
        run.finished_at = datetime.now(UTC)
        if run.status == "failed":
            run.error_message = delivery_result.get("error")

        sub.last_run_at = now
        sub.last_run_status = run.status
        sub.last_run_rows = row_count
        db.commit()

        typer.echo(
            f"Export complete: {row_count} rows, {len(file_bytes)} bytes, "
            f"status={run.status}, delivery={run.delivery_status}"
        )
        typer.echo(f"File saved: {file_path}")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        db.close()


@export_app.command("run-due")
def export_run_due() -> None:
    """Process all due export subscriptions."""
    from app.database import SessionLocal
    from app.modules.export_scheduler import run_due_exports

    db = SessionLocal()
    try:
        results = run_due_exports(db)
        if not results:
            typer.echo("No exports are due.")
            return
        for r in results:
            typer.echo(
                f"  Subscription {r['subscription_id']}: "
                f"run_id={r.get('run_id')}, status={r['status']}, "
                f"rows={r.get('row_count', 'N/A')}"
            )
        typer.echo(f"Processed {len(results)} exports.")
    except Exception as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        db.close()


@export_app.command("cleanup")
def export_cleanup() -> None:
    """Delete expired export files."""
    from app.modules.export_scheduler import cleanup_expired_files

    deleted = cleanup_expired_files()
    typer.echo(f"Deleted {deleted} expired export file(s).")


app.add_typer(export_app, name="export")
