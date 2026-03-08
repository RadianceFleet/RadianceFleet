"""CLI commands for database backup and restore."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import typer

from app.cli_app import app
from app.config import settings


@app.command()
def backup(
    output: str = typer.Option(None, help="Output path (default: auto-generated)"),
):
    """Create a database backup (SQLite file copy or pg_dump)."""
    db_url = settings.DATABASE_URL

    if db_url.startswith("sqlite"):
        # Extract SQLite file path
        db_path = db_url.replace("sqlite:///", "")
        if not Path(db_path).exists():
            typer.echo(f"ERROR: SQLite database not found at {db_path}", err=True)
            raise typer.Exit(1)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = output or f"radiancefleet_backup_{timestamp}.db"
        shutil.copy2(db_path, out_path)
        size_mb = Path(out_path).stat().st_size / (1024 * 1024)
        typer.echo(f"Backup created: {out_path} ({size_mb:.1f} MB)")

    elif "postgresql" in db_url or "postgres" in db_url:
        # Use pg_dump
        if not shutil.which("pg_dump"):
            typer.echo("ERROR: pg_dump not found. Install PostgreSQL client tools.", err=True)
            raise typer.Exit(1)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = output or f"radiancefleet_backup_{timestamp}.sql.gz"

        # Parse DATABASE_URL for pg_dump
        # Format: postgresql+psycopg2://user:pass@host:port/dbname
        import urllib.parse

        url = db_url.replace("postgresql+psycopg2://", "postgresql://")
        parsed = urllib.parse.urlparse(url)

        env = os.environ.copy()
        if parsed.password:
            env["PGPASSWORD"] = parsed.password

        pg_args = ["pg_dump", "--no-owner", "--no-acl", "--format=custom"]
        if parsed.hostname:
            pg_args.extend(["-h", parsed.hostname])
        if parsed.port:
            pg_args.extend(["-p", str(parsed.port)])
        if parsed.username:
            pg_args.extend(["-U", parsed.username])
        pg_args.append(parsed.path.lstrip("/"))

        try:
            with open(out_path, "wb") as f:
                result = subprocess.run(pg_args, stdout=f, stderr=subprocess.PIPE, env=env)
            if result.returncode != 0:
                typer.echo(f"ERROR: pg_dump failed: {result.stderr.decode()}", err=True)
                raise typer.Exit(1)
            size_mb = Path(out_path).stat().st_size / (1024 * 1024)
            typer.echo(f"Backup created: {out_path} ({size_mb:.1f} MB)")
        except Exception as e:
            typer.echo(f"ERROR: Backup failed: {e}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo("ERROR: Unsupported database type in DATABASE_URL", err=True)
        raise typer.Exit(1)


@app.command()
def restore(
    backup_path: str = typer.Argument(..., help="Path to backup file"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm destructive restore"),
):
    """Restore a database from backup. WARNING: Overwrites current data."""
    if not confirm:
        typer.echo("WARNING: This will OVERWRITE the current database.")
        typer.echo("Re-run with --confirm to proceed.")
        raise typer.Exit(1)

    if not Path(backup_path).exists():
        typer.echo(f"ERROR: Backup file not found: {backup_path}", err=True)
        raise typer.Exit(1)

    db_url = settings.DATABASE_URL

    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "")
        # Create safety backup before restoring
        if Path(db_path).exists():
            safety = f"{db_path}.pre_restore"
            shutil.copy2(db_path, safety)
            typer.echo(f"Safety backup: {safety}")

        shutil.copy2(backup_path, db_path)
        typer.echo(f"Restored from {backup_path}")

    elif "postgresql" in db_url or "postgres" in db_url:
        if not shutil.which("pg_restore"):
            typer.echo("ERROR: pg_restore not found. Install PostgreSQL client tools.", err=True)
            raise typer.Exit(1)

        import urllib.parse

        url = db_url.replace("postgresql+psycopg2://", "postgresql://")
        parsed = urllib.parse.urlparse(url)

        env = os.environ.copy()
        if parsed.password:
            env["PGPASSWORD"] = parsed.password

        pg_args = ["pg_restore", "--no-owner", "--no-acl", "--clean", "--if-exists"]
        if parsed.hostname:
            pg_args.extend(["-h", parsed.hostname])
        if parsed.port:
            pg_args.extend(["-p", str(parsed.port)])
        if parsed.username:
            pg_args.extend(["-U", parsed.username])
        pg_args.extend(["-d", parsed.path.lstrip("/")])
        pg_args.append(backup_path)

        result = subprocess.run(pg_args, stderr=subprocess.PIPE, env=env)
        if result.returncode != 0:
            stderr = result.stderr.decode()
            # pg_restore returns non-zero on warnings too
            if "ERROR" in stderr:
                typer.echo(f"WARN: pg_restore had errors: {stderr}", err=True)
            else:
                typer.echo(f"Restored with warnings from {backup_path}")
        else:
            typer.echo(f"Restored from {backup_path}")
    else:
        typer.echo("ERROR: Unsupported database type", err=True)
        raise typer.Exit(1)
