"""Tests for merge confirm/reject/reverse operations.

Tests the API endpoints and model structures for vessel identity merging:
  - POST /merge-candidates/{id}/confirm
  - POST /merge-candidates/{id}/reject
  - POST /merge-operations/{id}/reverse
  - POST /vessels/merge
  - GET /merge-candidates
  - GET /merge-candidates/{id}

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestConfirmMergeCandidate:
    """POST /api/v1/merge-candidates/{id}/confirm."""

    def test_confirm_404_not_found(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.post("/api/v1/merge-candidates/99999/confirm")
        assert resp.status_code == 404

    def test_confirm_already_resolved_returns_400(self, api_client, mock_db):
        """Candidate that is not PENDING cannot be confirmed."""
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.status = MergeCandidateStatusEnum.REJECTED
        mock_db.query.return_value.get.return_value = candidate

        resp = api_client.post("/api/v1/merge-candidates/1/confirm")
        assert resp.status_code == 400

    def test_confirm_pending_calls_execute_merge(self, api_client, mock_db):
        """Pending candidate triggers execute_merge."""
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.candidate_id = 1
        candidate.vessel_a_id = 10
        candidate.vessel_b_id = 20
        candidate.status = MergeCandidateStatusEnum.PENDING
        mock_db.query.return_value.get.return_value = candidate

        with patch(
            "app.modules.identity_resolver.execute_merge",
            return_value={"success": True, "merge_op_id": 5},
        ):
            resp = api_client.post("/api/v1/merge-candidates/1/confirm")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    def test_confirm_failed_merge_returns_400(self, api_client, mock_db):
        """If execute_merge fails, returns 400."""
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.candidate_id = 1
        candidate.vessel_a_id = 10
        candidate.vessel_b_id = 20
        candidate.status = MergeCandidateStatusEnum.PENDING
        mock_db.query.return_value.get.return_value = candidate

        with patch(
            "app.modules.identity_resolver.execute_merge",
            return_value={"success": False, "error": "Vessel not found"},
        ):
            resp = api_client.post("/api/v1/merge-candidates/1/confirm")
            assert resp.status_code == 400


class TestRejectMergeCandidate:
    """POST /api/v1/merge-candidates/{id}/reject."""

    def test_reject_404_not_found(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.post("/api/v1/merge-candidates/99999/reject")
        assert resp.status_code == 404

    def test_reject_already_resolved_returns_400(self, api_client, mock_db):
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.status = MergeCandidateStatusEnum.AUTO_MERGED
        mock_db.query.return_value.get.return_value = candidate

        resp = api_client.post("/api/v1/merge-candidates/1/reject")
        assert resp.status_code == 400

    def test_reject_pending_succeeds(self, api_client, mock_db):
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.candidate_id = 1
        candidate.status = MergeCandidateStatusEnum.PENDING
        mock_db.query.return_value.get.return_value = candidate

        resp = api_client.post("/api/v1/merge-candidates/1/reject")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["candidate_id"] == 1

    def test_reject_updates_status(self, api_client, mock_db):
        from app.models.base import MergeCandidateStatusEnum

        candidate = MagicMock()
        candidate.candidate_id = 2
        candidate.status = MergeCandidateStatusEnum.PENDING
        mock_db.query.return_value.get.return_value = candidate

        resp = api_client.post("/api/v1/merge-candidates/2/reject")
        assert resp.status_code == 200
        assert candidate.status == MergeCandidateStatusEnum.REJECTED


class TestReverseMergeOperation:
    """POST /api/v1/merge-operations/{id}/reverse."""

    def test_reverse_merge_calls_function(self, api_client, mock_db):
        with patch(
            "app.modules.identity_resolver.reverse_merge",
            return_value={"success": True, "message": "Reversed"},
        ):
            resp = api_client.post("/api/v1/merge-operations/1/reverse")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

    def test_reverse_merge_failed_returns_400(self, api_client, mock_db):
        with patch(
            "app.modules.identity_resolver.reverse_merge",
            return_value={"success": False, "error": "Not found"},
        ):
            resp = api_client.post("/api/v1/merge-operations/99999/reverse")
            assert resp.status_code == 400


class TestManualMerge:
    """POST /api/v1/vessels/merge — manual merge by analyst."""

    def test_manual_merge_success(self, api_client, mock_db):
        with patch(
            "app.modules.identity_resolver.execute_merge",
            return_value={"success": True, "merge_op_id": 10},
        ):
            resp = api_client.post(
                "/api/v1/vessels/merge?vessel_a_id=1&vessel_b_id=2&reason=Same+vessel"
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_manual_merge_failure_returns_400(self, api_client, mock_db):
        with patch(
            "app.modules.identity_resolver.execute_merge",
            return_value={"success": False, "error": "Vessel not found"},
        ):
            resp = api_client.post(
                "/api/v1/vessels/merge?vessel_a_id=1&vessel_b_id=99999"
            )
            assert resp.status_code == 400


class TestListMergeCandidates:
    """GET /api/v1/merge-candidates — list with pagination."""

    def test_list_empty(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/merge-candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_list_with_status_filter(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/merge-candidates?status=pending")
        assert resp.status_code == 200


class TestGetMergeCandidate:
    """GET /api/v1/merge-candidates/{id} — detail with vessel info."""

    def test_get_404(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.get("/api/v1/merge-candidates/99999")
        assert resp.status_code == 404


class TestMergeCandidateModel:
    """Verify MergeCandidate model structure."""

    def test_merge_candidate_has_expected_fields(self):
        from app.models.merge_candidate import MergeCandidate

        columns = {c.name for c in MergeCandidate.__table__.columns}
        assert "candidate_id" in columns
        assert "vessel_a_id" in columns
        assert "vessel_b_id" in columns
        assert "confidence_score" in columns
        assert "status" in columns

    def test_merge_operation_has_expected_fields(self):
        from app.models.merge_operation import MergeOperation

        columns = {c.name for c in MergeOperation.__table__.columns}
        assert "merge_op_id" in columns
        assert "canonical_vessel_id" in columns
        assert "absorbed_vessel_id" in columns
        assert "status" in columns
        assert "affected_records_json" in columns


class TestMergeCandidateStatusEnum:
    """Verify status enum values."""

    def test_status_enum_values(self):
        from app.models.base import MergeCandidateStatusEnum

        values = [e.value for e in MergeCandidateStatusEnum]
        assert "pending" in values
        assert "auto_merged" in values
        assert "analyst_merged" in values
        assert "rejected" in values
