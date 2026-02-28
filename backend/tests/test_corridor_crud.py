"""Tests for corridor CRUD endpoints.

Verifies create, update, delete, list, and get for corridors.
Tests validation, 404 handling, and linked-gap deletion prevention.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock


class TestCreateCorridor:
    """POST /api/v1/corridors creates a corridor."""

    def test_create_corridor_ok(self, api_client, mock_db):
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()

        def set_id(obj):
            obj.corridor_id = 42

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/corridors",
            json={
                "name": "Test Corridor",
                "corridor_type": "export_route",
                "risk_weight": 1.5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "corridor_id" in data

    def test_create_corridor_missing_name_returns_400(self, api_client, mock_db):
        """Missing required name field returns 422 (Pydantic validation)."""
        resp = api_client.post(
            "/api/v1/corridors",
            json={"corridor_type": "export_route"},
        )
        assert resp.status_code == 422

    def test_create_corridor_invalid_type_returns_400(self, api_client, mock_db):
        """Invalid corridor_type returns 400."""
        resp = api_client.post(
            "/api/v1/corridors",
            json={"name": "Test", "corridor_type": "nonexistent_type"},
        )
        assert resp.status_code == 400

    def test_create_corridor_with_all_fields(self, api_client, mock_db):
        """Create corridor with all optional fields set."""
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()

        def set_id(obj):
            obj.corridor_id = 43

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/corridors",
            json={
                "name": "Full Corridor",
                "corridor_type": "sts_zone",
                "risk_weight": 2.0,
                "description": "A test STS zone",
                "is_jamming_zone": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    def test_create_corridor_default_type(self, api_client, mock_db):
        """Create corridor without corridor_type uses default 'import_route'."""
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()

        def set_id(obj):
            obj.corridor_id = 44

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/corridors",
            json={"name": "Default Type Corridor"},
        )
        assert resp.status_code == 200


class TestUpdateCorridor:
    """PATCH /api/v1/corridors/{id} updates corridor fields."""

    def test_update_corridor_name(self, api_client, mock_db):
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Original"
        corridor.corridor_type = MagicMock(value="export_route")
        corridor.risk_weight = 1.0
        corridor.is_jamming_zone = False
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        resp = api_client.patch(
            "/api/v1/corridors/1",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert corridor.name == "Updated Name"

    def test_update_corridor_risk_weight(self, api_client, mock_db):
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Test"
        corridor.corridor_type = MagicMock(value="export_route")
        corridor.risk_weight = 1.0
        corridor.is_jamming_zone = False
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        resp = api_client.patch(
            "/api/v1/corridors/1",
            json={"risk_weight": 2.5},
        )
        assert resp.status_code == 200
        assert corridor.risk_weight == 2.5

    def test_update_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.patch("/api/v1/corridors/99999", json={"name": "Test"})
        assert resp.status_code == 404

    def test_update_corridor_invalid_type_returns_400(self, api_client, mock_db):
        corridor = MagicMock()
        corridor.corridor_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        resp = api_client.patch(
            "/api/v1/corridors/1",
            json={"corridor_type": "invalid_type"},
        )
        assert resp.status_code == 400


class TestDeleteCorridor:
    """DELETE /api/v1/corridors/{id} removes a corridor."""

    def test_delete_corridor_ok(self, api_client, mock_db):
        corridor = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        resp = api_client.delete("/api/v1/corridors/1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.delete("/api/v1/corridors/99999")
        assert resp.status_code == 404

    def test_delete_corridor_409_with_linked_gaps(self, api_client, mock_db):
        """Cannot delete corridor that has gap events referencing it."""
        corridor = MagicMock()
        call_count = [0]

        def filter_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.first.return_value = corridor
            else:
                result.count.return_value = 5
            return result

        mock_db.query.return_value.filter.side_effect = filter_side_effect
        resp = api_client.delete("/api/v1/corridors/1")
        assert resp.status_code == 409
        assert "gap event" in resp.json()["detail"].lower()


class TestGetCorridor:
    """GET /api/v1/corridors/{id} returns corridor detail."""

    def test_get_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/corridors/99999")
        assert resp.status_code == 404

    def test_get_corridor_detail(self, api_client, mock_db):
        """Corridor detail returns expected fields."""
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Test Corridor"
        corridor.corridor_type = MagicMock(value="export_route")
        corridor.risk_weight = 1.5
        corridor.is_jamming_zone = False
        corridor.description = "A test corridor"

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Corridor lookup
                result.filter.return_value.first.return_value = corridor
            else:
                # Alert count queries
                result.filter.return_value.count.return_value = 0
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/corridors/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["corridor_id"] == 1
        assert data["name"] == "Test Corridor"
        assert "corridor_type" in data
        assert "risk_weight" in data


class TestListCorridors:
    """GET /api/v1/corridors returns paginated corridor list."""

    def test_list_corridors_empty(self, api_client, mock_db):
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/corridors")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 0

    def test_list_corridors_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/corridors?skip=-1")
        assert resp.status_code == 422


class TestCorridorModelEnums:
    """Verify corridor type enum values are valid."""

    def test_corridor_type_enum_values(self):
        from app.models.base import CorridorTypeEnum

        values = [e.value for e in CorridorTypeEnum]
        assert "export_route" in values
        assert "sts_zone" in values
        assert "import_route" in values
        assert "dark_zone" in values
        assert "legitimate_trade_route" in values
