"""Tests for E2: Ingest validation hardening.

Verifies:
  1. Future timestamp ceiling (now + 7 days) — rejects far-future, accepts near-future
  2. Impossible SOG warning (> 50 knots) — tags but doesn't reject
  3. Anchored + high SOG warning (nav_status=1 and SOG > 3) — tags but doesn't reject
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.normalize import validate_ais_row


def _valid_row(**overrides):
    """Return a valid AIS row dict with optional field overrides."""
    row = {
        "mmsi": "241234567",
        "lat": 55.0,
        "lon": 12.0,
        "sog": 10.0,
        "cog": 180.0,
        "heading": 200.0,
        "timestamp_utc": "2025-06-01T00:00:00Z",
    }
    row.update(overrides)
    return row


# ── E2.1: Future timestamp ceiling ──────────────────────────────────────────

class TestFutureTimestampCeiling:
    """Timestamps > now + 7 days should be rejected; within 7 days should pass."""

    def test_far_future_timestamp_rejected(self):
        """Timestamp 10 days in the future should be rejected."""
        far_future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        row = _valid_row(timestamp_utc=far_future)
        error = validate_ais_row(row)
        assert error is not None, "Timestamp 10 days in future should be rejected"
        assert "Future timestamp rejected" in error

    def test_far_future_timestamp_logs_warning(self, caplog):
        """Rejection of far-future timestamp should log a warning."""
        far_future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        row = _valid_row(timestamp_utc=far_future)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            validate_ais_row(row)
        assert any("future timestamp" in msg.lower() for msg in caplog.messages), \
            f"Expected future timestamp warning, got: {caplog.messages}"

    def test_near_future_timestamp_accepted(self):
        """Timestamp 1 day in the future should be accepted (within 7-day ceiling)."""
        near_future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        row = _valid_row(timestamp_utc=near_future)
        error = validate_ais_row(row)
        assert error is None, f"Timestamp 1 day in future should be accepted, got: {error}"

    def test_timestamp_6_days_future_accepted(self):
        """Timestamp 6 days in the future should be accepted (under 7-day ceiling)."""
        future_6d = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
        row = _valid_row(timestamp_utc=future_6d)
        error = validate_ais_row(row)
        assert error is None, f"Timestamp 6 days in future should be accepted, got: {error}"

    def test_timestamp_8_days_future_rejected(self):
        """Timestamp 8 days in the future should be rejected (over 7-day ceiling)."""
        future_8d = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
        row = _valid_row(timestamp_utc=future_8d)
        error = validate_ais_row(row)
        assert error is not None, "Timestamp 8 days in future should be rejected"

    def test_past_timestamp_still_accepted(self):
        """Normal past timestamps should still be accepted."""
        row = _valid_row(timestamp_utc="2025-06-01T00:00:00Z")
        error = validate_ais_row(row)
        assert error is None

    def test_pre_2010_still_rejected(self):
        """Timestamps before 2010 should still be rejected."""
        row = _valid_row(timestamp_utc="2009-12-31T23:59:59Z")
        error = validate_ais_row(row)
        assert error is not None
        assert "pre-2010" in error


# ── E2.2: Impossible SOG warning ─────────────────────────────────────────────

class TestImpossibleSOGWarning:
    """SOG > 50 knots should be flagged with a warning (but SOG > 35 still rejects)."""

    def test_sog_above_50_logs_warning(self, caplog):
        """SOG of 55 knots should log a suspicious SOG warning (before rejection at 35)."""
        # Note: SOG > 35 is rejected, but the warning fires first for SOG > 50
        row = _valid_row(sog=55.0)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        # SOG 55 > 35 so it will be rejected
        assert error is not None, "SOG 55 should still be rejected (> 35 physical limit)"
        assert any("Suspicious SOG" in msg for msg in caplog.messages), \
            f"Expected suspicious SOG warning, got: {caplog.messages}"

    def test_sog_above_50_adds_quality_flag(self):
        """SOG > 50 should add a quality flag to the row."""
        row = _valid_row(sog=55.0)
        validate_ais_row(row)
        flags = row.get("_quality_flags", [])
        assert any("suspicious_sog" in f for f in flags), \
            f"Expected suspicious_sog quality flag, got: {flags}"

    def test_sog_40_no_warning(self, caplog):
        """SOG of 40 (> 35 but < 50) should be rejected but NOT log suspicious SOG warning."""
        row = _valid_row(sog=40.0)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is not None, "SOG 40 should be rejected (> 35)"
        assert not any("Suspicious SOG" in msg for msg in caplog.messages), \
            "SOG 40 should not trigger suspicious SOG warning (only > 50)"

    def test_sog_30_no_warning(self, caplog):
        """SOG of 30 (normal) should pass without warnings."""
        row = _valid_row(sog=30.0)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert not any("Suspicious SOG" in msg for msg in caplog.messages)

    def test_sog_sentinel_no_warning(self, caplog):
        """SOG sentinel value (102.3) should be converted to None, not flagged."""
        row = _valid_row(sog=102.3)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert row["sog"] is None
        assert not any("Suspicious SOG" in msg for msg in caplog.messages)


# ── E2.3: Anchored + high SOG warning ───────────────────────────────────────

class TestAnchoredHighSOGWarning:
    """Anchored vessel (nav_status=1) with SOG > 3 should be flagged."""

    def test_anchored_high_sog_logs_warning(self, caplog):
        """Anchored vessel with SOG 5 knots should log a data quality warning."""
        row = _valid_row(sog=5.0, nav_status=1)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None, "Anchored+high SOG should not reject the row"
        assert any("Anchored vessel" in msg for msg in caplog.messages), \
            f"Expected anchored vessel warning, got: {caplog.messages}"
        assert any("241234567" in msg for msg in caplog.messages), \
            "Warning should include MMSI"

    def test_anchored_high_sog_adds_quality_flag(self):
        """Anchored + high SOG should add a quality flag."""
        row = _valid_row(sog=5.0, nav_status=1)
        validate_ais_row(row)
        flags = row.get("_quality_flags", [])
        assert any("anchored_high_sog" in f for f in flags), \
            f"Expected anchored_high_sog quality flag, got: {flags}"

    def test_anchored_low_sog_no_warning(self, caplog):
        """Anchored vessel with SOG 2 knots (below threshold) should not warn."""
        row = _valid_row(sog=2.0, nav_status=1)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert not any("Anchored vessel" in msg for msg in caplog.messages)

    def test_not_anchored_high_sog_no_warning(self, caplog):
        """Non-anchored vessel with SOG 5 knots should not trigger anchored warning."""
        row = _valid_row(sog=5.0, nav_status=0)  # nav_status 0 = under way using engine
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert not any("Anchored vessel" in msg for msg in caplog.messages)

    def test_anchored_exactly_3_knots_no_warning(self, caplog):
        """Anchored vessel with exactly 3 knots should NOT warn (threshold is > 3)."""
        row = _valid_row(sog=3.0, nav_status=1)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert not any("Anchored vessel" in msg for msg in caplog.messages)

    def test_anchored_3_1_knots_warns(self, caplog):
        """Anchored vessel with 3.1 knots should warn."""
        row = _valid_row(sog=3.1, nav_status=1)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert any("Anchored vessel" in msg for msg in caplog.messages)

    def test_no_nav_status_no_warning(self, caplog):
        """Missing nav_status should not trigger anchored warning."""
        row = _valid_row(sog=5.0)
        # Ensure no nav_status
        row.pop("nav_status", None)
        with caplog.at_level(logging.WARNING, logger="app.modules.normalize"):
            error = validate_ais_row(row)
        assert error is None
        assert not any("Anchored vessel" in msg for msg in caplog.messages)
