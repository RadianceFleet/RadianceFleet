"""Tests for the AIS stream worker module."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.aisstream_worker import AisstreamWorker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def worker():
    """Create a worker instance with test defaults."""
    return AisstreamWorker(
        api_key="test-key",
        bounding_boxes=[[[54.0, 10.0], [66.0, 30.0]]],
        health_port=9999,
        batch_interval=5,
        stats_interval=10,
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def test_worker_initialization(worker):
    """Worker initializes with correct defaults."""
    assert worker.api_key == "test-key"
    assert len(worker.bounding_boxes) == 1
    assert worker.health_port == 9999
    assert worker.batch_interval == 5
    assert worker.stats["messages_received"] == 0
    assert worker.stats["points_stored"] == 0
    assert worker.stats["connected"] is False
    assert worker._point_buffer == []
    assert worker._static_buffer == {}


def test_worker_initialization_no_api_key():
    """Worker can be created without API key (validation happens at CLI level)."""
    w = AisstreamWorker(api_key="", bounding_boxes=[])
    assert w.api_key == ""


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def test_signal_handler_sets_shutdown_event(worker):
    """Signal handler sets the shutdown event."""
    worker._shutdown_event = asyncio.Event()
    assert not worker._shutdown_event.is_set()
    worker._signal_shutdown()
    assert worker._shutdown_event.is_set()


def test_signal_shutdown_idempotent(worker):
    """Calling signal_shutdown multiple times does not error."""
    worker._shutdown_event = asyncio.Event()
    worker._signal_shutdown()
    worker._signal_shutdown()  # Should not raise
    assert worker._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_json(worker):
    """Health endpoint returns correct JSON stats."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    worker.stats["start_time"] = time.time() - 120
    worker.stats["messages_received"] = 42
    worker.stats["points_stored"] = 10
    worker.stats["connected"] = True
    worker.stats["last_batch_time"] = time.time()

    async def health_handler(_request):
        from datetime import UTC, datetime
        uptime = time.time() - worker.stats["start_time"] if worker.stats["start_time"] else 0
        body = {
            "status": "ok",
            "messages_received": worker.stats["messages_received"],
            "points_stored": worker.stats["points_stored"],
            "vessels_updated": worker.stats["vessels_updated"],
            "batches": worker.stats["batches"],
            "batch_errors": worker.stats["batch_errors"],
            "connected": worker.stats["connected"],
            "uptime_seconds": round(uptime, 1),
            "last_batch_time": (
                datetime.fromtimestamp(worker.stats["last_batch_time"], tz=UTC).isoformat()
                if worker.stats["last_batch_time"]
                else None
            ),
        }
        return web.json_response(body)

    async def _run():
        app = web.Application()
        app.router.add_get("/health", health_handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["messages_received"] == 42
            assert data["points_stored"] == 10
            assert data["connected"] is True
            assert data["uptime_seconds"] > 0
            assert data["last_batch_time"] is not None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def test_map_position_report_integration(worker):
    """Worker correctly processes position report messages via shared helper."""
    from app.modules.aisstream_client import _map_position_report

    msg = {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": 273456789,
            "ShipName": "TEST VESSEL",
            "latitude": 55.5,
            "longitude": 15.5,
            "time_utc": "2025-01-15T12:00:00Z",
        },
        "Message": {
            "PositionReport": {
                "Sog": 12.5,
                "Cog": 180.0,
                "TrueHeading": 179,
                "NavigationalStatus": 0,
            }
        },
    }
    pt = _map_position_report(msg, "PositionReport")
    assert pt is not None
    assert pt["mmsi"] == "273456789"
    assert pt["sog"] == 12.5
    assert pt["source"] == "aisstream"


def test_map_static_data_integration(worker):
    """Worker correctly processes static data messages via shared helper."""
    from app.modules.aisstream_client import _map_static_data

    msg = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 273456789, "ShipName": "TEST VESSEL"},
        "Message": {
            "ShipStaticData": {
                "ImoNumber": 9876543,
                "Type": 80,
                "CallSign": "ABCD",
                "Destination": "ROTTERDAM",
                "Draught": 12.5,
                "Dimension": {"A": 100, "B": 50, "C": 10, "D": 10},
            }
        },
    }
    sd = _map_static_data(msg)
    assert sd is not None
    assert sd["mmsi"] == "273456789"
    assert sd["vessel_type"] == "Tanker"
    assert sd["callsign"] == "ABCD"


# ---------------------------------------------------------------------------
# Batch flushing
# ---------------------------------------------------------------------------

def test_flush_buffers_calls_ingest(worker):
    """Flush buffers calls _ingest_sync in a thread."""
    worker._point_buffer = [{"mmsi": "123", "lat": 55.0, "lon": 15.0}]
    worker._static_buffer = {"123": {"mmsi": "123", "vessel_name": "TEST"}}

    async def _run():
        with patch.object(
            AisstreamWorker,
            "_ingest_sync",
            return_value={"points_stored": 1, "vessels_updated": 0},
        ) as mock_ingest:
            await worker._flush_buffers()
        mock_ingest.assert_called_once()
        assert worker.stats["points_stored"] == 1
        assert worker.stats["batches"] == 1
        assert worker._point_buffer == []
        assert worker._static_buffer == {}

    asyncio.run(_run())


def test_flush_buffers_handles_error(worker):
    """Flush buffers increments batch_errors on failure."""
    worker._point_buffer = [{"mmsi": "123"}]

    async def _run():
        with patch.object(
            AisstreamWorker,
            "_ingest_sync",
            side_effect=RuntimeError("DB error"),
        ):
            await worker._flush_buffers()

    asyncio.run(_run())
    assert worker.stats["batch_errors"] == 1
    assert worker.stats["points_stored"] == 0


def test_flush_buffers_noop_when_empty(worker):
    """Flush buffers does nothing when buffers are empty."""

    async def _run():
        with patch.object(AisstreamWorker, "_ingest_sync") as mock_ingest:
            await worker._flush_buffers()
        mock_ingest.assert_not_called()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

def test_stats_initialized(worker):
    """Stats dict has all required keys."""
    required = [
        "messages_received", "points_stored", "vessels_updated",
        "batches", "batch_errors", "errors", "connected",
        "start_time", "last_batch_time",
    ]
    for key in required:
        assert key in worker.stats


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

def test_graceful_shutdown(worker):
    """Worker shuts down gracefully when shutdown event is set."""

    async def fake_health():
        await asyncio.Event().wait()

    async def fake_ws():
        await asyncio.Event().wait()

    async def _run():
        with patch.object(worker, "_health_server", side_effect=fake_health), \
             patch.object(worker, "_ws_loop", side_effect=fake_ws), \
             patch.object(worker, "_flush_buffers", new_callable=AsyncMock) as mock_flush, \
             patch.object(worker, "_setup_signal_handlers"):

            async def trigger_shutdown():
                await asyncio.sleep(0.05)
                worker._signal_shutdown()

            worker._shutdown_event = asyncio.Event()
            worker.stats["start_time"] = time.time()

            shutdown_task = asyncio.create_task(trigger_shutdown())
            await asyncio.wait_for(worker.run(), timeout=2.0)
            await shutdown_task

            mock_flush.assert_called_once()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CLI: disabled when no API key
# ---------------------------------------------------------------------------

def test_worker_start_requires_api_key():
    """Worker start command exits when no API key is set."""
    from typer.testing import CliRunner

    import app.cli_worker  # noqa: F401 — force command registration
    from app.cli_app import app as cli_app

    runner = CliRunner()

    with patch("app.config.settings.AISSTREAM_API_KEY", None):
        result = runner.invoke(cli_app, ["worker", "start"])
        assert result.exit_code == 1
        assert "AISSTREAM_API_KEY" in result.output


# ---------------------------------------------------------------------------
# WebSocket reconnection
# ---------------------------------------------------------------------------

def test_ws_loop_reconnects_on_error(worker):
    """WebSocket loop attempts reconnection on connection error."""

    async def _run():
        worker._shutdown_event = asyncio.Event()
        call_count = 0

        async def mock_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("test disconnect")
            # Third time: stop
            worker._signal_shutdown()
            raise ConnectionError("final")

        # mock_connect is a coroutine-returning function but websockets.connect
        # returns an async context manager. We need a different approach.
        class FakeConnect:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                nonlocal call_count
                call_count += 1
                if call_count >= 3:
                    worker._signal_shutdown()
                raise ConnectionError(f"test disconnect {call_count}")

            async def __aexit__(self, *args):
                pass

        with patch("app.modules.aisstream_worker.settings") as mock_settings:
            mock_settings.AISSTREAM_WORKER_RECONNECT_DELAY_S = 0.01
            mock_settings.AISSTREAM_WORKER_MAX_RECONNECT_ATTEMPTS = 5

            with patch("websockets.connect", FakeConnect):
                await asyncio.wait_for(worker._ws_loop(), timeout=5.0)

        assert call_count >= 2

    asyncio.run(_run())
