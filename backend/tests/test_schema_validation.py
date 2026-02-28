"""Tests for G4: Request schema validation with Pydantic models."""
import pytest
from pydantic import ValidationError

from app.schemas.corridor import CorridorCreateRequest, CorridorUpdateRequest
from app.schemas.alerts import BulkStatusUpdateRequest, WatchlistAddRequest, NoteAddRequest


class TestCorridorCreateRequest:
    def test_valid_data(self):
        req = CorridorCreateRequest(name="Baltic Export Gate", corridor_type="export_route", risk_weight=2.0)
        assert req.name == "Baltic Export Gate"
        assert req.corridor_type == "export_route"
        assert req.risk_weight == 2.0

    def test_defaults(self):
        req = CorridorCreateRequest(name="Test")
        assert req.corridor_type == "import_route"
        assert req.risk_weight == 1.0
        assert req.is_jamming_zone is False
        assert req.geometry_wkt is None

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError) as exc_info:
            CorridorCreateRequest(name="")
        assert "name" in str(exc_info.value).lower()

    def test_rejects_missing_name(self):
        with pytest.raises(ValidationError):
            CorridorCreateRequest()

    def test_rejects_risk_weight_above_10(self):
        with pytest.raises(ValidationError) as exc_info:
            CorridorCreateRequest(name="Test", risk_weight=11.0)
        assert "risk_weight" in str(exc_info.value).lower()

    def test_rejects_negative_risk_weight(self):
        with pytest.raises(ValidationError):
            CorridorCreateRequest(name="Test", risk_weight=-1.0)

    def test_accepts_boundary_risk_weight(self):
        req = CorridorCreateRequest(name="Test", risk_weight=0.0)
        assert req.risk_weight == 0.0
        req2 = CorridorCreateRequest(name="Test", risk_weight=10.0)
        assert req2.risk_weight == 10.0


class TestCorridorUpdateRequest:
    def test_partial_update(self):
        req = CorridorUpdateRequest(name="Updated Name")
        updates = req.model_dump(exclude_unset=True)
        assert updates == {"name": "Updated Name"}

    def test_empty_update(self):
        req = CorridorUpdateRequest()
        updates = req.model_dump(exclude_unset=True)
        assert updates == {}

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            CorridorUpdateRequest(name="")


class TestBulkStatusUpdateRequest:
    def test_valid_data(self):
        req = BulkStatusUpdateRequest(alert_ids=[1, 2, 3], status="under_review")
        assert req.alert_ids == [1, 2, 3]
        assert req.status == "under_review"

    def test_rejects_empty_alert_ids(self):
        with pytest.raises(ValidationError) as exc_info:
            BulkStatusUpdateRequest(alert_ids=[], status="under_review")
        errors = str(exc_info.value).lower()
        assert "alert_ids" in errors

    def test_rejects_missing_status(self):
        with pytest.raises(ValidationError):
            BulkStatusUpdateRequest(alert_ids=[1])


class TestWatchlistAddRequest:
    def test_defaults(self):
        req = WatchlistAddRequest()
        assert req.source == "manual"
        assert req.vessel_id is None
        assert req.reason is None

    def test_with_vessel_id(self):
        req = WatchlistAddRequest(vessel_id=42, reason="Suspicious")
        assert req.vessel_id == 42
        assert req.reason == "Suspicious"


class TestNoteAddRequest:
    def test_accepts_notes(self):
        req = NoteAddRequest(notes="A note")
        assert req.notes == "A note"

    def test_accepts_text(self):
        req = NoteAddRequest(text="Legacy text")
        assert req.text == "Legacy text"

    def test_both_none(self):
        req = NoteAddRequest()
        assert req.notes is None
        assert req.text is None
