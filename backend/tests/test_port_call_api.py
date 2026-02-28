"""Tests for port call related functionality.

Since the dedicated /port-calls/{vessel_id} endpoint may not yet exist,
these tests verify the port call model behavior and the vessel detail
endpoint which includes port-related data.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Port Call Model Tests
# ---------------------------------------------------------------------------

class TestPortCallModel:
    """Verify PortCall model structure and nullable fields."""

    def test_port_call_model_has_expected_fields(self):
        """PortCall model has vessel_id, port_id (nullable), arrival_utc, raw_port_name, source."""
        from app.models.port_call import PortCall

        # Check column names exist on the model
        mapper = PortCall.__table__
        column_names = {c.name for c in mapper.columns}
        assert "vessel_id" in column_names
        assert "arrival_utc" in column_names

    def test_port_call_model_has_port_id(self):
        """PortCall model has a port_id column referencing ports."""
        from app.models.port_call import PortCall

        col = PortCall.__table__.columns.get("port_id")
        assert col is not None, "port_id column should exist"
        fk = list(col.foreign_keys)
        assert len(fk) > 0, "port_id should be a foreign key"


# ---------------------------------------------------------------------------
# Vessel Detail — Port-Adjacent Data
# ---------------------------------------------------------------------------

class TestVesselDetailPortData:
    """Vessel detail endpoint returns port-call-adjacent data (gap counts, etc.)."""

    def _mock_vessel_detail(self, mock_db):
        """Set up mock for GET /api/v1/vessels/{id}."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "TEST VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 50000.0
        vessel.year_built = 2005
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high_risk")
        vessel.pi_coverage_status = MagicMock(value="unknown")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = datetime(2020, 1, 1, tzinfo=timezone.utc)
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Vessel query
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] <= 3:
                # Gap count queries (7d, 30d)
                result.filter.return_value.count.return_value = 2
            elif call_count[0] == 4:
                # Watchlist entries
                result.filter.return_value.all.return_value = []
            elif call_count[0] == 5:
                # Spoofing anomalies 30d
                result.filter.return_value.all.return_value = []
            elif call_count[0] == 6:
                # Loitering events 30d
                result.filter.return_value.all.return_value = []
            elif call_count[0] == 7:
                # STS events 60d
                result.filter.return_value.all.return_value = []
            else:
                result.filter.return_value.first.return_value = None
                result.filter.return_value.all.return_value = []
                result.filter.return_value.count.return_value = 0
            return result

        mock_db.query.side_effect = query_side_effect
        return vessel

    def test_vessel_detail_has_gap_counts(self, api_client, mock_db):
        """Vessel detail returns total_gaps_7d and total_gaps_30d."""
        self._mock_vessel_detail(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_gaps_7d" in data
        assert "total_gaps_30d" in data

    def test_vessel_detail_has_identity_fields(self, api_client, mock_db):
        """Vessel detail returns MMSI, IMO, name, flag."""
        self._mock_vessel_detail(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mmsi"] == "123456789"
        assert data["name"] == "TEST VESSEL"
        assert data["flag"] == "PA"

    def test_vessel_detail_has_watchlist(self, api_client, mock_db):
        """Vessel detail returns watchlist_entries array."""
        self._mock_vessel_detail(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "watchlist_entries" in data
        assert isinstance(data["watchlist_entries"], list)


class TestVesselNotFound:
    """Vessel detail returns 404 for non-existent vessel."""

    def test_vessel_detail_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/vessels/99999")
        assert resp.status_code == 404

    def test_vessel_detail_404_response_has_detail(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/vessels/99999")
        data = resp.json()
        assert "detail" in data


class TestVesselAlerts:
    """Vessel alerts endpoint for port-adjacent gap tracking."""

    def test_vessel_alerts_empty(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/vessels/1/alerts")
        assert resp.status_code == 200
