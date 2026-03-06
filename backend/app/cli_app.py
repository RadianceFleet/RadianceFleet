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
