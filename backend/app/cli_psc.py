"""PSC detention CLI sub-commands — import, sync, stats."""
from __future__ import annotations

import typer

from app.cli_app import app

psc_app = typer.Typer(
    name="psc",
    help="PSC detention management.",
    no_args_is_help=True,
)


@psc_app.command("import")
def psc_import(
    source: str = typer.Option("all", help="all|tokyo_mou|paris_mou|emsa"),
    ftm_path: str = typer.Option(None, help="Path to FTM JSON file (for tokyo_mou/paris_mou)"),
    emsa_path: str = typer.Option(None, help="Path to EMSA ban JSON file"),
):
    """Import PSC detention records from FTM or EMSA sources."""
    from app.database import SessionLocal
    from app.modules.psc_loader import load_psc_ftm, load_emsa_bans

    db = SessionLocal()
    try:
        if source in ("all", "tokyo_mou") and ftm_path:
            result = load_psc_ftm(db, ftm_path, source="tokyo_mou")
            typer.echo(f"FTM tokyo_mou import: {result}")
        if source in ("all", "paris_mou") and ftm_path:
            result = load_psc_ftm(db, ftm_path, source="paris_mou")
            typer.echo(f"FTM paris_mou import: {result}")
        if source in ("all", "emsa") and emsa_path:
            result = load_emsa_bans(db, emsa_path)
            typer.echo(f"EMSA import: {result}")
        if not ftm_path and not emsa_path:
            typer.echo("No data file paths provided. Use --ftm-path and/or --emsa-path.")
            raise typer.Exit(code=1)
    finally:
        db.close()


@psc_app.command("sync")
def psc_sync():
    """Recalculate boolean flags from detention records."""
    from app.database import SessionLocal
    from app.models.vessel import Vessel
    from app.modules.psc_loader import sync_vessel_psc_summary

    db = SessionLocal()
    try:
        vessels = db.query(Vessel).all()
        count = 0
        for v in vessels:
            sync_vessel_psc_summary(db, v)
            count += 1
        db.commit()
        typer.echo(f"Synced {count} vessels")
    finally:
        db.close()


@psc_app.command("stats")
def psc_stats():
    """Detention statistics by MOU source."""
    from app.database import SessionLocal
    from app.models.psc_detention import PscDetention
    from sqlalchemy import func

    db = SessionLocal()
    try:
        rows = db.query(PscDetention.mou_source, func.count()).group_by(PscDetention.mou_source).all()
        total = sum(r[1] for r in rows)
        typer.echo(f"Total detentions: {total}")
        for source, count in rows:
            typer.echo(f"  {source}: {count}")
    finally:
        db.close()


app.add_typer(psc_app, name="psc")
