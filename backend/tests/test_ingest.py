"""Ingest module tests — VesselHistory deduplication."""
from datetime import datetime
from unittest.mock import MagicMock


def test_vessel_history_dedup_within_24h():
    """Calling _track_field_change twice with same data within 24h creates only one record."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "123456789"

    # First call: no existing record → should add
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    _track_field_change(mock_db, vessel, "flag", "PA", "LR", datetime(2026, 1, 15), "csv")
    assert mock_db.add.called, "First call should add a record"

    # Second call: simulate existing record found → should skip (dedup)
    mock_db2 = MagicMock()
    mock_db2.query.return_value.filter.return_value.first.return_value = MagicMock()

    _track_field_change(mock_db2, vessel, "flag", "PA", "LR", datetime(2026, 1, 15, 6, 0), "csv")
    assert not mock_db2.add.called, "Second call should skip (dedup)"


def test_track_field_change_skips_none_values():
    """_track_field_change returns early when old_val or new_val is None."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "123456789"

    mock_db = MagicMock()

    _track_field_change(mock_db, vessel, "flag", None, "LR", datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Should skip when old_val is None"

    _track_field_change(mock_db, vessel, "flag", "PA", None, datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Should skip when new_val is None"


def test_track_field_change_skips_same_value():
    """_track_field_change skips when old and new values are identical (case-insensitive)."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "123456789"

    mock_db = MagicMock()

    _track_field_change(mock_db, vessel, "name", "TANKER ONE", "tanker one", datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Same value (case-insensitive) should not create a record"
