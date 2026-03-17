"""Tests for evidence verification checklist (Task 32)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.modules.verification_checklist import (
    CHECKLIST_TEMPLATES,
    check_item,
    create_checklist_for_alert,
    enforce_checklist_before_verdict,
    get_checklist_for_alert,
    get_checklist_template,
    is_checklist_complete,
    uncheck_item,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(risk_score=50, corridor=None):
    alert = MagicMock()
    alert.risk_score = risk_score
    alert.corridor = corridor
    return alert


def _make_corridor(corridor_type="export_route"):
    corridor = MagicMock()
    corridor.corridor_type = MagicMock()
    corridor.corridor_type.value = corridor_type
    return corridor


def _make_checklist(checklist_id=1, alert_id=1, template="standard", completed_at=None):
    cl = MagicMock()
    cl.checklist_id = checklist_id
    cl.alert_id = alert_id
    cl.checklist_template = template
    cl.created_by = 1
    cl.created_at = datetime(2026, 3, 15, tzinfo=UTC)
    cl.completed_at = completed_at
    cl.completed_by = None
    return cl


def _make_item(item_id=1, checklist_id=1, item_key="check_ais_gap_duration",
               label="Test", is_checked=False, sort_order=0):
    item = MagicMock()
    item.item_id = item_id
    item.checklist_id = checklist_id
    item.item_key = item_key
    item.label = label
    item.is_checked = is_checked
    item.checked_by = None
    item.checked_at = None
    item.notes = None
    item.sort_order = sort_order
    return item


# ---------------------------------------------------------------------------
# Template selection tests
# ---------------------------------------------------------------------------


class TestGetChecklistTemplate:
    def test_standard_for_low_risk(self):
        alert = _make_alert(risk_score=30)
        assert get_checklist_template(alert) == "standard"

    def test_high_risk_for_score_70(self):
        alert = _make_alert(risk_score=70)
        assert get_checklist_template(alert) == "high_risk"

    def test_high_risk_for_score_above_70(self):
        alert = _make_alert(risk_score=90)
        assert get_checklist_template(alert) == "high_risk"

    def test_sts_zone_when_corridor_is_sts(self):
        corridor = _make_corridor("sts_zone")
        alert = _make_alert(risk_score=50, corridor=corridor)
        assert get_checklist_template(alert) == "sts_zone"

    def test_high_risk_takes_priority_over_sts_zone(self):
        corridor = _make_corridor("sts_zone")
        alert = _make_alert(risk_score=80, corridor=corridor)
        assert get_checklist_template(alert) == "high_risk"

    def test_standard_for_non_sts_corridor(self):
        corridor = _make_corridor("export_route")
        alert = _make_alert(risk_score=50, corridor=corridor)
        assert get_checklist_template(alert) == "standard"

    def test_standard_when_no_corridor(self):
        alert = _make_alert(risk_score=30, corridor=None)
        assert get_checklist_template(alert) == "standard"

    def test_standard_for_score_69(self):
        alert = _make_alert(risk_score=69)
        assert get_checklist_template(alert) == "standard"

    def test_none_risk_score_gives_standard(self):
        alert = _make_alert(risk_score=None)
        assert get_checklist_template(alert) == "standard"


# ---------------------------------------------------------------------------
# Template content tests
# ---------------------------------------------------------------------------


class TestTemplateContent:
    def test_standard_has_5_items(self):
        assert len(CHECKLIST_TEMPLATES["standard"]) == 5

    def test_high_risk_has_9_items(self):
        assert len(CHECKLIST_TEMPLATES["high_risk"]) == 9

    def test_sts_zone_has_8_items(self):
        assert len(CHECKLIST_TEMPLATES["sts_zone"]) == 8

    def test_high_risk_includes_standard_items(self):
        standard_keys = {item["key"] for item in CHECKLIST_TEMPLATES["standard"]}
        high_risk_keys = {item["key"] for item in CHECKLIST_TEMPLATES["high_risk"]}
        assert standard_keys.issubset(high_risk_keys)

    def test_sts_zone_includes_standard_items(self):
        standard_keys = {item["key"] for item in CHECKLIST_TEMPLATES["standard"]}
        sts_keys = {item["key"] for item in CHECKLIST_TEMPLATES["sts_zone"]}
        assert standard_keys.issubset(sts_keys)


# ---------------------------------------------------------------------------
# Checklist creation tests
# ---------------------------------------------------------------------------


class TestCreateChecklist:
    def test_create_standard_checklist(self):
        db = MagicMock()
        # The duplicate check: db.query().filter().first() returns None
        db.query.return_value.filter.return_value.first.return_value = None

        # Capture added objects and set IDs
        added = []

        def capture_add(obj):
            added.append(obj)
            if hasattr(obj, "checklist_id"):
                obj.checklist_id = 1
            if hasattr(obj, "item_id"):
                obj.item_id = len(added)

        db.add.side_effect = capture_add

        result = create_checklist_for_alert(db, alert_id=1, template="standard", analyst_id=1)
        assert result["checklist_template"] == "standard"
        assert result["alert_id"] == 1
        assert len(result["items"]) == 5
        assert result["items"][0]["item_key"] == "check_ais_gap_duration"

    def test_create_with_invalid_template_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            create_checklist_for_alert(db, alert_id=1, template="nonexistent", analyst_id=1)
        assert exc_info.value.status_code == 400

    def test_duplicate_checklist_raises_409(self):
        db = MagicMock()
        existing = _make_checklist()
        db.query.return_value.filter.return_value.first.return_value = existing
        with pytest.raises(HTTPException) as exc_info:
            create_checklist_for_alert(db, alert_id=1, template="standard", analyst_id=1)
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Check / uncheck item tests
# ---------------------------------------------------------------------------


class TestCheckItem:
    def test_check_item_sets_fields(self):
        db = MagicMock()
        item = _make_item(item_id=1)
        db.query.return_value.filter.return_value.first.return_value = item
        db.query.return_value.filter.return_value.all.return_value = [item]

        result = check_item(db, item_id=1, analyst_id=5, notes="Verified")
        assert item.is_checked is True
        assert item.checked_by == 5
        assert item.notes == "Verified"
        assert item.checked_at is not None

    def test_check_nonexistent_item_raises_404(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            check_item(db, item_id=999, analyst_id=1)
        assert exc_info.value.status_code == 404

    def test_uncheck_item_clears_fields(self):
        db = MagicMock()
        item = _make_item(item_id=1, is_checked=True)
        item.checked_by = 5
        item.checked_at = datetime(2026, 3, 15, tzinfo=UTC)
        db.query.return_value.filter.return_value.first.return_value = item

        result = uncheck_item(db, item_id=1)
        assert item.is_checked is False
        assert item.checked_by is None
        assert item.checked_at is None

    def test_uncheck_nonexistent_item_raises_404(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            uncheck_item(db, item_id=999)
        assert exc_info.value.status_code == 404

    def test_check_item_without_notes(self):
        db = MagicMock()
        item = _make_item(item_id=1)
        item.notes = None
        db.query.return_value.filter.return_value.first.return_value = item
        db.query.return_value.filter.return_value.all.return_value = [item]

        check_item(db, item_id=1, analyst_id=5)
        assert item.notes is None


# ---------------------------------------------------------------------------
# Completion detection tests
# ---------------------------------------------------------------------------


class TestIsChecklistComplete:
    def test_complete_when_all_checked(self):
        db = MagicMock()
        checklist = _make_checklist()
        items = [_make_item(item_id=i, is_checked=True) for i in range(5)]
        db.query.return_value.filter.return_value.all.side_effect = [
            [checklist],  # checklists query
            items,        # items query
        ]
        assert is_checklist_complete(db, alert_id=1) is True

    def test_incomplete_when_some_unchecked(self):
        db = MagicMock()
        checklist = _make_checklist()
        items = [_make_item(item_id=i, is_checked=(i < 3)) for i in range(5)]
        db.query.return_value.filter.return_value.all.side_effect = [
            [checklist],  # checklists query
            items,        # items query
        ]
        assert is_checklist_complete(db, alert_id=1) is False

    def test_complete_when_no_checklist(self):
        """No checklist created yet — should not block verdicts."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        assert is_checklist_complete(db, alert_id=1) is True

    def test_incomplete_when_no_items(self):
        db = MagicMock()
        checklist = _make_checklist()
        db.query.return_value.filter.return_value.all.side_effect = [
            [checklist],  # checklists query
            [],           # items query (empty)
        ]
        assert is_checklist_complete(db, alert_id=1) is False


# ---------------------------------------------------------------------------
# Verdict enforcement tests
# ---------------------------------------------------------------------------


class TestEnforceChecklistBeforeVerdict:
    def test_raises_when_incomplete(self):
        """Checklist exists but has unchecked items — verdict blocked."""
        db = MagicMock()
        checklist = _make_checklist()
        items = [_make_item(item_id=1, is_checked=False)]
        db.query.return_value.filter.return_value.all.side_effect = [
            [checklist],  # checklists query
            items,        # items query (unchecked)
        ]
        with pytest.raises(HTTPException) as exc_info:
            enforce_checklist_before_verdict(db, alert_id=1)
        assert exc_info.value.status_code == 400
        assert "checklist" in exc_info.value.detail.lower()

    def test_passes_when_complete(self):
        db = MagicMock()
        checklist = _make_checklist()
        items = [_make_item(item_id=i, is_checked=True) for i in range(5)]
        db.query.return_value.filter.return_value.all.side_effect = [
            [checklist],  # checklists query
            items,        # items query
        ]
        # Should not raise
        enforce_checklist_before_verdict(db, alert_id=1)


# ---------------------------------------------------------------------------
# Get checklist tests
# ---------------------------------------------------------------------------


class TestGetChecklist:
    def test_returns_none_when_no_checklist(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = get_checklist_for_alert(db, alert_id=1)
        assert result is None

    def test_returns_checklist_with_items(self):
        db = MagicMock()
        checklist = _make_checklist(checklist_id=1, alert_id=1, template="standard")
        items = [_make_item(item_id=i, checklist_id=1, sort_order=i) for i in range(3)]

        db.query.return_value.filter.return_value.first.return_value = checklist
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = items

        result = get_checklist_for_alert(db, alert_id=1)
        assert result is not None
        assert result["checklist_id"] == 1
        assert result["checklist_template"] == "standard"
        assert len(result["items"]) == 3


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestChecklistEndpoints:
    @pytest.fixture
    def api_client(self):
        from fastapi.testclient import TestClient

        from app.auth import require_auth
        from app.database import get_db
        from app.main import app

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.options.return_value = mock_db.query.return_value

        def override_get_db():
            yield mock_db

        def override_auth():
            return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_auth] = override_auth
        with TestClient(app) as client:
            yield client, mock_db
        app.dependency_overrides.clear()

    def test_create_checklist_alert_not_found(self, api_client):
        client, mock_db = api_client
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/api/v1/alerts/999/checklist", json={})
        assert resp.status_code == 404

    def test_get_checklist_not_found(self, api_client):
        client, mock_db = api_client
        resp = client.get("/api/v1/alerts/999/checklist")
        assert resp.status_code == 404

    def test_create_checklist_disabled(self, api_client):
        client, _ = api_client
        from app.config import settings

        original = settings.VERIFICATION_CHECKLIST_ENABLED
        try:
            settings.VERIFICATION_CHECKLIST_ENABLED = False
            resp = client.post("/api/v1/alerts/1/checklist", json={})
            assert resp.status_code == 400
        finally:
            settings.VERIFICATION_CHECKLIST_ENABLED = original

    def test_toggle_item_not_found(self, api_client):
        client, mock_db = api_client
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = client.patch(
            "/api/v1/alerts/1/checklist/items/999",
            json={"is_checked": True},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_corridor_type_as_string(self):
        """Corridor type might be a plain string rather than enum."""
        corridor = MagicMock()
        corridor.corridor_type = "sts_zone"  # plain string, no .value
        alert = _make_alert(risk_score=50, corridor=corridor)
        assert get_checklist_template(alert) == "sts_zone"

    def test_all_templates_have_unique_keys(self):
        for template_name, items in CHECKLIST_TEMPLATES.items():
            keys = [item["key"] for item in items]
            assert len(keys) == len(set(keys)), f"Duplicate keys in template {template_name}"

    def test_all_items_have_labels(self):
        for template_name, items in CHECKLIST_TEMPLATES.items():
            for item in items:
                assert item.get("label"), f"Missing label in {template_name}: {item}"
