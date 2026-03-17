"""Tests for SatelliteOrder and SatelliteOrderLog models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.satellite_order import SatelliteOrder
from app.models.satellite_order_log import SatelliteOrderLog


@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables for each test."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_create_satellite_order(db):
    """SatelliteOrder can be created with minimal required fields."""
    order = SatelliteOrder(
        provider="planet",
        order_type="archive_search",
        status="draft",
        aoi_wkt="POLYGON((10 55, 11 55, 11 56, 10 56, 10 55))",
    )
    db.add(order)
    db.commit()

    fetched = db.query(SatelliteOrder).first()
    assert fetched is not None
    assert fetched.provider == "planet"
    assert fetched.order_type == "archive_search"
    assert fetched.status == "draft"
    assert fetched.cost_confirmed is False


def test_update_satellite_order_status(db):
    """SatelliteOrder status can be updated through its lifecycle."""
    order = SatelliteOrder(
        provider="capella",
        order_type="new_tasking",
        status="draft",
    )
    db.add(order)
    db.commit()

    order.status = "submitted"
    order.external_order_id = "ext-123"
    db.commit()

    fetched = db.query(SatelliteOrder).first()
    assert fetched.status == "submitted"
    assert fetched.external_order_id == "ext-123"


def test_create_order_log_linked_to_order(db):
    """SatelliteOrderLog can be created and linked to a SatelliteOrder."""
    order = SatelliteOrder(
        provider="planet",
        order_type="archive_search",
        status="submitted",
    )
    db.add(order)
    db.commit()

    log = SatelliteOrderLog(
        satellite_order_id=order.satellite_order_id,
        action="submit_order",
        provider="planet",
        response_status=200,
        request_summary="POST /orders",
        response_summary="Order accepted",
    )
    db.add(log)
    db.commit()

    fetched_log = db.query(SatelliteOrderLog).first()
    assert fetched_log is not None
    assert fetched_log.action == "submit_order"
    assert fetched_log.response_status == 200


def test_order_logs_relationship(db):
    """Order.logs relationship returns associated log entries."""
    order = SatelliteOrder(
        provider="capella",
        order_type="archive_search",
        status="draft",
    )
    db.add(order)
    db.commit()

    for action in ["search", "submit", "check_status"]:
        log = SatelliteOrderLog(
            satellite_order_id=order.satellite_order_id,
            action=action,
            provider="capella",
        )
        db.add(log)
    db.commit()

    db.refresh(order)
    assert len(order.logs) == 3
    assert {log.action for log in order.logs} == {"search", "submit", "check_status"}


def test_order_with_cost_and_scene_urls(db):
    """SatelliteOrder stores cost and scene URL data correctly."""
    order = SatelliteOrder(
        provider="planet",
        order_type="archive_search",
        status="delivered",
        cost_usd=120.50,
        cost_confirmed=True,
        resolution_m=3.0,
        product_type="analytic",
        scene_urls_json={
            "urls": ["https://example.com/scene1.tif", "https://example.com/scene2.tif"]
        },
        time_window_start=datetime(2026, 1, 1, tzinfo=UTC),
        time_window_end=datetime(2026, 1, 15, tzinfo=UTC),
    )
    db.add(order)
    db.commit()

    fetched = db.query(SatelliteOrder).first()
    assert fetched.cost_usd == 120.50
    assert fetched.cost_confirmed is True
    assert fetched.resolution_m == 3.0
    assert fetched.scene_urls_json["urls"][0] == "https://example.com/scene1.tif"
    assert len(fetched.scene_urls_json["urls"]) == 2
