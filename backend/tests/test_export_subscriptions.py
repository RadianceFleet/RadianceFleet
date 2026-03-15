"""Tests for bulk export subscriptions (Task 49)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.models.export_subscription import ExportSubscription
from app.models.export_run import ExportRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subscription(**overrides) -> MagicMock:
    defaults = {
        "subscription_id": 1,
        "name": "Test Export",
        "created_by": 1,
        "schedule": "daily",
        "schedule_day": None,
        "schedule_hour_utc": 6,
        "export_type": "alerts",
        "filter_json": None,
        "columns_json": None,
        "format": "csv",
        "delivery_method": "email",
        "delivery_config_json": {"email": "test@example.com"},
        "is_active": True,
        "last_run_at": None,
        "last_run_status": None,
        "last_run_rows": None,
        "created_at": datetime(2026, 3, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    sub = MagicMock(spec=ExportSubscription)
    for k, v in defaults.items():
        setattr(sub, k, v)
    return sub


def _make_gap_event(gap_event_id, vessel_id, start, end, score=50, corridor_id=None):
    m = MagicMock()
    m.gap_event_id = gap_event_id
    m.vessel_id = vessel_id
    m.gap_start_utc = start
    m.gap_end_utc = end
    m.duration_minutes = int((end - start).total_seconds() / 60)
    m.status = MagicMock(value="new")
    m.risk_score = score
    m.corridor_id = corridor_id
    return m


def _make_vessel(vessel_id, mmsi, name="TestVessel", flag="PA"):
    m = MagicMock()
    m.vessel_id = vessel_id
    m.mmsi = mmsi
    m.imo = f"IMO{vessel_id}"
    m.name = name
    m.flag = flag
    m.vessel_type = "Tanker"
    m.deadweight = 50000.0
    return m


def _make_ais_point(point_id, vessel_id, lat, lon, ts):
    m = MagicMock()
    m.ais_point_id = point_id
    m.vessel_id = vessel_id
    m.latitude = lat
    m.longitude = lon
    m.speed = 12.5
    m.course = 180.0
    m.heading = 180.0
    m.timestamp_utc = ts
    m.source = "test"
    return m


def _make_evidence_card(card_id, gap_event_id):
    m = MagicMock()
    m.evidence_card_id = card_id
    m.gap_event_id = gap_event_id
    m.version = 1
    m.export_format = "json"
    m.created_at = datetime(2026, 3, 10, tzinfo=UTC)
    m.approval_status = "draft"
    m.score_snapshot = 75
    return m


# ---------------------------------------------------------------------------
# Export Engine Tests
# ---------------------------------------------------------------------------


class TestExportEngine:
    def test_generate_export_csv_alerts(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="alerts", format="csv")
        gap = _make_gap_event(1, 1, datetime(2026, 3, 1), datetime(2026, 3, 2))
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [gap]

        file_bytes, filename, count = generate_export(db, sub)
        assert count == 1
        assert filename.endswith(".csv")
        assert b"gap_event_id" in file_bytes

    def test_generate_export_json_vessels(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="vessels", format="json")
        vessel = _make_vessel(1, "123456789")
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [vessel]

        file_bytes, filename, count = generate_export(db, sub)
        assert count == 1
        assert filename.endswith(".json")
        data = json.loads(file_bytes)
        assert data[0]["mmsi"] == "123456789"

    @patch("app.modules.export_engine.settings")
    def test_generate_export_parquet_ais_positions(self, mock_settings):
        from app.modules.export_engine import generate_export

        mock_settings.EXPORT_MAX_ROWS = 100000
        sub = _make_subscription(export_type="ais_positions", format="parquet")
        point = _make_ais_point(1, 1, 55.0, 10.0, datetime(2026, 3, 10))
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [point]

        try:
            file_bytes, filename, count = generate_export(db, sub)
            assert count == 1
            assert filename.endswith(".parquet")
            assert len(file_bytes) > 0
        except ImportError:
            pytest.skip("polars not available")

    def test_generate_export_evidence_cards(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="evidence_cards", format="json")
        card = _make_evidence_card(1, 10)
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [card]

        file_bytes, filename, count = generate_export(db, sub)
        assert count == 1
        data = json.loads(file_bytes)
        assert data[0]["evidence_card_id"] == 1

    def test_column_selection(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(
            export_type="vessels",
            format="json",
            columns_json=["vessel_id", "mmsi"],
        )
        vessel = _make_vessel(1, "123456789")
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = [vessel]

        file_bytes, _, count = generate_export(db, sub)
        data = json.loads(file_bytes)
        assert set(data[0].keys()) == {"vessel_id", "mmsi"}

    @patch("app.modules.export_engine.settings")
    def test_max_rows_limit(self, mock_settings):
        from app.modules.export_engine import generate_export

        mock_settings.EXPORT_MAX_ROWS = 2
        sub = _make_subscription(export_type="vessels", format="json")
        vessels = [_make_vessel(i, f"12345678{i}") for i in range(5)]
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = vessels

        file_bytes, _, count = generate_export(db, sub)
        data = json.loads(file_bytes)
        assert len(data) == 2
        assert count == 2

    def test_empty_export_csv(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="alerts", format="csv")
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        file_bytes, _, count = generate_export(db, sub)
        assert count == 0
        assert file_bytes == b""

    def test_empty_export_json(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="alerts", format="json")
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        file_bytes, _, count = generate_export(db, sub)
        assert count == 0
        data = json.loads(file_bytes)
        assert data == []

    def test_unknown_export_type_raises(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(export_type="unknown_type")
        db = MagicMock()
        with pytest.raises(ValueError, match="Unknown export type"):
            generate_export(db, sub)

    def test_unknown_format_raises(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(format="xlsx")
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        with pytest.raises(ValueError, match="Unknown format"):
            generate_export(db, sub)


# ---------------------------------------------------------------------------
# Date Filter Resolution Tests
# ---------------------------------------------------------------------------


class TestDateFilterResolution:
    def test_last_day_resolution(self):
        from app.modules.export_engine import _resolve_date_filters

        result = _resolve_date_filters({"date_mode": "last_day"})
        assert "date_from" in result
        assert "date_to" in result
        assert "date_mode" not in result

    def test_last_week_resolution(self):
        from app.modules.export_engine import _resolve_date_filters

        result = _resolve_date_filters({"date_mode": "last_week"})
        assert "date_from" in result

    def test_last_month_resolution(self):
        from app.modules.export_engine import _resolve_date_filters

        result = _resolve_date_filters({"date_mode": "last_month"})
        assert "date_from" in result

    def test_no_date_mode_passthrough(self):
        from app.modules.export_engine import _resolve_date_filters

        result = _resolve_date_filters({"vessel_id": 42})
        assert result == {"vessel_id": 42}

    def test_empty_filters(self):
        from app.modules.export_engine import _resolve_date_filters

        result = _resolve_date_filters({})
        assert result == {}


# ---------------------------------------------------------------------------
# Delivery Tests
# ---------------------------------------------------------------------------


class TestEmailDelivery:
    @patch("app.modules.export_delivery.settings")
    def test_no_email_configured(self, mock_settings):
        from app.modules.export_delivery import deliver_via_email

        mock_settings.RESEND_API_KEY = None
        mock_settings.SMTP_HOST = None
        result = deliver_via_email(b"data", "test.csv", {"email": "a@b.com"})
        assert result["status"] == "failed"

    def test_no_email_in_config(self):
        from app.modules.export_delivery import deliver_via_email

        result = deliver_via_email(b"data", "test.csv", {})
        assert result["status"] == "failed"
        assert "No email" in result["error"]

    def test_file_too_large(self):
        from app.modules.export_delivery import deliver_via_email

        big = b"x" * (11 * 1024 * 1024)
        result = deliver_via_email(big, "test.csv", {"email": "a@b.com"})
        assert result["status"] == "failed"
        assert "too large" in result["error"]

    @patch("app.modules.export_delivery.settings")
    @patch("smtplib.SMTP")
    def test_smtp_delivery_success(self, mock_smtp_cls, mock_settings):
        from app.modules.export_delivery import deliver_via_email

        mock_settings.RESEND_API_KEY = None
        mock_settings.SMTP_HOST = "smtp.test.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASS = "pass"

        smtp_instance = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = deliver_via_email(b"data", "test.csv", {"email": "a@b.com"})
        assert result["status"] == "sent"
        assert result["method"] == "smtp"


class TestS3Delivery:
    def test_no_bucket_configured(self):
        from app.modules.export_delivery import deliver_via_s3

        with patch("app.modules.export_delivery.settings") as mock_settings:
            mock_settings.EXPORT_S3_BUCKET = None
            mock_settings.EXPORT_S3_PREFIX = None
            mock_settings.EXPORT_S3_REGION = None
            mock_settings.EXPORT_S3_ENDPOINT_URL = None
            result = deliver_via_s3(b"data", "test.csv", {})
            assert result["status"] == "failed"

    @patch("app.modules.export_delivery.settings")
    def test_s3_success(self, mock_settings):
        from app.modules.export_delivery import deliver_via_s3

        mock_settings.EXPORT_S3_BUCKET = None
        mock_settings.EXPORT_S3_PREFIX = None
        mock_settings.EXPORT_S3_REGION = None
        mock_settings.EXPORT_S3_ENDPOINT_URL = None

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = deliver_via_s3(
                b"data", "test.csv", {"bucket": "my-bucket", "prefix": "exports"}
            )
            assert result["status"] == "sent"
            assert result["method"] == "s3"
            assert result["bucket"] == "my-bucket"

    def test_boto3_not_installed(self):
        from app.modules.export_delivery import deliver_via_s3

        import sys
        # Temporarily remove boto3 from imports if present
        with patch.dict("sys.modules", {"boto3": None}):
            with patch("app.modules.export_delivery.settings") as mock_settings:
                mock_settings.EXPORT_S3_BUCKET = None
                mock_settings.EXPORT_S3_PREFIX = None
                mock_settings.EXPORT_S3_REGION = None
                mock_settings.EXPORT_S3_ENDPOINT_URL = None
                result = deliver_via_s3(b"data", "test.csv", {"bucket": "b"})
                assert result["status"] == "failed"
                assert "boto3" in result["error"]


class TestWebhookDelivery:
    def test_no_url_configured(self):
        from app.modules.export_delivery import deliver_via_webhook

        result = deliver_via_webhook(b"data", "test.csv", {})
        assert result["status"] == "failed"
        assert "No webhook URL" in result["error"]

    @patch("httpx.Client")
    def test_webhook_success(self, mock_client_cls):
        from app.modules.export_delivery import deliver_via_webhook

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = deliver_via_webhook(
            b"data", "test.csv", {"url": "https://hooks.test/x"}
        )
        assert result["status"] == "sent"

    @patch("httpx.Client")
    def test_webhook_with_hmac_signature(self, mock_client_cls):
        from app.modules.export_delivery import deliver_via_webhook

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = deliver_via_webhook(
            b"data", "test.csv", {"url": "https://hooks.test/x", "secret": "s3cret"}
        )
        assert result["status"] == "sent"
        # Verify HMAC header was set
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert "X-Webhook-Signature" in headers


class TestDeliverDispatch:
    def test_unknown_method(self):
        from app.modules.export_delivery import deliver

        result = deliver(b"data", "test.csv", "ftp", {})
        assert result["status"] == "failed"
        assert "Unknown delivery method" in result["error"]


# ---------------------------------------------------------------------------
# Scheduler Tests
# ---------------------------------------------------------------------------


class TestScheduleEvaluation:
    def test_daily_never_run(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(schedule="daily", schedule_hour_utc=6, last_run_at=None)
        now = datetime(2026, 3, 15, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is True

    def test_daily_before_hour(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(schedule="daily", schedule_hour_utc=6, last_run_at=None)
        now = datetime(2026, 3, 15, 5, 0, tzinfo=UTC)
        assert _is_due(sub, now) is False

    def test_daily_already_run_today(self):
        from app.modules.export_scheduler import _is_due

        last = datetime(2026, 3, 15, 6, 30, tzinfo=UTC)
        sub = _make_subscription(schedule="daily", schedule_hour_utc=6, last_run_at=last)
        now = datetime(2026, 3, 15, 8, 0, tzinfo=UTC)
        assert _is_due(sub, now) is False

    def test_daily_run_next_day(self):
        from app.modules.export_scheduler import _is_due

        last = datetime(2026, 3, 14, 6, 30, tzinfo=UTC)
        sub = _make_subscription(schedule="daily", schedule_hour_utc=6, last_run_at=last)
        now = datetime(2026, 3, 15, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is True

    def test_weekly_correct_day(self):
        from app.modules.export_scheduler import _is_due

        # 2026-03-16 is a Monday (weekday=0)
        sub = _make_subscription(
            schedule="weekly", schedule_day=0, schedule_hour_utc=6, last_run_at=None
        )
        now = datetime(2026, 3, 16, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is True

    def test_weekly_wrong_day(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(
            schedule="weekly", schedule_day=0, schedule_hour_utc=6, last_run_at=None
        )
        now = datetime(2026, 3, 15, 7, 0, tzinfo=UTC)  # Sunday
        assert _is_due(sub, now) is False

    def test_monthly_correct_day(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(
            schedule="monthly", schedule_day=15, schedule_hour_utc=6, last_run_at=None
        )
        now = datetime(2026, 3, 15, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is True

    def test_monthly_wrong_day(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(
            schedule="monthly", schedule_day=15, schedule_hour_utc=6, last_run_at=None
        )
        now = datetime(2026, 3, 14, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is False

    def test_unknown_schedule(self):
        from app.modules.export_scheduler import _is_due

        sub = _make_subscription(schedule="biweekly")
        now = datetime(2026, 3, 15, 7, 0, tzinfo=UTC)
        assert _is_due(sub, now) is False


class TestRunDueExports:
    @patch("app.modules.export_scheduler.settings")
    def test_disabled_returns_empty(self, mock_settings):
        from app.modules.export_scheduler import run_due_exports

        mock_settings.EXPORT_SUBSCRIPTIONS_ENABLED = False
        db = MagicMock()
        result = run_due_exports(db)
        assert result == []

    @patch("app.modules.export_scheduler.settings")
    @patch("app.modules.export_scheduler._is_due")
    def test_no_due_exports(self, mock_is_due, mock_settings):
        from app.modules.export_scheduler import run_due_exports

        mock_settings.EXPORT_SUBSCRIPTIONS_ENABLED = True
        mock_is_due.return_value = False
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.filter.return_value = q
        q.all.return_value = [_make_subscription()]
        result = run_due_exports(db)
        assert result == []


class TestCleanupExpiredFiles:
    def test_cleanup_empty_dir(self, tmp_path):
        from app.modules.export_scheduler import cleanup_expired_files

        with patch("app.modules.export_scheduler.settings") as mock_settings:
            mock_settings.EXPORT_TEMP_DIR = str(tmp_path)
            mock_settings.EXPORT_FILE_RETENTION_HOURS = 72
            result = cleanup_expired_files()
            assert result == 0

    def test_cleanup_nonexistent_dir(self):
        from app.modules.export_scheduler import cleanup_expired_files

        with patch("app.modules.export_scheduler.settings") as mock_settings:
            mock_settings.EXPORT_TEMP_DIR = "/nonexistent/path"
            result = cleanup_expired_files()
            assert result == 0


# ---------------------------------------------------------------------------
# Routes / API Tests
# ---------------------------------------------------------------------------


class TestRoutesValidation:
    def test_validate_fields_valid(self):
        from app.api.routes_exports import _validate_subscription_fields

        # Should not raise
        _validate_subscription_fields("daily", "alerts", "csv", "email")

    def test_validate_fields_invalid_schedule(self):
        from app.api.routes_exports import _validate_subscription_fields
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_subscription_fields("biweekly", None, None, None)
        assert exc_info.value.status_code == 400

    def test_validate_fields_invalid_export_type(self):
        from app.api.routes_exports import _validate_subscription_fields
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_subscription_fields(None, "users", None, None)

    def test_validate_fields_invalid_format(self):
        from app.api.routes_exports import _validate_subscription_fields
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_subscription_fields(None, None, "xlsx", None)

    def test_validate_fields_invalid_delivery(self):
        from app.api.routes_exports import _validate_subscription_fields
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_subscription_fields(None, None, None, "ftp")


class TestSubscriptionToDict:
    def test_masks_sensitive_fields(self):
        from app.api.routes_exports import _subscription_to_dict

        sub = _make_subscription(
            delivery_config_json={
                "email": "test@example.com",
                "aws_secret_access_key": "REAL_SECRET",
                "secret": "hmac_secret",
            }
        )
        result = _subscription_to_dict(sub)
        assert result["delivery_config_json"]["email"] == "test@example.com"
        assert result["delivery_config_json"]["aws_secret_access_key"] == "***"
        assert result["delivery_config_json"]["secret"] == "***"

    def test_null_delivery_config(self):
        from app.api.routes_exports import _subscription_to_dict

        sub = _make_subscription(delivery_config_json=None)
        result = _subscription_to_dict(sub)
        assert result["delivery_config_json"] is None


class TestRunToDict:
    def test_basic_serialization(self):
        from app.api.routes_exports import _run_to_dict

        run = MagicMock(spec=ExportRun)
        run.run_id = 1
        run.subscription_id = 1
        run.started_at = datetime(2026, 3, 15, tzinfo=UTC)
        run.finished_at = datetime(2026, 3, 15, 0, 5, tzinfo=UTC)
        run.status = "completed"
        run.row_count = 100
        run.file_size_bytes = 5000
        run.delivery_status = "sent"
        run.error_message = None
        run.created_at = datetime(2026, 3, 15, tzinfo=UTC)

        result = _run_to_dict(run)
        assert result["run_id"] == 1
        assert result["status"] == "completed"
        assert result["row_count"] == 100


# ---------------------------------------------------------------------------
# CSV / JSON helpers
# ---------------------------------------------------------------------------


class TestCsvOutput:
    def test_csv_empty(self):
        from app.modules.export_engine import _to_csv

        assert _to_csv([]) == b""

    def test_csv_with_data(self):
        from app.modules.export_engine import _to_csv

        rows = [{"a": 1, "b": "hello"}, {"a": 2, "b": "world"}]
        result = _to_csv(rows)
        assert b"a,b" in result
        assert b"1,hello" in result


class TestJsonOutput:
    def test_json_empty(self):
        from app.modules.export_engine import _to_json

        result = _to_json([])
        assert json.loads(result) == []

    def test_json_with_data(self):
        from app.modules.export_engine import _to_json

        rows = [{"x": 1}]
        result = _to_json(rows)
        data = json.loads(result)
        assert data[0]["x"] == 1


# ---------------------------------------------------------------------------
# Model instantiation tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_export_subscription_tablename(self):
        assert ExportSubscription.__tablename__ == "export_subscriptions"

    def test_export_run_tablename(self):
        assert ExportRun.__tablename__ == "export_runs"

    def test_export_subscription_has_required_columns(self):
        col_names = {c.name for c in ExportSubscription.__table__.columns}
        expected = {
            "subscription_id", "name", "created_by", "schedule",
            "schedule_day", "schedule_hour_utc", "export_type",
            "filter_json", "columns_json", "format", "delivery_method",
            "delivery_config_json", "is_active", "last_run_at",
            "last_run_status", "last_run_rows", "created_at",
        }
        assert expected.issubset(col_names)

    def test_export_run_has_required_columns(self):
        col_names = {c.name for c in ExportRun.__table__.columns}
        expected = {
            "run_id", "subscription_id", "started_at", "finished_at",
            "status", "row_count", "file_size_bytes", "file_path",
            "delivery_status", "error_message", "created_at",
        }
        assert expected.issubset(col_names)


# ---------------------------------------------------------------------------
# Filter Application Tests
# ---------------------------------------------------------------------------


class TestFilterApplication:
    def test_alert_filter_date_from(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(
            export_type="alerts",
            format="json",
            filter_json={"date_from": "2026-03-01T00:00:00"},
        )
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        file_bytes, _, count = generate_export(db, sub)
        assert count == 0
        # Verify filter was applied (at least one filter call)
        assert q.filter.called

    def test_vessel_filter_flag(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(
            export_type="vessels",
            format="json",
            filter_json={"flag": "PA"},
        )
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        generate_export(db, sub)
        assert q.filter.called

    def test_date_mode_last_day_applied(self):
        from app.modules.export_engine import generate_export

        sub = _make_subscription(
            export_type="alerts",
            format="json",
            filter_json={"date_mode": "last_day"},
        )
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.order_by.return_value = q
        q.filter.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        generate_export(db, sub)
        # date_mode should have been resolved and filter applied
        assert q.filter.called
