"""Long-running AIS stream worker with health endpoint and graceful shutdown.

Connects to aisstream.io via WebSocket and continuously ingests AIS data.
Designed to run as a standalone background process (not inside FastAPI).

Usage:
    radiancefleet worker start
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import UTC, datetime
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# aisstream.io WebSocket endpoint
_WS_URL = settings.AISSTREAM_WS_URL


class AisstreamWorker:
    """Long-running WebSocket worker for aisstream.io AIS data ingestion."""

    def __init__(
        self,
        api_key: str,
        bounding_boxes: list[list[list[float]]],
        health_port: int = settings.AISSTREAM_WORKER_HEALTH_PORT,
        batch_interval: int = settings.AISSTREAM_BATCH_INTERVAL,
        stats_interval: int = settings.AISSTREAM_WORKER_STATS_INTERVAL_S,
    ) -> None:
        self.api_key = api_key
        self.bounding_boxes = bounding_boxes
        self.health_port = health_port
        self.batch_interval = batch_interval
        self.stats_interval = stats_interval

        # Shutdown coordination
        self._shutdown_event: asyncio.Event | None = None

        # Runtime stats
        self.stats: dict[str, Any] = {
            "messages_received": 0,
            "points_stored": 0,
            "vessels_updated": 0,
            "batches": 0,
            "batch_errors": 0,
            "errors": 0,
            "connected": False,
            "start_time": None,
            "last_batch_time": None,
        }

        # Buffers
        self._point_buffer: list[dict] = []
        self._static_buffer: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main entry: start health server, WebSocket loop, wait for shutdown."""
        self._shutdown_event = asyncio.Event()
        self.stats["start_time"] = time.time()

        self._setup_signal_handlers()

        health_task = asyncio.create_task(self._health_server())
        ws_task = asyncio.create_task(self._ws_loop())
        stats_task = asyncio.create_task(self._stats_logger())

        logger.info(
            "AIS stream worker starting — health on :%d, %d bounding boxes",
            self.health_port,
            len(self.bounding_boxes),
        )

        try:
            await self._shutdown_event.wait()
        finally:
            logger.info("Shutdown signal received — draining buffers")
            ws_task.cancel()
            stats_task.cancel()

            # Flush remaining buffers
            await self._flush_buffers()

            health_task.cancel()

            # Suppress CancelledError from tasks
            import contextlib

            for t in (ws_task, stats_task, health_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await t

            logger.info(
                "Worker stopped — %d messages, %d points stored",
                self.stats["messages_received"],
                self.stats["points_stored"],
            )

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers that set the shutdown event."""
        if sys.platform == "win32":
            return  # Windows: rely on KeyboardInterrupt in run()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_shutdown)

    def _signal_shutdown(self) -> None:
        """Called by signal handler — sets the shutdown event."""
        if self._shutdown_event and not self._shutdown_event.is_set():
            logger.info("Received shutdown signal")
            self._shutdown_event.set()

    # ------------------------------------------------------------------
    # WebSocket loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Connect to aisstream.io and process messages with auto-reconnect."""
        import websockets

        from app.modules.aisstream_client import _map_position_report, _map_static_data

        subscription = {
            "APIKey": self.api_key,
            "BoundingBoxes": self.bounding_boxes,
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ShipStaticData",
            ],
        }

        last_flush = time.monotonic()
        reconnect_delay = settings.AISSTREAM_WORKER_RECONNECT_DELAY_S
        attempts = 0

        while not self._shutdown_event.is_set():
            try:
                async with websockets.connect(_WS_URL) as ws:
                    await ws.send(json.dumps(subscription))
                    self.stats["connected"] = True
                    attempts = 0
                    logger.info("Connected to aisstream.io")

                    async for raw_msg in ws:
                        if self._shutdown_event.is_set():
                            break

                        try:
                            msg = json.loads(raw_msg)
                        except json.JSONDecodeError:
                            self.stats["errors"] += 1
                            continue

                        self.stats["messages_received"] += 1
                        msg_type = msg.get("MessageType", "")

                        if msg_type in ("PositionReport", "StandardClassBPositionReport"):
                            pt = _map_position_report(msg, msg_type=msg_type)
                            if pt:
                                self._point_buffer.append(pt)
                        elif msg_type == "ShipStaticData":
                            sd = _map_static_data(msg)
                            if sd:
                                self._static_buffer[sd["mmsi"]] = sd

                        # Periodic batch flush
                        now = time.monotonic()
                        if now - last_flush >= self.batch_interval and (
                            self._point_buffer or self._static_buffer
                        ):
                            await self._flush_buffers()
                            last_flush = now

            except asyncio.CancelledError:
                self.stats["connected"] = False
                return
            except Exception as exc:
                self.stats["connected"] = False
                attempts += 1
                if attempts > settings.AISSTREAM_WORKER_MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        "Max reconnect attempts (%d) reached: %s",
                        settings.AISSTREAM_WORKER_MAX_RECONNECT_ATTEMPTS,
                        exc,
                    )
                    if self._shutdown_event:
                        self._shutdown_event.set()
                    return

                delay = min(reconnect_delay * (2 ** min(attempts - 1, 6)), 300)
                logger.warning(
                    "Connection lost (%s), reconnecting in %.0fs (attempt %d)",
                    exc,
                    delay,
                    attempts,
                )
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
                    return  # Shutdown requested during backoff
                except TimeoutError:
                    pass  # Backoff elapsed — retry

        self.stats["connected"] = False

    # ------------------------------------------------------------------
    # Buffer flushing
    # ------------------------------------------------------------------

    async def _flush_buffers(self) -> None:
        """Flush point and static buffers to DB via thread pool."""
        if not self._point_buffer and not self._static_buffer:
            return

        points = list(self._point_buffer)
        static = dict(self._static_buffer)
        self._point_buffer.clear()
        self._static_buffer.clear()

        try:
            result = await asyncio.to_thread(self._ingest_sync, points, static)
            self.stats["points_stored"] += result["points_stored"]
            self.stats["vessels_updated"] += result["vessels_updated"]
            self.stats["batches"] += 1
            self.stats["last_batch_time"] = time.time()
        except Exception as exc:
            logger.error("Batch ingestion error: %s", exc)
            self.stats["batch_errors"] += 1

    @staticmethod
    def _ingest_sync(points: list[dict], static: dict[str, dict]) -> dict:
        """Run synchronous DB ingestion in a thread."""
        from app.database import SessionLocal
        from app.modules.aisstream_client import _ingest_batch

        db = SessionLocal()
        try:
            return _ingest_batch(db, points, static)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Health endpoint
    # ------------------------------------------------------------------

    async def _health_server(self) -> None:
        """Run a lightweight HTTP health endpoint using aiohttp."""
        from aiohttp import web

        async def health_handler(_request: web.Request) -> web.Response:
            uptime = time.time() - self.stats["start_time"] if self.stats["start_time"] else 0
            body = {
                "status": "ok",
                "messages_received": self.stats["messages_received"],
                "points_stored": self.stats["points_stored"],
                "vessels_updated": self.stats["vessels_updated"],
                "batches": self.stats["batches"],
                "batch_errors": self.stats["batch_errors"],
                "connected": self.stats["connected"],
                "uptime_seconds": round(uptime, 1),
                "last_batch_time": (
                    datetime.fromtimestamp(self.stats["last_batch_time"], tz=UTC).isoformat()
                    if self.stats["last_batch_time"]
                    else None
                ),
            }
            return web.json_response(body)

        app = web.Application()
        app.router.add_get("/health", health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.health_port)  # noqa: S104
        try:
            await site.start()
            logger.info("Health endpoint listening on :%d", self.health_port)
            # Block until cancelled
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    # ------------------------------------------------------------------
    # Stats logger
    # ------------------------------------------------------------------

    async def _stats_logger(self) -> None:
        """Periodically log worker stats."""
        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=self.stats_interval
                    )
                    return  # Shutdown requested
                except TimeoutError:
                    pass  # Interval elapsed

                uptime = time.time() - self.stats["start_time"] if self.stats["start_time"] else 0
                logger.info(
                    "Worker stats: %d msgs, %d pts stored, %d batches, connected=%s, uptime=%.0fs",
                    self.stats["messages_received"],
                    self.stats["points_stored"],
                    self.stats["batches"],
                    self.stats["connected"],
                    uptime,
                )
        except asyncio.CancelledError:
            pass
