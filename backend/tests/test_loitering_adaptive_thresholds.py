"""Tests for per-corridor adaptive loitering thresholds."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_mock_vessel


# ── Helpers ──────────────────────────────────────────────────────────


def _make_ais_point(vessel_id, ts, lat, lon, sog=0.1):
    p = MagicMock()
    p.vessel_id = vessel_id
    p.timestamp_utc = ts
    p.lat = lat
    p.lon = lon
    p.sog = sog
    return p


def _make_vessel(vessel_id=1, mmsi="123456789"):
    return make_mock_vessel(
        vessel_id=vessel_id,
        mmsi=mmsi,
        vessel_laid_up_30d=False,
        vessel_laid_up_60d=False,
        vessel_laid_up_in_sts_zone=False,
    )


def _make_corridor(corridor_id=1, name="Test STS", corridor_type="sts_zone"):
    c = MagicMock()
    c.corridor_id = corridor_id
    c.name = name
    c.corridor_type = MagicMock()
    c.corridor_type.value = corridor_type
    c.geometry = None
    return c


def _generate_loitering_track(vessel_id, hours=6, sog=0.1, lat=25.0, lon=55.0):
    """Generate AIS points with low SOG over several hours (loitering)."""
    base = datetime(2026, 1, 15, 10, 0, 0)
    points = []
    for i in range(hours * 6):  # 6 points per hour (every 10 min)
        ts = base + timedelta(minutes=i * 10)
        points.append(_make_ais_point(vessel_id, ts, lat, lon, sog))
    return points


def _setup_db_mock(points, corridors=None, existing_event=None):
    """Set up a DB mock that returns points, corridors, and handles dedup/gap queries."""
    db = MagicMock()

    call_count = [0]

    def query_side_effect(model):
        call_count[0] += 1
        mock = MagicMock()

        model_name = getattr(model, "__name__", str(model))

        if model_name == "AISPoint":
            chain = MagicMock()
            chain.all.return_value = points
            chain.filter.return_value = chain
            chain.order_by.return_value = chain
            return chain
        elif model_name == "Corridor":
            chain = MagicMock()
            chain.all.return_value = corridors if corridors is not None else []
            return chain
        elif model_name == "LoiteringEvent":
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.first.return_value = existing_event
            return chain
        else:
            # AISGapEvent or others — return None for gap lookups
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.order_by.return_value = chain
            chain.first.return_value = None
            return chain

    db.query.side_effect = query_side_effect
    return db


_ADAPTIVE_CONFIG = {
    "loitering_by_corridor_type": {
        "enabled": True,
        "sts_zone": {"min_hours": 2, "sustained_hours": 8},
        "anchorage_holding": {"min_hours": 8, "sustained_hours": 24},
        "export_route": {"min_hours": 4, "sustained_hours": 12},
        "default": {"min_hours": 4, "sustained_hours": 12},
    },
    "detection_thresholds": {
        "loitering": {
            "sog_threshold_kn": 0.5,
            "min_hours": 4,
            "sustained_hours": 12,
            "risk_baseline": 8,
            "risk_sustained": 20,
        }
    },
}

_DISABLED_CONFIG = {
    "loitering_by_corridor_type": {
        "enabled": False,
        "sts_zone": {"min_hours": 2, "sustained_hours": 8},
        "default": {"min_hours": 4, "sustained_hours": 12},
    },
    "detection_thresholds": {
        "loitering": {
            "sog_threshold_kn": 0.5,
            "min_hours": 4,
            "sustained_hours": 12,
            "risk_baseline": 8,
            "risk_sustained": 20,
        }
    },
}


class TestAdaptiveLoiteringThresholds:
    """Tests for per-corridor adaptive loitering thresholds."""

    def test_sts_zone_triggers_at_3h(self):
        """STS zone has min_hours=2, so a 3h loitering event should trigger."""
        from app.modules.loitering_detector import _get_min_hours_for_corridor

        corridor = _make_corridor(corridor_type="sts_zone")

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_ADAPTIVE_CONFIG,
        ):
            min_h = _get_min_hours_for_corridor(corridor)
            assert min_h == 2

    def test_anchorage_no_trigger_at_6h(self):
        """Anchorage has min_hours=8, so 6h should NOT be enough."""
        from app.modules.loitering_detector import _get_min_hours_for_corridor

        corridor = _make_corridor(corridor_type="anchorage_holding")

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_ADAPTIVE_CONFIG,
        ):
            min_h = _get_min_hours_for_corridor(corridor)
            assert min_h == 8
            assert 6 < min_h  # 6h loitering would NOT trigger

    def test_no_corridor_uses_default(self):
        """No corridor match uses default thresholds (4h)."""
        from app.modules.loitering_detector import _get_min_hours_for_corridor

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_ADAPTIVE_CONFIG,
        ):
            min_h = _get_min_hours_for_corridor(None)
            assert min_h == 4

    def test_export_route_14h_scores_baseline_not_sustained(self):
        """Export route 14h loitering scores 8 (baseline), NOT 20.

        The sustained rule (20 pts) is restricted to sts_zone corridors only.
        """
        from app.modules.loitering_detector import detect_loitering_for_vessel

        vessel = _make_vessel()
        points = _generate_loitering_track(1, hours=14)
        corridor = _make_corridor(corridor_type="export_route")

        db = _setup_db_mock(points, corridors=[corridor])

        with (
            patch(
                "app.modules.loitering_detector._find_corridor_for_position",
                return_value=corridor,
            ),
            patch(
                "app.modules.scoring_config.load_scoring_config",
                return_value=_ADAPTIVE_CONFIG,
            ),
            patch("app.modules.scoring_config._SCORING_CONFIG", _ADAPTIVE_CONFIG),
        ):
            result = detect_loitering_for_vessel(db, vessel)
            assert result >= 1
            # Check the event was added with risk_score_component = 8 (baseline)
            added_event = db.add.call_args[0][0]
            assert added_event.risk_score_component == 8

    def test_sts_zone_14h_scores_sustained(self):
        """STS zone 14h loitering scores 20 (sustained rule preserved)."""
        from app.modules.loitering_detector import detect_loitering_for_vessel

        vessel = _make_vessel()
        points = _generate_loitering_track(1, hours=14)
        corridor = _make_corridor(corridor_type="sts_zone")

        db = _setup_db_mock(points, corridors=[corridor])

        with (
            patch(
                "app.modules.loitering_detector._find_corridor_for_position",
                return_value=corridor,
            ),
            patch(
                "app.modules.scoring_config.load_scoring_config",
                return_value=_ADAPTIVE_CONFIG,
            ),
            patch("app.modules.scoring_config._SCORING_CONFIG", _ADAPTIVE_CONFIG),
        ):
            result = detect_loitering_for_vessel(db, vessel)
            assert result >= 1
            added_event = db.add.call_args[0][0]
            assert added_event.risk_score_component == 20

    def test_disabled_config_uses_global_constant(self):
        """When enabled=false, uses global _MIN_LOITER_HOURS (4h)."""
        from app.modules.loitering_detector import _get_min_hours_for_corridor

        corridor = _make_corridor(corridor_type="sts_zone")

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_DISABLED_CONFIG,
        ):
            min_h = _get_min_hours_for_corridor(corridor)
            assert min_h == 4  # Falls back to _MIN_LOITER_HOURS

    def test_prefilter_allows_2h_runs(self):
        """Pre-filter uses global minimum (2h for sts_zone), letting short runs through."""
        from app.modules.loitering_detector import _get_global_min_hours

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_ADAPTIVE_CONFIG,
        ):
            global_min = _get_global_min_hours()
            assert global_min == 2

    def test_prefilter_disabled_uses_default(self):
        """When adaptive thresholds disabled, pre-filter uses _MIN_LOITER_HOURS."""
        from app.modules.loitering_detector import _get_global_min_hours

        with patch(
            "app.modules.scoring_config.load_scoring_config",
            return_value=_DISABLED_CONFIG,
        ):
            global_min = _get_global_min_hours()
            assert global_min == 4

    def test_sts_zone_3h_event_created(self):
        """Full integration: 3h loitering in STS zone creates an event (below old 4h global)."""
        from app.modules.loitering_detector import detect_loitering_for_vessel

        vessel = _make_vessel()
        points = _generate_loitering_track(1, hours=3)
        corridor = _make_corridor(corridor_type="sts_zone")

        db = _setup_db_mock(points, corridors=[corridor])

        with (
            patch(
                "app.modules.loitering_detector._find_corridor_for_position",
                return_value=corridor,
            ),
            patch(
                "app.modules.scoring_config.load_scoring_config",
                return_value=_ADAPTIVE_CONFIG,
            ),
            patch("app.modules.scoring_config._SCORING_CONFIG", _ADAPTIVE_CONFIG),
        ):
            result = detect_loitering_for_vessel(db, vessel)
            assert result >= 1
            assert db.add.called

    def test_anchorage_6h_event_not_created(self):
        """Full integration: 6h loitering in anchorage_holding does NOT create event (needs 8h)."""
        from app.modules.loitering_detector import detect_loitering_for_vessel

        vessel = _make_vessel()
        points = _generate_loitering_track(1, hours=6)
        corridor = _make_corridor(corridor_type="anchorage_holding")

        db = _setup_db_mock(points, corridors=[corridor])

        with (
            patch(
                "app.modules.loitering_detector._find_corridor_for_position",
                return_value=corridor,
            ),
            patch(
                "app.modules.scoring_config.load_scoring_config",
                return_value=_ADAPTIVE_CONFIG,
            ),
            patch("app.modules.scoring_config._SCORING_CONFIG", _ADAPTIVE_CONFIG),
        ):
            result = detect_loitering_for_vessel(db, vessel)
            assert result == 0
