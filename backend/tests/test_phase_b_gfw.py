"""Tests for Phase B7 — GFW encounter + port visit import, port resolver.

Covers:
  - Port resolution (geo match, name match, no match)
  - GFW encounter import (creation, dedup)
  - GFW port visit import (creation, nullable port_id)
  - STSDetectionTypeEnum.GFW_ENCOUNTER existence
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_vessel(vessel_id, mmsi, merged_into=None):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.merged_into_vessel_id = merged_into
    return v


def _make_port(port_id, name, lat, lon, country="GR"):
    """Create a mock Port with a POINT WKT geometry."""
    p = MagicMock()
    p.port_id = port_id
    p.name = name
    p.country = country
    p.geometry = f"POINT({lon} {lat})"
    return p


def _make_gfw_encounter_event(
    start_iso, end_iso, lat=36.8, lon=22.5, partner_ssvid="211234567",
):
    return {
        "event_id": f"enc-{start_iso}",
        "type": "encounter",
        "start": start_iso,
        "end": end_iso,
        "lat": lat,
        "lon": lon,
        "vessel_id": "gfw-123",
        "regions": {},
        "distances": {},
        "encounter": {
            "vessel": {"ssvid": partner_ssvid},
        },
    }


def _make_gfw_port_visit_event(
    start_iso, end_iso=None, lat=59.9, lon=30.3, port_name="Saint Petersburg",
):
    return {
        "event_id": f"pv-{start_iso}",
        "type": "port_visit",
        "start": start_iso,
        "end": end_iso,
        "lat": lat,
        "lon": lon,
        "vessel_id": "gfw-456",
        "regions": {},
        "distances": {},
        "port_visit": {"name": port_name},
    }


# ── Port resolver tests ─────────────────────────────────────────────────────


class TestResolvePortGeoMatch:
    def test_resolve_port_geo_match(self):
        """Port within 10nm of given coordinates should be found."""
        from app.modules.port_resolver import resolve_port

        port = _make_port(1, "Piraeus", 37.9475, 23.6375)

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # Query a point ~1nm from Piraeus
        result = resolve_port(db, 37.95, 23.64)
        assert result is not None
        assert result.port_id == 1

    def test_resolve_port_geo_nearest(self):
        """When multiple ports are within radius, nearest wins."""
        from app.modules.port_resolver import resolve_port

        port_far = _make_port(1, "Port A", 37.0, 23.0)
        port_near = _make_port(2, "Port B", 37.95, 23.64)

        db = MagicMock()
        db.query.return_value.all.return_value = [port_far, port_near]

        result = resolve_port(db, 37.95, 23.64)
        assert result is not None
        assert result.port_id == 2


class TestResolvePortNameMatch:
    def test_resolve_port_name_match(self):
        """Port matched by name when geo match fails (port too far)."""
        from app.modules.port_resolver import resolve_port

        # Port is at 0,0 (far from query point) but name matches
        port = _make_port(1, "Novorossiysk", 0.0, 0.0, country="RU")

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, 44.72, 37.77, port_name="NOVOROSSIYSK")
        assert result is not None
        assert result.port_id == 1

    def test_resolve_port_name_case_insensitive(self):
        """Name match is case-insensitive."""
        from app.modules.port_resolver import resolve_port

        port = _make_port(1, "Fujairah", 0.0, 0.0, country="AE")

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, 25.1, 56.3, port_name="fujairah")
        assert result is not None
        assert result.port_id == 1


class TestResolvePortNoMatch:
    def test_resolve_port_no_match(self):
        """None returned when no port within radius and no name match."""
        from app.modules.port_resolver import resolve_port

        # Port is far away and name does not match
        port = _make_port(1, "Rotterdam", 51.9, 4.5, country="NL")

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, -33.9, 18.4, port_name="Cape Town")
        assert result is None

    def test_resolve_port_empty_db(self):
        """None returned when no ports exist in DB."""
        from app.modules.port_resolver import resolve_port

        db = MagicMock()
        db.query.return_value.all.return_value = []

        result = resolve_port(db, 37.0, 23.0)
        assert result is None

    def test_resolve_port_no_geometry(self):
        """Port with None geometry is skipped for geo matching."""
        from app.modules.port_resolver import resolve_port

        port = MagicMock()
        port.port_id = 1
        port.name = "Ghost Port"
        port.geometry = None

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        result = resolve_port(db, 37.0, 23.0, port_name="Other")
        assert result is None


# ── GFW encounter import tests ──────────────────────────────────────────────


class TestImportGFWEncounters:
    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_encounters(self, mock_sleep, mock_search, mock_events):
        """Encounter event creates StsTransferEvent with GFW_ENCOUNTER type."""
        from app.modules.gfw_client import import_gfw_encounters

        mock_search.return_value = [{"gfw_id": "gfw-123", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_encounter_event(
                "2025-12-01T10:00:00Z",
                "2025-12-01T14:00:00Z",
                partner_ssvid="211234567",
            ),
        ]

        vessel1 = _make_vessel(1, "636017000")
        partner = _make_vessel(2, "211234567")

        db = MagicMock()
        # Vessels query
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel1]
        # Partner lookup + dedup: alternate return values
        first_calls = [partner, None]  # partner found, then no existing STS
        db.query.return_value.filter.return_value.first.side_effect = first_calls

        result = import_gfw_encounters(db, token="test")
        assert result["created"] == 1
        assert result["errors"] == 0
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_encounters_dedup(self, mock_sleep, mock_search, mock_events):
        """Duplicate encounter events are skipped."""
        from app.modules.gfw_client import import_gfw_encounters

        mock_search.return_value = [{"gfw_id": "gfw-123", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_encounter_event(
                "2025-12-01T10:00:00Z",
                "2025-12-01T14:00:00Z",
            ),
        ]

        vessel1 = _make_vessel(1, "636017000")
        partner = _make_vessel(2, "211234567")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel1]
        # partner found, then existing STS found (dedup hit)
        existing_sts = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [partner, existing_sts]

        result = import_gfw_encounters(db, token="test")
        assert result["created"] == 0
        db.add.assert_not_called()

    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_encounters_no_partner(self, mock_sleep, mock_search, mock_events):
        """Encounter without matching partner vessel is skipped."""
        from app.modules.gfw_client import import_gfw_encounters

        mock_search.return_value = [{"gfw_id": "gfw-123", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_encounter_event(
                "2025-12-01T10:00:00Z",
                "2025-12-01T14:00:00Z",
                partner_ssvid="999999999",
            ),
        ]

        vessel1 = _make_vessel(1, "636017000")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel1]
        # Partner not found
        db.query.return_value.filter.return_value.first.return_value = None

        result = import_gfw_encounters(db, token="test")
        assert result["created"] == 0
        db.add.assert_not_called()

    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_encounters_vessel_id_ordering(self, mock_sleep, mock_search, mock_events):
        """vessel_1_id should always be < vessel_2_id."""
        from app.modules.gfw_client import import_gfw_encounters

        mock_search.return_value = [{"gfw_id": "gfw-123", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_encounter_event(
                "2025-12-01T10:00:00Z",
                "2025-12-01T14:00:00Z",
            ),
        ]

        # vessel_id=10 encounters partner vessel_id=5
        vessel1 = _make_vessel(10, "636017000")
        partner = _make_vessel(5, "211234567")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel1]
        db.query.return_value.filter.return_value.first.side_effect = [partner, None]

        result = import_gfw_encounters(db, token="test")
        assert result["created"] == 1

        # Check the StsTransferEvent was created with correct ordering
        added_obj = db.add.call_args[0][0]
        assert added_obj.vessel_1_id == 5
        assert added_obj.vessel_2_id == 10


# ── GFW port visit import tests ─────────────────────────────────────────────


class TestImportGFWPortVisits:
    @patch("app.modules.port_resolver.resolve_port")
    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_port_visits(self, mock_sleep, mock_search, mock_events, mock_resolve):
        """Port visit event creates PortCall with resolved port."""
        from app.modules.gfw_client import import_gfw_port_visits

        mock_search.return_value = [{"gfw_id": "gfw-456", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_port_visit_event(
                "2025-12-01T10:00:00Z",
                "2025-12-03T08:00:00Z",
            ),
        ]

        port = _make_port(1, "Saint Petersburg", 59.9, 30.3, "RU")
        mock_resolve.return_value = port

        vessel = _make_vessel(1, "636017000")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        # Dedup: no existing port call
        db.query.return_value.filter.return_value.first.return_value = None

        result = import_gfw_port_visits(db, token="test")
        assert result["created"] == 1
        assert result["errors"] == 0
        db.add.assert_called_once()

        # Check PortCall attributes
        added_obj = db.add.call_args[0][0]
        assert added_obj.vessel_id == 1
        assert added_obj.port_id == 1
        assert added_obj.source == "gfw"
        assert added_obj.raw_port_name == "Saint Petersburg"

    @patch("app.modules.port_resolver.resolve_port")
    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_gfw_port_visits_dedup(self, mock_sleep, mock_search, mock_events, mock_resolve):
        """Duplicate port visit events are skipped."""
        from app.modules.gfw_client import import_gfw_port_visits

        mock_search.return_value = [{"gfw_id": "gfw-456", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_port_visit_event("2025-12-01T10:00:00Z", "2025-12-03T08:00:00Z"),
        ]

        mock_resolve.return_value = _make_port(1, "Saint Petersburg", 59.9, 30.3, "RU")

        vessel = _make_vessel(1, "636017000")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        # Dedup: existing port call found
        db.query.return_value.filter.return_value.first.return_value = MagicMock()

        result = import_gfw_port_visits(db, token="test")
        assert result["created"] == 0
        db.add.assert_not_called()


class TestPortCallNullablePortId:
    @patch("app.modules.port_resolver.resolve_port")
    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_port_call_nullable_port_id(self, mock_sleep, mock_search, mock_events, mock_resolve):
        """PortCall can be created with port_id=None when port not resolved."""
        from app.modules.gfw_client import import_gfw_port_visits

        mock_search.return_value = [{"gfw_id": "gfw-456", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_port_visit_event(
                "2025-12-01T10:00:00Z",
                "2025-12-03T08:00:00Z",
                port_name="Unknown Anchorage",
            ),
        ]
        mock_resolve.return_value = None  # Port not resolved

        vessel = _make_vessel(1, "636017000")

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        db.query.return_value.filter.return_value.first.return_value = None

        result = import_gfw_port_visits(db, token="test")
        assert result["created"] == 1

        added_obj = db.add.call_args[0][0]
        assert added_obj.port_id is None
        assert added_obj.raw_port_name == "Unknown Anchorage"
        assert added_obj.source == "gfw"


# ── Enum test ────────────────────────────────────────────────────────────────


class TestGFWEncounterEnum:
    def test_gfw_encounter_enum_exists(self):
        """STSDetectionTypeEnum.GFW_ENCOUNTER is a valid enum member."""
        from app.models.base import STSDetectionTypeEnum

        assert hasattr(STSDetectionTypeEnum, "GFW_ENCOUNTER")
        assert STSDetectionTypeEnum.GFW_ENCOUNTER.value == "gfw_encounter"

    def test_gfw_encounter_in_enum_members(self):
        """GFW_ENCOUNTER is among the enum members."""
        from app.models.base import STSDetectionTypeEnum

        values = [e.value for e in STSDetectionTypeEnum]
        assert "gfw_encounter" in values


# ── Port resolver edge cases ────────────────────────────────────────────────


class TestPortResolverEdgeCases:
    def test_resolve_port_geo_wins_over_name(self):
        """Geo match takes precedence over name match."""
        from app.modules.port_resolver import resolve_port

        # Port A: close geographically, different name
        port_a = _make_port(1, "Piraeus", 37.9475, 23.6375)
        # Port B: far away, matching name
        port_b = _make_port(2, "Target Port", 0.0, 0.0)

        db = MagicMock()
        db.query.return_value.all.return_value = [port_a, port_b]

        result = resolve_port(db, 37.95, 23.64, port_name="Target Port")
        assert result is not None
        assert result.port_id == 1  # Geo match wins

    def test_resolve_port_name_fallback(self):
        """Name match is used as fallback when geo match fails."""
        from app.modules.port_resolver import resolve_port

        # Port far from query point but name matches
        port = _make_port(1, "Murmansk", 68.97, 33.08, "RU")

        db = MagicMock()
        db.query.return_value.all.return_value = [port]

        # Query point is far from Murmansk coordinates
        result = resolve_port(db, 0.0, 0.0, port_name="  Murmansk  ")
        assert result is not None
        assert result.port_id == 1
