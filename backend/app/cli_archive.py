"""CLI sub-commands for data retention and archival."""

from __future__ import annotations

import typer

from app.cli_app import app, console

archive_app = typer.Typer(
    name="archive",
    help="AIS data archival and retention management.",
    no_args_is_help=True,
)


@archive_app.command("run")
def archive_run(
    cutoff_days: int = typer.Option(90, "--cutoff-days", help="Archive points older than N days"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be archived without doing it"),
    source: str | None = typer.Option(None, "--source", help="Only archive points from this source"),
) -> None:
    """Archive old AIS points to compressed Parquet files."""
    from datetime import UTC, datetime, timedelta

    from app.config import settings
    from app.database import SessionLocal

    if not getattr(settings, "ARCHIVE_ENABLED", True):
        console.print("[red]Archival is disabled (ARCHIVE_ENABLED=False)[/red]")
        raise typer.Exit(1)

    cutoff_date = datetime.now(UTC) - timedelta(days=cutoff_days)
    console.print(f"Archiving AIS points older than {cutoff_date.date()} ({cutoff_days} days)")

    if dry_run:
        db = SessionLocal()
        try:
            from app.models.ais_point import AISPoint

            q = db.query(AISPoint).filter(AISPoint.timestamp_utc < cutoff_date)
            if source:
                q = q.filter(AISPoint.source == source)
            count = q.count()
            console.print(f"[yellow]Dry run:[/yellow] would archive {count} points")
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        from app.modules.ais_archiver import archive_old_points

        batch = archive_old_points(db, cutoff_date, source=source)
        console.print(
            f"[green]Archived {batch.row_count} points[/green] -> {batch.file_path} "
            f"({batch.file_size_bytes} bytes)"
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None
    finally:
        db.close()


@archive_app.command("list")
def archive_list() -> None:
    """List all archive batches."""
    from app.database import SessionLocal
    from app.models.ais_archive_batch import AisArchiveBatch

    db = SessionLocal()
    try:
        batches = db.query(AisArchiveBatch).order_by(AisArchiveBatch.archive_date.desc()).all()
        if not batches:
            console.print("No archive batches found.")
            return
        for b in batches:
            console.print(
                f"  Batch {b.batch_id}: {b.row_count} rows, "
                f"{b.file_size_bytes} bytes, status={b.status}, "
                f"date={b.archive_date.date() if b.archive_date else 'N/A'}"
            )
    finally:
        db.close()


@archive_app.command("restore")
def archive_restore(
    batch_id: int = typer.Argument(..., help="Batch ID to restore"),
) -> None:
    """Restore an archived batch back into the database."""
    from app.database import SessionLocal
    from app.modules.ais_archiver import restore_archive_batch

    db = SessionLocal()
    try:
        count = restore_archive_batch(db, batch_id)
        console.print(f"[green]Restored {count} rows from batch {batch_id}[/green]")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None
    finally:
        db.close()


@archive_app.command("verify")
def archive_verify(
    batch_id: int = typer.Argument(..., help="Batch ID to verify"),
) -> None:
    """Verify the integrity of an archive file."""
    from app.database import SessionLocal
    from app.models.ais_archive_batch import AisArchiveBatch
    from app.modules.ais_archiver import verify_archive_integrity

    db = SessionLocal()
    try:
        batch = db.query(AisArchiveBatch).filter(AisArchiveBatch.batch_id == batch_id).first()
        if not batch:
            console.print(f"[red]Batch {batch_id} not found[/red]")
            raise typer.Exit(1)
        valid = verify_archive_integrity(batch)
        if valid:
            console.print(f"[green]Batch {batch_id}: integrity OK[/green]")
        else:
            console.print(f"[red]Batch {batch_id}: integrity FAILED[/red]")
            raise typer.Exit(1)
    finally:
        db.close()


@archive_app.command("stats")
def archive_stats() -> None:
    """Show data retention and archival statistics."""
    from app.database import SessionLocal
    from app.modules.ais_archiver import get_retention_stats

    db = SessionLocal()
    try:
        stats = get_retention_stats(db)
        console.print("Data Retention Statistics:")
        if stats["db_size_bytes"] is not None:
            mb = stats["db_size_bytes"] / (1024 * 1024)
            console.print(f"  DB size: {mb:.1f} MB")
        console.print(f"  AIS points in DB: {stats['ais_point_count']}")
        console.print(f"  Archive batches: {stats['archive_count']}")
        console.print(f"  Total archived rows: {stats['total_archived_rows']}")
        if stats["total_archive_size_bytes"]:
            mb = stats["total_archive_size_bytes"] / (1024 * 1024)
            console.print(f"  Total archive size: {mb:.1f} MB")
    finally:
        db.close()


app.add_typer(archive_app, name="archive")
