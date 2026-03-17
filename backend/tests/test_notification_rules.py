"""Tests for the notification rules engine — matching, throttling, dispatch, and API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.notification_rule import NotificationRule
from app.models.notification_rule_log import NotificationRuleLog
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.models.analyst import Analyst
from app.models.corridor import Corridor


@pytest.fixture
def db():
    """In-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def vessel(db):
    v = Vessel(vessel_id=1, name="SHADOW TANKER", mmsi="123456789", flag="RU", vessel_type="tanker")
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def corridor(db):
    c = Corridor(corridor_id=1, name="Test Corridor", corridor_type="export_route", geometry="LINESTRING(0 0, 1 1)")
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def alert(db, vessel, corridor):
    a = AISGapEvent(
        gap_event_id=1,
        vessel_id=vessel.vessel_id,
        gap_start_utc=datetime(2026, 1, 1, tzinfo=UTC),
        gap_end_utc=datetime(2026, 1, 1, 6, tzinfo=UTC),
        duration_minutes=360,
        corridor_id=corridor.corridor_id,
        risk_score=80,
        status="new",
        risk_breakdown_json={"viirs": {"score": 10}, "corridor": {"score": 20}},
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture
def rule(db):
    r = NotificationRule(
        rule_id=1,
        name="High Risk Alert",
        is_active=True,
        channel="slack",
        destination="#alerts",
        throttle_minutes=30,
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def _enable():
    with patch("app.modules.notification_rules_engine.settings") as s:
        s.NOTIFICATION_RULES_ENABLED = True
        s.NOTIFICATION_RULES_DEFAULT_THROTTLE_MINUTES = 30
        s.SLACK_BOT_TOKEN = "xoxb-test"
        yield s


# ---------------------------------------------------------------------------
# Rule condition matching
# ---------------------------------------------------------------------------


class TestMatchesRule:
    def test_no_conditions_matches_any(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        assert _matches_rule(rule, alert, vessel) is True

    def test_min_score_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 70
        assert _matches_rule(rule, alert, vessel) is True

    def test_min_score_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 90
        assert _matches_rule(rule, alert, vessel) is False

    def test_max_score_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.max_score = 90
        assert _matches_rule(rule, alert, vessel) is True

    def test_max_score_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.max_score = 50
        assert _matches_rule(rule, alert, vessel) is False

    def test_score_range(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 60
        rule.max_score = 90
        assert _matches_rule(rule, alert, vessel) is True

    def test_corridor_ids_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.corridor_ids_json = [1, 2, 3]
        assert _matches_rule(rule, alert, vessel) is True

    def test_corridor_ids_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.corridor_ids_json = [5, 6]
        assert _matches_rule(rule, alert, vessel) is False

    def test_vessel_flags_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.vessel_flags_json = ["RU", "CM"]
        assert _matches_rule(rule, alert, vessel) is True

    def test_vessel_flags_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.vessel_flags_json = ["PA", "LR"]
        assert _matches_rule(rule, alert, vessel) is False

    def test_alert_statuses_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.alert_statuses_json = ["new", "under_review"]
        assert _matches_rule(rule, alert, vessel) is True

    def test_alert_statuses_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.alert_statuses_json = ["dismissed"]
        assert _matches_rule(rule, alert, vessel) is False

    def test_vessel_types_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.vessel_types_json = ["tanker", "bulk"]
        assert _matches_rule(rule, alert, vessel) is True

    def test_vessel_types_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.vessel_types_json = ["container"]
        assert _matches_rule(rule, alert, vessel) is False

    def test_scoring_signals_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.scoring_signals_json = ["viirs"]
        assert _matches_rule(rule, alert, vessel) is True

    def test_scoring_signals_no_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.scoring_signals_json = ["nonexistent_signal"]
        assert _matches_rule(rule, alert, vessel) is False

    def test_scoring_signals_no_breakdown(self, db, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        alert = MagicMock()
        alert.risk_breakdown_json = None
        rule.scoring_signals_json = ["viirs"]
        assert _matches_rule(rule, alert, vessel) is False


class TestAndLogic:
    def test_multiple_conditions_all_match(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 70
        rule.vessel_flags_json = ["RU"]
        rule.corridor_ids_json = [1]
        assert _matches_rule(rule, alert, vessel) is True

    def test_multiple_conditions_one_fails(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 70
        rule.vessel_flags_json = ["PA"]  # doesn't match RU
        assert _matches_rule(rule, alert, vessel) is False

    def test_all_conditions_set(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.min_score = 50
        rule.max_score = 100
        rule.corridor_ids_json = [1]
        rule.vessel_flags_json = ["RU"]
        rule.alert_statuses_json = ["new"]
        rule.vessel_types_json = ["tanker"]
        rule.scoring_signals_json = ["viirs"]
        assert _matches_rule(rule, alert, vessel) is True


class TestTimeWindow:
    def test_time_window_within(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        rule.time_window_start = "00:00"
        rule.time_window_end = "23:59"
        assert _matches_rule(rule, alert, vessel) is True

    def test_time_window_overnight(self, db, alert, vessel, rule):
        from app.modules.notification_rules_engine import _matches_rule

        # Overnight window covers all times
        rule.time_window_start = "22:00"
        rule.time_window_end = "06:00"
        with patch("app.modules.notification_rules_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert _matches_rule(rule, alert, vessel) is True

    def test_time_window_only_start_set(self, db, alert, vessel, rule):
        """If only start is set without end, condition is skipped."""
        from app.modules.notification_rules_engine import _matches_rule

        rule.time_window_start = "08:00"
        rule.time_window_end = None
        assert _matches_rule(rule, alert, vessel) is True


# ---------------------------------------------------------------------------
# Throttle logic
# ---------------------------------------------------------------------------


class TestThrottle:
    def test_not_throttled_no_recent_log(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import _is_throttled

        assert _is_throttled(db, rule, alert) is False

    def test_throttled_recent_sent(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import _is_throttled

        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="slack",
            destination="#alerts",
            status="sent",
            sent_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add(log)
        db.flush()
        assert _is_throttled(db, rule, alert) is True

    def test_not_throttled_old_log(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import _is_throttled

        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="slack",
            destination="#alerts",
            status="sent",
            sent_at=datetime.now(UTC) - timedelta(minutes=60),
        )
        db.add(log)
        db.flush()
        assert _is_throttled(db, rule, alert) is False

    def test_throttle_failed_not_counted(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import _is_throttled

        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="slack",
            destination="#alerts",
            status="failed",
            sent_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add(log)
        db.flush()
        assert _is_throttled(db, rule, alert) is False

    def test_custom_throttle_minutes(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import _is_throttled

        rule.throttle_minutes = 5
        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="slack",
            destination="#alerts",
            status="sent",
            sent_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        db.add(log)
        db.flush()
        assert _is_throttled(db, rule, alert) is False


# ---------------------------------------------------------------------------
# Slack message formatting
# ---------------------------------------------------------------------------


class TestSlackFormatting:
    def test_default_block_kit(self, alert, vessel):
        from app.modules.slack_notifier import format_alert_for_slack

        msg = format_alert_for_slack(alert, vessel=vessel)
        assert "SHADOW TANKER" in msg["text"]
        assert msg["blocks"] is not None
        assert len(msg["blocks"]) == 3

    def test_custom_template(self, alert, vessel):
        from app.modules.slack_notifier import format_alert_for_slack

        msg = format_alert_for_slack(alert, vessel=vessel, template="Vessel: {vessel_name} Score: {risk_score}")
        assert "SHADOW TANKER" in msg["text"]
        assert "80" in msg["text"]
        assert msg["blocks"] is None

    def test_no_vessel(self, alert):
        from app.modules.slack_notifier import format_alert_for_slack

        msg = format_alert_for_slack(alert, vessel=None)
        assert "Unknown" in msg["text"]


# ---------------------------------------------------------------------------
# Dispatch (mocked)
# ---------------------------------------------------------------------------


class TestDispatch:
    @patch("app.modules.slack_notifier.send_slack_message")
    def test_slack_dispatch(self, mock_send, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        mock_send.return_value = {"ok": True}
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is True
        mock_send.assert_called_once()

    @patch("app.modules.email_notifier._send_email")
    def test_email_dispatch(self, mock_send, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        rule.channel = "email"
        rule.destination = "analyst@example.com"
        mock_send.return_value = True
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is True
        mock_send.assert_called_once()

    @patch("app.modules.email_notifier._send_email")
    def test_email_dispatch_failure(self, mock_send, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        rule.channel = "email"
        rule.destination = "bad@example.com"
        mock_send.return_value = False
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is False

    @patch("httpx.Client")
    def test_webhook_dispatch(self, mock_client_cls, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        rule.channel = "webhook"
        rule.destination = "https://hooks.example.com/alert"
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is True

    @patch("httpx.Client")
    def test_webhook_dispatch_failure(self, mock_client_cls, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client_cls.return_value = mock_client

        rule.channel = "webhook"
        rule.destination = "https://hooks.example.com/alert"
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is False
        assert "Connection refused" in result["error"]

    def test_unknown_channel(self, rule, alert, vessel):
        from app.modules.notification_rules_engine import dispatch_notification

        rule.channel = "carrier_pigeon"
        result = dispatch_notification(rule, alert, vessel)
        assert result["success"] is False
        assert "Unknown channel" in result["error"]


# ---------------------------------------------------------------------------
# Evaluate rules
# ---------------------------------------------------------------------------


class TestEvaluateRules:
    def test_returns_matching_rules(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import evaluate_rules

        matched = evaluate_rules(db, alert)
        assert len(matched) == 1
        assert matched[0].rule_id == rule.rule_id

    def test_inactive_rules_excluded(self, db, rule, alert, _enable):
        from app.modules.notification_rules_engine import evaluate_rules

        rule.is_active = False
        db.flush()
        matched = evaluate_rules(db, alert)
        assert len(matched) == 0

    def test_disabled_feature_returns_empty(self, db, rule, alert):
        from app.modules.notification_rules_engine import evaluate_rules

        with patch("app.modules.notification_rules_engine.settings") as s:
            s.NOTIFICATION_RULES_ENABLED = False
            matched = evaluate_rules(db, alert)
            assert len(matched) == 0


# ---------------------------------------------------------------------------
# fire_matching_rules (integration)
# ---------------------------------------------------------------------------


class TestFireMatchingRules:
    @patch("app.modules.notification_rules_engine.dispatch_notification")
    def test_fire_and_log(self, mock_dispatch, db, rule, alert, vessel, _enable):
        from app.modules.notification_rules_engine import fire_matching_rules

        mock_dispatch.return_value = {"success": True, "error": None}
        results = fire_matching_rules(db, alert)
        assert len(results) == 1
        assert results[0]["status"] == "sent"

        # Verify log was created
        logs = db.query(NotificationRuleLog).all()
        assert len(logs) == 1
        assert logs[0].status == "sent"

    @patch("app.modules.notification_rules_engine.dispatch_notification")
    def test_throttled_rule_logged(self, mock_dispatch, db, rule, alert, vessel, _enable):
        from app.modules.notification_rules_engine import fire_matching_rules

        # Create a recent sent log to trigger throttle
        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="slack",
            destination="#alerts",
            status="sent",
            sent_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add(log)
        db.flush()

        results = fire_matching_rules(db, alert)
        assert len(results) == 1
        assert results[0]["status"] == "throttled"
        mock_dispatch.assert_not_called()

    @patch("app.modules.notification_rules_engine.dispatch_notification")
    def test_failed_dispatch_logged(self, mock_dispatch, db, rule, alert, vessel, _enable):
        from app.modules.notification_rules_engine import fire_matching_rules

        mock_dispatch.return_value = {"success": False, "error": "timeout"}
        results = fire_matching_rules(db, alert)
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "timeout"

    def test_feature_disabled(self, db, rule, alert, vessel):
        from app.modules.notification_rules_engine import fire_matching_rules

        with patch("app.modules.notification_rules_engine.settings") as s:
            s.NOTIFICATION_RULES_ENABLED = False
            results = fire_matching_rules(db, alert)
            assert results == []


# ---------------------------------------------------------------------------
# API CRUD
# ---------------------------------------------------------------------------


class TestAPI:
    @pytest.fixture
    def client(self):
        """FastAPI test client with auth and DB overridden."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool

        from app.api.routes_notification_rules import router
        from app.auth import require_senior_or_admin
        from app.database import get_db

        # Use StaticPool so all connections share the same in-memory DB
        _engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(_engine)
        from sqlalchemy.orm import sessionmaker as _sm

        _Session = _sm(bind=_engine, expire_on_commit=False)

        app = FastAPI()
        app.include_router(router)

        app.dependency_overrides[require_senior_or_admin] = lambda: {
            "analyst_id": 1,
            "role": "admin",
        }

        def _override_db():
            s = _Session()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[get_db] = _override_db

        yield TestClient(app)
        app.dependency_overrides.clear()
        _engine.dispose()

    @pytest.fixture
    def _enable_config(self):
        with patch("app.config.settings") as s:
            s.NOTIFICATION_RULES_ENABLED = True
            s.NOTIFICATION_RULES_DEFAULT_THROTTLE_MINUTES = 30
            s.SLACK_BOT_TOKEN = None
            yield s

    def test_list_empty(self, client):
        resp = client.get("/admin/notification-rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules"] == []

    def test_create_rule(self, client, _enable_config):
        resp = client.post(
            "/admin/notification-rules",
            json={
                "name": "Test Rule",
                "channel": "slack",
                "destination": "#test",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Rule"
        assert data["channel"] == "slack"

    def test_create_invalid_channel(self, client, _enable_config):
        resp = client.post(
            "/admin/notification-rules",
            json={
                "name": "Bad Rule",
                "channel": "carrier_pigeon",
                "destination": "sky",
            },
        )
        assert resp.status_code == 400

    def test_get_rule(self, client, _enable_config):
        # Create first
        create_resp = client.post(
            "/admin/notification-rules",
            json={"name": "Get Me", "channel": "email", "destination": "a@b.com"},
        )
        rule_id = create_resp.json()["rule_id"]

        resp = client.get(f"/admin/notification-rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Me"

    def test_get_rule_not_found(self, client):
        resp = client.get("/admin/notification-rules/999")
        assert resp.status_code == 404

    def test_update_rule(self, client, _enable_config):
        create_resp = client.post(
            "/admin/notification-rules",
            json={"name": "Original", "channel": "slack", "destination": "#orig"},
        )
        rule_id = create_resp.json()["rule_id"]

        resp = client.put(
            f"/admin/notification-rules/{rule_id}",
            json={"name": "Updated", "min_score": 50},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"
        assert resp.json()["min_score"] == 50

    def test_delete_rule(self, client, _enable_config):
        create_resp = client.post(
            "/admin/notification-rules",
            json={"name": "Delete Me", "channel": "webhook", "destination": "https://example.com"},
        )
        rule_id = create_resp.json()["rule_id"]

        resp = client.delete(f"/admin/notification-rules/{rule_id}")
        assert resp.status_code == 200

        resp2 = client.get(f"/admin/notification-rules/{rule_id}")
        assert resp2.status_code == 404

    def test_test_notification(self, client, _enable_config):
        create_resp = client.post(
            "/admin/notification-rules",
            json={"name": "Test Notif", "channel": "webhook", "destination": "https://example.com"},
        )
        rule_id = create_resp.json()["rule_id"]

        with patch("app.modules.notification_rules_engine.dispatch_notification") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "error": None}
            resp = client.post(f"/admin/notification-rules/{rule_id}/test")
            assert resp.status_code == 200

    def test_get_logs(self, client, _enable_config):
        create_resp = client.post(
            "/admin/notification-rules",
            json={"name": "Log Rule", "channel": "slack", "destination": "#logs"},
        )
        rule_id = create_resp.json()["rule_id"]
        resp = client.get(f"/admin/notification-rules/{rule_id}/logs")
        assert resp.status_code == 200
        assert resp.json()["logs"] == []

    def test_get_logs_not_found(self, client):
        resp = client.get("/admin/notification-rules/999/logs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Slack send (mocked)
# ---------------------------------------------------------------------------


class TestSlackSend:
    @patch("app.modules.slack_notifier.httpx.Client")
    def test_send_success(self, mock_client_cls):
        from app.modules.slack_notifier import send_slack_message

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("app.modules.slack_notifier.settings") as s:
            s.SLACK_BOT_TOKEN = "xoxb-test"
            result = send_slack_message("#test", "Hello")
            assert result["ok"] is True

    def test_send_no_token(self):
        from app.modules.slack_notifier import send_slack_message

        with patch("app.modules.slack_notifier.settings") as s:
            s.SLACK_BOT_TOKEN = None
            result = send_slack_message("#test", "Hello")
            assert result["ok"] is False
            assert result["error"] == "not_configured"


# ---------------------------------------------------------------------------
# Email dispatch with custom template
# ---------------------------------------------------------------------------


class TestEmailTemplate:
    @patch("app.modules.email_notifier._send_email")
    def test_custom_template(self, mock_send, rule, alert, vessel):
        from app.modules.notification_rules_engine import _dispatch_email

        rule.channel = "email"
        rule.destination = "test@example.com"
        rule.message_template = "Alert for {vessel_name}: score {risk_score}"
        mock_send.return_value = True
        result = _dispatch_email(rule, alert, vessel)
        assert result["success"] is True
        call_args = mock_send.call_args
        assert "SHADOW TANKER" in call_args[0][2]
        assert "80" in call_args[0][2]

    @patch("app.modules.email_notifier._send_email")
    def test_default_template(self, mock_send, rule, alert, vessel):
        from app.modules.notification_rules_engine import _dispatch_email

        rule.channel = "email"
        rule.destination = "test@example.com"
        rule.message_template = None
        mock_send.return_value = True
        result = _dispatch_email(rule, alert, vessel)
        assert result["success"] is True
        body = mock_send.call_args[0][2]
        assert "SHADOW TANKER" in body


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------


class TestModels:
    def test_notification_rule_defaults(self, db):
        r = NotificationRule(name="Test", channel="slack", destination="#ch")
        db.add(r)
        db.flush()
        assert r.is_active is True
        assert r.throttle_minutes == 30

    def test_notification_rule_log(self, db, rule, alert):
        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel="email",
            destination="x@y.com",
            status="sent",
        )
        db.add(log)
        db.flush()
        assert log.log_id is not None
        assert log.status == "sent"

    def test_rule_with_all_conditions(self, db):
        r = NotificationRule(
            name="Full",
            channel="webhook",
            destination="https://example.com",
            min_score=50,
            max_score=90,
            corridor_ids_json=[1, 2],
            vessel_flags_json=["RU"],
            alert_statuses_json=["new"],
            vessel_types_json=["tanker"],
            scoring_signals_json=["viirs"],
            time_window_start="08:00",
            time_window_end="18:00",
        )
        db.add(r)
        db.flush()
        assert r.rule_id is not None
        assert r.corridor_ids_json == [1, 2]
