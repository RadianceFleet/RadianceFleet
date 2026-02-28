"""Tests for audit log listing endpoint.

Tests:
  - GET /api/v1/audit-log returns entries
  - Entries have action, entity_type, created_at fields
  - Filtering by action and entity_type
  - Pagination parameters

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestListAuditLogs:
    """GET /api/v1/audit-log returns paginated audit log entries."""

    def test_audit_log_empty(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["logs"] == []

    def test_audit_log_response_shape(self, api_client, mock_db):
        """Response must have 'total' (int) and 'logs' (list)."""
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log")
        data = resp.json()
        assert isinstance(data["total"], int)
        assert isinstance(data["logs"], list)

    def test_audit_log_with_entries(self, api_client, mock_db):
        """Log entries have expected field structure."""
        log_entry = MagicMock()
        log_entry.audit_id = 1
        log_entry.action = "status_change"
        log_entry.entity_type = "alert"
        log_entry.entity_id = 42
        log_entry.details = {"old_status": "new", "new_status": "under_review"}
        log_entry.created_at = datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc)

        mock_db.query.return_value.order_by.return_value.count.return_value = 1
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            log_entry
        ]

        resp = api_client.get("/api/v1/audit-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["logs"]) == 1

        entry = data["logs"][0]
        assert entry["audit_id"] == 1
        assert entry["action"] == "status_change"
        assert entry["entity_type"] == "alert"
        assert entry["entity_id"] == 42
        assert "created_at" in entry

    def test_audit_log_with_action_filter(self, api_client, mock_db):
        """Filter by action parameter works."""
        mock_db.query.return_value.order_by.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log?action=status_change")
        assert resp.status_code == 200

    def test_audit_log_with_entity_type_filter(self, api_client, mock_db):
        """Filter by entity_type parameter works."""
        mock_db.query.return_value.order_by.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log?entity_type=alert")
        assert resp.status_code == 200

    def test_audit_log_pagination(self, api_client, mock_db):
        """skip and limit parameters are accepted."""
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log?skip=10&limit=5")
        assert resp.status_code == 200


class TestAuditLogModel:
    """Verify AuditLog model structure."""

    def test_audit_log_has_expected_fields(self):
        from app.models.audit_log import AuditLog

        columns = {c.name for c in AuditLog.__table__.columns}
        assert "audit_id" in columns
        assert "action" in columns
        assert "entity_type" in columns
        assert "entity_id" in columns
        assert "details" in columns
        assert "created_at" in columns

    def test_audit_log_has_user_agent(self):
        """AuditLog tracks user_agent for accountability."""
        from app.models.audit_log import AuditLog

        columns = {c.name for c in AuditLog.__table__.columns}
        assert "user_agent" in columns

    def test_audit_log_has_ip_address(self):
        """AuditLog tracks ip_address for accountability."""
        from app.models.audit_log import AuditLog

        columns = {c.name for c in AuditLog.__table__.columns}
        assert "ip_address" in columns

    def test_audit_log_table_name(self):
        from app.models.audit_log import AuditLog

        assert AuditLog.__tablename__ == "audit_logs"

    def test_audit_log_action_indexed(self):
        """Action column should be indexed for filtering."""
        from app.models.audit_log import AuditLog

        col = AuditLog.__table__.columns["action"]
        assert col.index is True


class TestAuditLogOnMutations:
    """Verify that mutation endpoints produce audit log entries (via db.add calls)."""

    def test_status_change_calls_commit(self, api_client, mock_db):
        """Changing alert status triggers a commit (which includes audit log)."""
        alert = MagicMock()
        alert.status = "new"
        alert.analyst_notes = ""
        alert.gap_event_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = alert

        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "under_review"},
        )
        assert resp.status_code == 200
        # _audit_log calls db.add with an AuditLog; db.commit persists it
        assert mock_db.add.called
        assert mock_db.commit.called
