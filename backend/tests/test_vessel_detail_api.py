"""Tests for vessel detail endpoint response shape and content.

Tests:
  - All expected fields present in vessel detail
  - Merged vessel redirect behavior
  - Watchlist entries in detail
  - Gap summary counts
  - Spoofing, loitering, STS aggregates

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta


class TestVesselDetailFields:
    """GET /api/v1/vessels/{id} returns all expected fields."""

    def _mock_full_vessel(self, mock_db):
        """Build fully populated vessel mock."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "FULL VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 80000.0
        vessel.year_built = 2008
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high_risk")
        vessel.pi_coverage_status = MagicMock(value="active")
        vessel.psc_detained_last_12m = True
        vessel.mmsi_first_seen_utc = datetime(2020, 6, 1, tzinfo=timezone.utc)
        vessel.vessel_laid_up_30d = True
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = True
        vessel.merged_into_vessel_id = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] <= 3:
                result.filter.return_value.count.return_value = 3
            else:
                result.filter.return_value.all.return_value = []
            return result

        mock_db.query.side_effect = query_side_effect
        return vessel

    def test_identity_fields(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["mmsi"] == "123456789"
        assert data["imo"] == "IMO1234567"
        assert data["name"] == "FULL VESSEL"
        assert data["flag"] == "PA"

    def test_vessel_characteristics(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        data = resp.json()
        assert data["vessel_type"] == "Crude Oil Tanker"
        assert data["deadweight"] == 80000.0
        assert data["year_built"] == 2008

    def test_risk_classification_fields(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        data = resp.json()
        assert "ais_class" in data
        assert "flag_risk_category" in data
        assert "pi_coverage_status" in data
        assert "psc_detained_last_12m" in data

    def test_laid_up_flags(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        data = resp.json()
        assert "vessel_laid_up_30d" in data
        assert "vessel_laid_up_60d" in data
        assert "vessel_laid_up_in_sts_zone" in data

    def test_gap_counts(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        data = resp.json()
        assert "total_gaps_7d" in data
        assert "total_gaps_30d" in data
        assert isinstance(data["total_gaps_7d"], int)

    def test_aggregated_lists(self, api_client, mock_db):
        self._mock_full_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        data = resp.json()
        assert "watchlist_entries" in data
        assert "spoofing_anomalies_30d" in data
        assert "loitering_events_30d" in data
        assert "sts_events_60d" in data
        assert isinstance(data["watchlist_entries"], list)
        assert isinstance(data["spoofing_anomalies_30d"], list)


class TestMergedVesselRedirect:
    """When vessel is absorbed, detail returns redirect info."""

    def test_merged_vessel_returns_redirect(self, api_client, mock_db):
        """Absorbed vessel returns full detail with merged_into_vessel_id set."""
        vessel = MagicMock()
        vessel.vessel_id = 20
        vessel.mmsi = "111111111"
        vessel.imo = "IMO9999999"
        vessel.name = "MERGED VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 50000.0
        vessel.year_built = 2005
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high_risk")
        vessel.pi_coverage_status = MagicMock(value="unknown")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = None
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = 10  # absorbed into vessel 10

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            else:
                result.filter.return_value.first.return_value = None
                canonical = MagicMock()
                canonical.vessel_id = 10
                canonical.merged_into_vessel_id = None
                result.get = MagicMock(return_value=canonical)
            return result

        mock_db.query.side_effect = query_side_effect

        from unittest.mock import patch
        with patch("app.modules.identity_resolver.resolve_canonical", return_value=10):
            resp = api_client.get("/api/v1/vessels/20")
            assert resp.status_code == 200
            data = resp.json()
            assert data["merged_into_vessel_id"] is not None
            assert data["merged_into_vessel_id"] == 10
            assert data["vessel_id"] == 20


class TestVesselSearch:
    """GET /api/v1/vessels — search/list endpoint."""

    def test_search_returns_paginated_response(self, api_client, mock_db):
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/vessels")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_search_with_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/vessels?skip=-1")
        assert resp.status_code == 422


class TestVesselTimeline:
    """GET /api/v1/vessels/{id}/timeline — event timeline."""

    def test_timeline_404_for_unknown_vessel(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.get("/api/v1/vessels/99999/timeline")
        assert resp.status_code == 404

    def test_timeline_returns_events(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.get.return_value = vessel

        with MagicMock() as timeline:
            from unittest.mock import patch
            with patch("app.modules.identity_resolver.get_vessel_timeline", return_value=[]):
                resp = api_client.get("/api/v1/vessels/1/timeline")
                assert resp.status_code == 200
                data = resp.json()
                assert "vessel_id" in data
                assert "events" in data
                assert "count" in data


class TestVesselAliases:
    """GET /api/v1/vessels/{id}/aliases — MMSI aliases."""

    def test_aliases_404_for_unknown_vessel(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.get("/api/v1/vessels/99999/aliases")
        assert resp.status_code == 404

    def test_aliases_returns_list(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.get.return_value = vessel

        from unittest.mock import patch
        with patch("app.modules.identity_resolver.get_vessel_aliases", return_value=["123456789", "987654321"]):
            resp = api_client.get("/api/v1/vessels/1/aliases")
            assert resp.status_code == 200
            data = resp.json()
            assert "vessel_id" in data
            assert "aliases" in data
            assert isinstance(data["aliases"], list)
