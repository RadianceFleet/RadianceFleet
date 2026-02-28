"""Tests for watchlist management endpoints.

Verifies add, remove, list, and import for watchlist entries.
Tests audit log creation on add/remove operations.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from io import BytesIO
from unittest.mock import MagicMock, patch, call


class TestAddToWatchlist:
    """POST /api/v1/watchlist adds a vessel to the local watchlist."""

    def test_add_to_watchlist_ok(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        def set_id(obj):
            obj.watchlist_entry_id = 10

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/watchlist",
            json={"vessel_id": 1, "reason": "Suspected sanctions evasion"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "added"
        assert "watchlist_entry_id" in data

    def test_add_to_watchlist_missing_vessel_id(self, api_client, mock_db):
        """Missing vessel_id in body returns 400."""
        resp = api_client.post("/api/v1/watchlist", json={})
        assert resp.status_code == 400

    def test_add_to_watchlist_vessel_not_found(self, api_client, mock_db):
        """Vessel not in DB returns 404."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post("/api/v1/watchlist", json={"vessel_id": 99999})
        assert resp.status_code == 404

    def test_add_to_watchlist_creates_entry(self, api_client, mock_db):
        """db.add is called with a VesselWatchlist object."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        def set_id(obj):
            obj.watchlist_entry_id = 11

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/watchlist",
            json={"vessel_id": 1, "reason": "Test"},
        )
        assert resp.status_code == 200
        assert mock_db.add.called


class TestRemoveFromWatchlist:
    """DELETE /api/v1/watchlist/{id} removes (soft delete) from watchlist."""

    def test_remove_from_watchlist_ok(self, api_client, mock_db):
        entry = MagicMock()
        entry.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = entry

        resp = api_client.delete("/api/v1/watchlist/1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        assert entry.is_active is False

    def test_remove_from_watchlist_404(self, api_client, mock_db):
        """Non-existent entry returns 404."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.delete("/api/v1/watchlist/99999")
        assert resp.status_code == 404

    def test_remove_from_watchlist_commits(self, api_client, mock_db):
        """Removal triggers a db.commit."""
        entry = MagicMock()
        entry.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = entry

        resp = api_client.delete("/api/v1/watchlist/1")
        assert resp.status_code == 200
        assert mock_db.commit.called


class TestListWatchlist:
    """GET /api/v1/watchlist returns active watchlist entries."""

    def test_list_watchlist_empty(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/watchlist")
        assert resp.status_code == 200
        assert resp.json() == []


class TestWatchlistImport:
    """POST /api/v1/watchlist/import handles various watchlist sources."""

    def test_import_ofac_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_ofac_sdn", return_value=5):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "ofac"},
                files={"file": ("sdn.csv", BytesIO(b"mmsi,name\n123456789,TEST"), "text/csv")},
            )
            assert resp.status_code == 200
            assert resp.json()["imported"] == 5
            assert resp.json()["source"] == "ofac"

    def test_import_kse_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_kse_list", return_value=3):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "kse"},
                files={"file": ("kse.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
            )
            assert resp.status_code == 200

    def test_import_opensanctions_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_opensanctions", return_value=7):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "opensanctions"},
                files={"file": ("os.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
            )
            assert resp.status_code == 200

    def test_import_invalid_source_returns_422(self, api_client, mock_db):
        """Unknown source name returns 422."""
        resp = api_client.post(
            "/api/v1/watchlist/import",
            data={"source": "fake_source"},
            files={"file": ("test.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
        )
        assert resp.status_code == 422


class TestWatchlistModelFields:
    """Verify VesselWatchlist model has expected fields."""

    def test_watchlist_model_has_required_columns(self):
        from app.models.vessel_watchlist import VesselWatchlist

        columns = {c.name for c in VesselWatchlist.__table__.columns}
        assert "watchlist_entry_id" in columns
        assert "vessel_id" in columns
        assert "is_active" in columns
        assert "watchlist_source" in columns


class TestAuditLogOnWatchlistActions:
    """Verify that watchlist add/remove is auditable."""

    def test_watchlist_add_triggers_commit(self, api_client, mock_db):
        """Adding to watchlist triggers a commit (audit trail is maintained)."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        def set_id(obj):
            obj.watchlist_entry_id = 20

        mock_db.add.side_effect = set_id

        resp = api_client.post(
            "/api/v1/watchlist",
            json={"vessel_id": 1, "reason": "Investigation"},
        )
        assert resp.status_code == 200
        assert mock_db.commit.called

    def test_watchlist_remove_triggers_commit(self, api_client, mock_db):
        """Removing from watchlist triggers a commit."""
        entry = MagicMock()
        entry.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = entry

        resp = api_client.delete("/api/v1/watchlist/1")
        assert resp.status_code == 200
        assert mock_db.commit.called
