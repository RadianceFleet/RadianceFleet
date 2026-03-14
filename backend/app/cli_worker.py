"""Worker CLI sub-commands — AIS stream worker management."""

from __future__ import annotations

import typer

from app.cli_app import app

worker_app = typer.Typer(
    name="worker",
    help="AIS stream worker management.",
    no_args_is_help=True,
)
app.add_typer(worker_app, name="worker")


@worker_app.command("start")
def worker_start(
    health_port: int = typer.Option(
        None, "--health-port", help="Health endpoint port (default: from config)"
    ),
) -> None:
    """Start the AIS stream worker (foreground, long-running)."""
    import asyncio

    from app.config import settings
    from app.database import SessionLocal

    api_key = settings.AISSTREAM_API_KEY
    if not api_key:
        typer.echo("Error: AISSTREAM_API_KEY is not set.", err=True)
        raise typer.Exit(1)

    # Get bounding boxes from corridors
    from app.modules.aisstream_client import get_corridor_bounding_boxes

    db = SessionLocal()
    try:
        bounding_boxes = get_corridor_bounding_boxes(db)
    finally:
        db.close()

    if not bounding_boxes:
        bounding_boxes = [
            [[54.0, 10.0], [66.0, 30.0]],  # Baltic Sea
            [[40.0, 27.0], [47.0, 42.0]],  # Black Sea
        ]
        typer.echo("No corridor bounding boxes found — using default Baltic/Black Sea regions.")

    from app.modules.aisstream_worker import AisstreamWorker

    port = health_port if health_port is not None else settings.AISSTREAM_WORKER_HEALTH_PORT
    worker = AisstreamWorker(
        api_key=api_key,
        bounding_boxes=bounding_boxes,
        health_port=port,
    )

    typer.echo(f"Starting AIS stream worker (health port {port})...")
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        typer.echo("\nWorker stopped.")


@worker_app.command("status")
def worker_status(
    port: int = typer.Option(
        None, "--port", help="Health endpoint port (default: from config)"
    ),
) -> None:
    """Check worker health endpoint status."""
    import json
    import urllib.request

    from app.config import settings

    health_port = port if port is not None else settings.AISSTREAM_WORKER_HEALTH_PORT
    url = f"http://localhost:{health_port}/health"

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())

        typer.echo(f"Status:    {data.get('status', 'unknown')}")
        typer.echo(f"Connected: {data.get('connected', False)}")
        typer.echo(f"Messages:  {data.get('messages_received', 0)}")
        typer.echo(f"Points:    {data.get('points_stored', 0)}")
        typer.echo(f"Batches:   {data.get('batches', 0)}")
        typer.echo(f"Errors:    {data.get('batch_errors', 0)}")
        uptime = data.get("uptime_seconds", 0)
        hours = int(uptime // 3600)
        mins = int((uptime % 3600) // 60)
        typer.echo(f"Uptime:    {hours}h {mins}m")
        if data.get("last_batch_time"):
            typer.echo(f"Last batch: {data['last_batch_time']}")
    except Exception as exc:
        typer.echo(f"Worker not reachable at {url}: {exc}", err=True)
        raise typer.Exit(1) from None
