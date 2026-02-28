"""Tests for port resolver fuzzy matching (G1).

Validates the 3-step resolution strategy:
1. Geo-nearest within 10nm
2. Exact name match
3. Fuzzy name match via rapidfuzz (threshold > 80)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_port(port_id: int, name: str, lat: float = 0.0, lon: float = 0.0):
    """Create a mock Port with geometry WKT."""
    port = MagicMock()
    port.port_id = port_id
    port.name = name
    port.geometry = f"POINT ({lon} {lat})"
    return port


class TestGeoNearestPriority:
    """Step 1 takes priority — geo-nearest within 10nm wins."""

    @patch("app.modules.port_resolver.load_geometry")
    def test_geo_nearest_takes_priority_over_fuzzy(self, mock_load_geom):
        """When a port is within 10nm, it should be returned even if fuzzy match is better."""
        from app.modules.port_resolver import resolve_port

        port_geo = _make_port(1, "SOME_PORT", lat=25.0, lon=56.0)
        port_fuzzy = _make_port(2, "FUJAIRAH", lat=90.0, lon=0.0)

        # Make port_geo geometry return a point very close to query (within 10nm)
        from shapely.geometry import Point

        mock_load_geom.side_effect = lambda g: (
            Point(56.0, 25.0) if "56" in str(g) else Point(0.0, 90.0)
        )

        db = MagicMock()
        db.query.return_value.all.return_value = [port_geo, port_fuzzy]

        result = resolve_port(db, lat=25.0, lon=56.0, port_name="FUJAIRAH")
        assert result.port_id == 1  # geo-nearest wins


class TestExactNameMatch:
    """Step 2 — exact name match works."""

    @patch("app.modules.port_resolver.load_geometry")
    def test_exact_name_match(self, mock_load_geom):
        """Exact name match on step 2 returns the port."""
        from app.modules.port_resolver import resolve_port

        port = _make_port(10, "FUJAIRAH", lat=0.0, lon=0.0)
        # Put geometry far away so geo-nearest won't match
        from shapely.geometry import Point

        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, lat=60.0, lon=20.0, port_name="FUJAIRAH")
        assert result.port_id == 10


class TestFuzzyNameMatch:
    """Step 3 — fuzzy name matching."""

    @patch("app.modules.port_resolver.load_geometry")
    def test_fuzzy_match_slight_misspelling(self, mock_load_geom):
        """Fuzzy matching should match 'FUJAIRA' to 'FUJAIRAH' (score > 80)."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port = _make_port(20, "FUJAIRAH", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, lat=60.0, lon=20.0, port_name="FUJAIRA")
        assert result is not None
        assert result.port_id == 20

    @patch("app.modules.port_resolver.load_geometry")
    def test_fuzzy_match_cyrillic_transliteration(self, mock_load_geom):
        """Cyrillic name should transliterate and fuzzy match to latin equivalent."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port = _make_port(30, "NOVOROSSIYSK", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # "Новороссийск" transliterates to "Novorossiisk" — close to NOVOROSSIYSK
        result = resolve_port(db, lat=60.0, lon=20.0, port_name="Новороссийск")
        assert result is not None
        assert result.port_id == 30

    @patch("app.modules.port_resolver.load_geometry")
    def test_fuzzy_threshold_score_80_does_not_match(self, mock_load_geom):
        """A score of exactly 80 should NOT match (threshold is strictly greater than 80)."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point
        from unittest.mock import patch as inner_patch

        port = _make_port(40, "TESTPORT", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # Mock fuzz.ratio to return exactly 80
        with inner_patch("rapidfuzz.fuzz.ratio", return_value=80):
            result = resolve_port(db, lat=60.0, lon=20.0, port_name="XESTPORT")
        assert result is None

    @patch("app.modules.port_resolver.load_geometry")
    def test_fuzzy_threshold_score_81_matches(self, mock_load_geom):
        """A score of 81 should match (above threshold)."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point
        from unittest.mock import patch as inner_patch

        port = _make_port(41, "TESTPORT", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # Mock fuzz.ratio to return 81
        with inner_patch("rapidfuzz.fuzz.ratio", return_value=81):
            result = resolve_port(db, lat=60.0, lon=20.0, port_name="XESTPORT")
        assert result is not None
        assert result.port_id == 41

    @patch("app.modules.port_resolver.load_geometry")
    def test_no_ports_returns_none(self, mock_load_geom):
        """When no ports exist, resolve_port returns None."""
        from app.modules.port_resolver import resolve_port

        db = MagicMock()
        db.query.return_value.all.return_value = []

        result = resolve_port(db, lat=25.0, lon=56.0, port_name="FUJAIRAH")
        assert result is None

    @patch("app.modules.port_resolver.load_geometry")
    def test_port_name_none_skips_fuzzy(self, mock_load_geom):
        """When port_name is None, fuzzy step is skipped entirely."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port = _make_port(50, "FUJAIRAH", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # Lat/lon is far from the port, no name provided
        result = resolve_port(db, lat=60.0, lon=20.0, port_name=None)
        assert result is None

    @patch("app.modules.port_resolver.load_geometry")
    def test_fuzzy_picks_best_match(self, mock_load_geom):
        """When multiple ports have fuzzy matches, the best score wins."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port_a = _make_port(60, "FUJAIRA PORT", lat=0.0, lon=0.0)
        port_b = _make_port(61, "FUJAIRAH", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port_a, port_b]

        result = resolve_port(db, lat=60.0, lon=20.0, port_name="FUJAIRAH")
        # Exact match on step 2 should catch "FUJAIRAH"
        assert result.port_id == 61

    @patch("app.modules.port_resolver.load_geometry")
    def test_port_with_none_name_skipped_in_fuzzy(self, mock_load_geom):
        """Ports with name=None are skipped in fuzzy matching."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port_no_name = _make_port(70, None, lat=0.0, lon=0.0)
        port_no_name.name = None
        port_named = _make_port(71, "FUJAIRAH", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port_no_name, port_named]

        result = resolve_port(db, lat=60.0, lon=20.0, port_name="FUJAIRA")
        assert result is not None
        assert result.port_id == 71

    @patch("app.modules.port_resolver.load_geometry")
    def test_very_different_name_no_match(self, mock_load_geom):
        """A very different name should not fuzzy match (score well below 80)."""
        from app.modules.port_resolver import resolve_port
        from shapely.geometry import Point

        port = _make_port(80, "SINGAPORE", lat=0.0, lon=0.0)
        mock_load_geom.return_value = Point(0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, lat=60.0, lon=20.0, port_name="FUJAIRAH")
        assert result is None
