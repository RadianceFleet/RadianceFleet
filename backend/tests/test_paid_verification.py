"""Tests for paid verification models and vessel owner endpoints.

Since paid verification endpoints (POST /vessels/{id}/verify, GET /verification/budget)
are not yet implemented in routes.py, these tests verify:
  1. The VesselOwner model structure
  2. The vessel detail endpoint including owner data
  3. The model-level verification fields

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# VesselOwner Model Structure
# ---------------------------------------------------------------------------

class TestVesselOwnerModel:
    """Verify VesselOwner model has the expected columns."""

    def test_vessel_owner_has_required_fields(self):
        from app.models.vessel_owner import VesselOwner

        columns = {c.name for c in VesselOwner.__table__.columns}
        assert "owner_id" in columns
        assert "vessel_id" in columns
        assert "owner_name" in columns
        assert "country" in columns
        assert "is_sanctioned" in columns

    def test_vessel_owner_table_name(self):
        from app.models.vessel_owner import VesselOwner

        assert VesselOwner.__tablename__ == "vessel_owners"

    def test_vessel_owner_vessel_id_is_foreign_key(self):
        from app.models.vessel_owner import VesselOwner

        col = VesselOwner.__table__.columns["vessel_id"]
        fk = list(col.foreign_keys)
        assert len(fk) > 0
        # Foreign key should reference vessels.vessel_id
        assert any("vessels.vessel_id" in str(f) for f in fk)


# ---------------------------------------------------------------------------
# Vessel Detail — Owner Context
# ---------------------------------------------------------------------------

class TestVesselDetailOwnerContext:
    """Vessel detail includes owner-adjacent data (flag, type)."""

    def _mock_vessel(self, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "VERIFIED TANKER"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 80000.0
        vessel.year_built = 2010
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high_risk")
        vessel.pi_coverage_status = MagicMock(value="unknown")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = None
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] <= 3:
                result.filter.return_value.count.return_value = 0
            else:
                result.filter.return_value.all.return_value = []
            return result

        mock_db.query.side_effect = query_side_effect
        return vessel

    def test_vessel_detail_includes_flag_risk(self, api_client, mock_db):
        """Vessel detail returns flag_risk_category for verification context."""
        self._mock_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "flag_risk_category" in data

    def test_vessel_detail_includes_pi_status(self, api_client, mock_db):
        """Vessel detail returns pi_coverage_status for insurance verification."""
        self._mock_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "pi_coverage_status" in data

    def test_vessel_detail_includes_psc_detention(self, api_client, mock_db):
        """Vessel detail returns PSC detention flag for compliance context."""
        self._mock_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "psc_detained_last_12m" in data


# ---------------------------------------------------------------------------
# PATCH Vessel Owner (if endpoint exists)
# ---------------------------------------------------------------------------

class TestVesselOwnerUpdate:
    """Verify VesselOwner model can be constructed with expected fields."""

    def test_create_vessel_owner_instance(self):
        """VesselOwner can be instantiated with required fields."""
        from app.models.vessel_owner import VesselOwner

        owner = VesselOwner(
            vessel_id=1,
            owner_name="Test Corp",
            country="PA",
            is_sanctioned=False,
        )
        assert owner.owner_name == "Test Corp"
        assert owner.country == "PA"
        assert owner.is_sanctioned is False

    def test_vessel_owner_sanctioned_flag(self):
        """is_sanctioned column has a default of False at the schema level."""
        from app.models.vessel_owner import VesselOwner

        col = VesselOwner.__table__.columns["is_sanctioned"]
        # The column definition has default=False
        assert col.default is not None
        assert col.default.arg is False


# ---------------------------------------------------------------------------
# Verification Budget / Tracking Model
# ---------------------------------------------------------------------------

class TestVerificationModels:
    """Verify that the models needed for paid verification exist."""

    def test_vessel_model_has_owner_name(self):
        """Vessel model includes owner_name for basic ownership tracking."""
        from app.models.vessel import Vessel

        columns = {c.name for c in Vessel.__table__.columns}
        assert "owner_name" in columns

    def test_vessel_owner_indexed_on_vessel_id(self):
        """VesselOwner.vessel_id should be indexed for lookup performance."""
        from app.models.vessel_owner import VesselOwner

        col = VesselOwner.__table__.columns["vessel_id"]
        assert col.index is True or any(
            idx for idx in VesselOwner.__table__.indexes
            if "vessel_id" in [c.name for c in idx.columns]
        )
