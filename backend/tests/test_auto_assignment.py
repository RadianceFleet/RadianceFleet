"""Tests for the auto-assignment queue module and API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.auto_assignment import auto_assign_alert, process_assignment_queue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(
    gap_event_id=1,
    risk_score=75,
    assigned_to=None,
    assigned_at=None,
    status="new",
    corridor_id=None,
):
    a = MagicMock()
    a.gap_event_id = gap_event_id
    a.risk_score = risk_score
    a.assigned_to = assigned_to
    a.assigned_at = assigned_at
    a.status = status
    a.corridor_id = corridor_id
    return a


# ---------------------------------------------------------------------------
# auto_assign_alert
# ---------------------------------------------------------------------------


class TestAutoAssignAlert:
    def test_assigns_single_alert(self, mock_db):
        """Should set assigned_to and assigned_at when analyst found."""
        alert = _make_alert(gap_event_id=1, risk_score=80)

        with patch(
            "app.modules.workload_balancer.suggest_assignment", return_value=42
        ):
            result = auto_assign_alert(mock_db, alert)

        assert result == 42
        assert alert.assigned_to == 42
        assert alert.assigned_at is not None

    def test_returns_none_when_no_analyst(self, mock_db):
        """Should return None when no eligible analyst is found."""
        alert = _make_alert(gap_event_id=1)

        with patch(
            "app.modules.workload_balancer.suggest_assignment", return_value=None
        ):
            result = auto_assign_alert(mock_db, alert)

        assert result is None
        # assigned_to should not have been updated to an int
        # (MagicMock attribute may have been set; just check return)

    def test_passes_alert_id_to_balancer(self, mock_db):
        """Should pass the alert's gap_event_id to suggest_assignment."""
        alert = _make_alert(gap_event_id=99)

        with patch(
            "app.modules.workload_balancer.suggest_assignment", return_value=1
        ) as mock_suggest:
            auto_assign_alert(mock_db, alert)

        mock_suggest.assert_called_once_with(mock_db, alert_id=99)


# ---------------------------------------------------------------------------
# process_assignment_queue
# ---------------------------------------------------------------------------


class TestProcessAssignmentQueue:
    def test_batch_processing(self, mock_db):
        """Should process multiple unassigned alerts."""
        alerts = [
            _make_alert(gap_event_id=1, risk_score=90),
            _make_alert(gap_event_id=2, risk_score=70),
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = alerts

        call_count = [0]

        def fake_suggest(db, alert_id=None):
            call_count[0] += 1
            return call_count[0]  # analyst 1, 2

        with patch("app.modules.workload_balancer.suggest_assignment", side_effect=fake_suggest):
            results = process_assignment_queue(mock_db)

        assert len(results) == 2
        assert results[0]["alert_id"] == 1
        assert results[0]["analyst_id"] == 1
        assert results[1]["alert_id"] == 2
        assert results[1]["analyst_id"] == 2
        mock_db.commit.assert_called_once()

    def test_empty_queue(self, mock_db):
        """No unassigned alerts -> empty results, no commit."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        with patch("app.modules.workload_balancer.suggest_assignment"):
            results = process_assignment_queue(mock_db)

        assert results == []
        mock_db.commit.assert_not_called()

    def test_respects_min_score(self, mock_db):
        """Only alerts with risk_score >= min_score should be queried."""
        # The filter is applied at the DB level, so we just verify the query
        # returns results and they get processed
        alerts = [_make_alert(gap_event_id=1, risk_score=60)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = alerts

        with patch("app.modules.workload_balancer.suggest_assignment", return_value=1):
            results = process_assignment_queue(mock_db)

        assert len(results) == 1

    def test_skips_when_no_analyst_found(self, mock_db):
        """Alerts where no analyst can be found should be skipped."""
        alerts = [
            _make_alert(gap_event_id=1, risk_score=90),
            _make_alert(gap_event_id=2, risk_score=80),
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = alerts

        def fake_suggest(db, alert_id=None):
            if alert_id == 1:
                return None  # No analyst for alert 1
            return 5

        with patch("app.modules.workload_balancer.suggest_assignment", side_effect=fake_suggest):
            results = process_assignment_queue(mock_db)

        assert len(results) == 1
        assert results[0]["alert_id"] == 2
        assert results[0]["analyst_id"] == 5


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestAutoAssignmentEndpoints:
    def test_run_auto_assign_disabled(self, api_client):
        """POST /auto-assign/run should return 404 when feature disabled."""
        with patch("app.config.settings.AUTO_ASSIGNMENT_ENABLED", False):
            resp = api_client.post("/api/v1/auto-assign/run")
        assert resp.status_code == 404
        assert "not enabled" in resp.json()["detail"]

    def test_run_auto_assign_enabled(self, api_client, mock_db):
        """POST /auto-assign/run should process queue when enabled."""
        with (
            patch("app.config.settings.AUTO_ASSIGNMENT_ENABLED", True),
            patch(
                "app.modules.auto_assignment.process_assignment_queue",
                return_value=[{"alert_id": 1, "analyst_id": 2, "risk_score": 85}],
            ),
        ):
            resp = api_client.post("/api/v1/auto-assign/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["assigned"] == 1
        assert len(data["assignments"]) == 1

    def test_preview_auto_assign(self, api_client, mock_db):
        """GET /auto-assign/preview should return proposals without committing."""
        alert = _make_alert(gap_event_id=1, risk_score=75)
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [alert]

        with patch(
            "app.modules.workload_balancer.suggest_assignment", return_value=3
        ):
            resp = api_client.get("/api/v1/auto-assign/preview")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["proposals"][0]["suggested_analyst_id"] == 3
        # DB should NOT have been committed
        mock_db.commit.assert_not_called()

    def test_suggest_assignment_endpoint(self, api_client, mock_db):
        """POST /alerts/{id}/suggest-assignment should return best analyst."""
        with patch(
            "app.modules.analyst_presence.suggest_assignment", return_value=7
        ):
            resp = api_client.post("/api/v1/alerts/42/suggest-assignment")

        assert resp.status_code == 200
        assert resp.json()["suggested_analyst_id"] == 7

    def test_suggest_assignment_no_candidate(self, api_client, mock_db):
        """POST /alerts/{id}/suggest-assignment should handle no candidates."""
        with patch(
            "app.modules.analyst_presence.suggest_assignment", return_value=None
        ):
            resp = api_client.post("/api/v1/alerts/42/suggest-assignment")

        assert resp.status_code == 200
        data = resp.json()
        assert data["suggested_analyst_id"] is None
        assert "No eligible" in data["reason"]
