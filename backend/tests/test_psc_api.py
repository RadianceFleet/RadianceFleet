"""Tests for PSC detention API endpoints."""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock


def _mock_vessel(vessel_id=1, psc_detentions=None):
    """Create a mock vessel with PSC detention attributes."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = "123456789"
    v.imo = "1234567"
    v.name = "TEST VESSEL"
    v.flag = "PA"
    v.vessel_type = "Crude Oil Tanker"
    v.deadweight = 100000.0
    v.year_built = 2010
    v.ais_class = MagicMock(value="A")
    v.flag_risk_category = MagicMock(value="neutral")
    v.pi_coverage_status = MagicMock(value="unknown")
    v.psc_detained_last_12m = False
    v.psc_major_deficiencies_last_12m = 0
    v.mmsi_first_seen_utc = datetime(2020, 1, 1, tzinfo=timezone.utc)
    v.vessel_laid_up_30d = False
    v.vessel_laid_up_60d = False
    v.vessel_laid_up_in_sts_zone = False
    v.merged_into_vessel_id = None
    v.last_ais_received_utc = datetime(2026, 3, 1, tzinfo=timezone.utc)
    v.callsign = "ABCD"
    v.owner_name = None
    v.ais_source = None
    v.dark_fleet_confidence = None
    v.confidence_evidence_json = None
    v.ais_cargo_type = None
    v.watchlist_stub_score = None
    v.watchlist_stub_breakdown = None
    v.updated_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    v.psc_detentions = psc_detentions or []
    return v


def _mock_detention(psc_detention_id=1, detention_date=None, mou_source="tokyo_mou",
                    deficiency_count=3, ban_type=None):
    """Create a mock PscDetention record that passes PscDetentionRead validation."""
    d = MagicMock()
    d.psc_detention_id = psc_detention_id
    d.vessel_id = 1
    d.detention_date = detention_date or date(2026, 1, 15)
    d.release_date = None
    d.port_name = "Tokyo"
    d.port_country = "JP"
    d.mou_source = mou_source
    d.data_source = "opensanctions_ftm"
    d.deficiency_count = deficiency_count
    d.major_deficiency_count = 1
    d.detention_reason = "Safety equipment"
    d.ban_type = ban_type
    d.authority_name = "Tokyo MOU"
    d.imo_at_detention = "1234567"
    d.vessel_name_at_detention = "TEST VESSEL"
    d.flag_at_detention = "PA"
    d.raw_entity_id = f"test-{psc_detention_id}"
    d.created_at = datetime(2026, 3, 1)
    return d


def test_psc_detentions_returns_list(api_client, mock_db):
    """GET /vessels/{id}/psc-detentions returns a list of detentions."""
    det1 = _mock_detention(psc_detention_id=1, detention_date=date(2026, 2, 1))
    det2 = _mock_detention(psc_detention_id=2, detention_date=date(2026, 1, 15))

    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [det1, det2]

    response = api_client.get("/api/v1/vessels/1/psc-detentions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["psc_detention_id"] == 1
    assert data[1]["psc_detention_id"] == 2


def test_psc_detentions_empty_list(api_client, mock_db):
    """GET /vessels/{id}/psc-detentions returns empty list when no detentions."""
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    response = api_client.get("/api/v1/vessels/1/psc-detentions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_vessel_detail_includes_psc_detention_count(api_client, mock_db):
    """Vessel detail includes psc_detention_count field."""
    det = _mock_detention()
    vessel = _mock_vessel(psc_detentions=[det])

    # Mock the vessel query to return our vessel
    mock_db.query.return_value.filter.return_value.first.return_value = vessel
    mock_db.query.return_value.filter.return_value.all.return_value = []
    mock_db.query.return_value.filter.return_value.count.return_value = 0

    response = api_client.get("/api/v1/vessels/1")
    assert response.status_code == 200
    data = response.json()
    assert "psc_detention_count" in data
    assert data["psc_detention_count"] == 1
    assert "psc_latest_detention_date" in data


def test_vessel_detail_psc_detentions_capped_at_10(api_client, mock_db):
    """Vessel detail psc_detentions list is capped at 10 entries."""
    detentions = [
        _mock_detention(psc_detention_id=i, detention_date=date(2026, 1, i + 1))
        for i in range(15)
    ]
    vessel = _mock_vessel(psc_detentions=detentions)

    mock_db.query.return_value.filter.return_value.first.return_value = vessel
    mock_db.query.return_value.filter.return_value.all.return_value = []
    mock_db.query.return_value.filter.return_value.count.return_value = 0

    response = api_client.get("/api/v1/vessels/1")
    assert response.status_code == 200
    data = response.json()
    assert "psc_detentions" in data
    assert len(data["psc_detentions"]) == 10
    assert data["psc_detention_count"] == 15
