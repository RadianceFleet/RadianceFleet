"""Tests for evidence card export module.

Tests:
  - export_evidence_card() JSON format
  - export_evidence_card() Markdown format
  - export_evidence_card() CSV format
  - Alert not found error
  - Status guard: new alerts cannot be exported
  - Regional coverage lookup

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestExportEvidenceCardJSON:
    """export_evidence_card(alert_id, 'json', db) produces JSON content."""

    def _mock_gap_and_vessel(self, db, status="under_review"):
        gap = MagicMock()
        gap.gap_event_id = 1
        gap.vessel_id = 10
        gap.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        gap.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        gap.duration_minutes = 360
        gap.corridor_id = 5
        gap.risk_score = 72
        gap.risk_breakdown_json = {"gap_duration_6h": 15}
        gap.status = status
        gap.analyst_notes = "Test notes"
        gap.impossible_speed_flag = False
        gap.velocity_plausibility_ratio = 0.8
        gap.max_plausible_distance_nm = 132.0
        gap.actual_gap_distance_nm = 105.0

        vessel = MagicMock()
        vessel.mmsi = "987654321"
        vessel.imo = "IMO9876543"
        vessel.name = "SHADOW TANKER"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.ais_source = "aisstream"

        corridor = MagicMock()
        corridor.name = "Laconian Gulf STS"
        corridor.corridor_type = MagicMock(value="sts_zone")

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = gap
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 3:
                result.filter.return_value.first.return_value = corridor
            else:
                result.filter.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.all.return_value = []
            return result

        db.query.side_effect = query_side_effect
        db.add = MagicMock()
        db.commit = MagicMock()
        return gap, vessel

    def test_json_export_returns_content(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "json", db)
        assert "content" in result
        assert "media_type" in result
        assert result["media_type"] == "application/json"

    def test_json_export_contains_vessel_info(self):
        from app.modules.evidence_export import export_evidence_card
        import json

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "json", db)
        content = json.loads(result["content"])
        assert "vessel" in content
        assert "gap" in content
        assert "risk" in content

    def test_json_export_has_disclaimer(self):
        from app.modules.evidence_export import export_evidence_card
        import json

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "json", db)
        content = json.loads(result["content"])
        assert "disclaimer" in content
        assert "triage" in content["disclaimer"].lower()


class TestExportEvidenceCardMarkdown:
    """export_evidence_card(alert_id, 'md', db) produces Markdown content."""

    def _mock_gap_and_vessel(self, db):
        gap = MagicMock()
        gap.gap_event_id = 1
        gap.vessel_id = 10
        gap.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        gap.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        gap.duration_minutes = 360
        gap.corridor_id = None
        gap.risk_score = 50
        gap.risk_breakdown_json = None
        gap.status = "under_review"
        gap.analyst_notes = None
        gap.impossible_speed_flag = False
        gap.velocity_plausibility_ratio = None
        gap.max_plausible_distance_nm = None
        gap.actual_gap_distance_nm = None

        vessel = MagicMock()
        vessel.mmsi = "123456789"
        vessel.imo = None
        vessel.name = "TEST VESSEL"
        vessel.flag = "GR"
        vessel.vessel_type = "Tanker"
        vessel.ais_source = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = gap
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = vessel
            else:
                result.filter.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.all.return_value = []
            return result

        db.query.side_effect = query_side_effect
        db.add = MagicMock()
        db.commit = MagicMock()
        return gap

    def test_markdown_export_returns_content(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "md", db)
        assert "content" in result
        assert result["media_type"] == "text/markdown"

    def test_markdown_contains_heading(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "md", db)
        assert "# RadianceFleet Evidence Card" in result["content"]


class TestExportEvidenceCardCSV:
    """export_evidence_card(alert_id, 'csv', db) produces CSV content."""

    def _mock_gap_and_vessel(self, db):
        gap = MagicMock()
        gap.gap_event_id = 1
        gap.vessel_id = 10
        gap.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        gap.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        gap.duration_minutes = 360
        gap.corridor_id = None
        gap.risk_score = 40
        gap.risk_breakdown_json = None
        gap.status = "documented"
        gap.analyst_notes = None
        gap.impossible_speed_flag = False
        gap.velocity_plausibility_ratio = None
        gap.max_plausible_distance_nm = None
        gap.actual_gap_distance_nm = None

        vessel = MagicMock()
        vessel.mmsi = "123456789"
        vessel.imo = None
        vessel.name = "CSV TEST"
        vessel.flag = "PA"
        vessel.vessel_type = "Tanker"
        vessel.ais_source = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = gap
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = vessel
            else:
                result.filter.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.first.return_value = None
                result.filter.return_value.order_by.return_value.all.return_value = []
            return result

        db.query.side_effect = query_side_effect
        db.add = MagicMock()
        db.commit = MagicMock()

    def test_csv_export_returns_content(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "csv", db)
        assert "content" in result
        assert result["media_type"] == "text/csv"

    def test_csv_contains_header_row(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        self._mock_gap_and_vessel(db)

        result = export_evidence_card(1, "csv", db)
        lines = result["content"].strip().split("\n")
        assert len(lines) >= 2  # header + data row
        assert "alert_id" in lines[0]


class TestExportErrors:
    """Error handling in evidence card export."""

    def test_alert_not_found_returns_error(self):
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = export_evidence_card(99999, "json", db)
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_new_status_returns_error(self):
        """Alerts with status 'new' cannot be exported (NFR7)."""
        from app.modules.evidence_export import export_evidence_card

        db = MagicMock()
        gap = MagicMock()
        gap.status = "new"
        db.query.return_value.filter.return_value.first.return_value = gap

        result = export_evidence_card(1, "json", db)
        assert "error" in result
        assert "review" in result["error"].lower()


class TestExportEndpoints:
    """API endpoints for evidence export."""

    def test_export_json_endpoint(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"evidence": "card"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=json")
            assert resp.status_code == 200

    def test_export_markdown_endpoint(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"markdown": "# Card"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=md")
            assert resp.status_code == 200

    def test_export_error_returns_400(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"error": "Status is new"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=json")
            assert resp.status_code == 400


class TestCorridorCoverage:
    """Test _corridor_coverage helper for regional AIS coverage lookup."""

    def test_unknown_corridor_returns_unknown(self):
        from app.modules.evidence_export import _corridor_coverage

        quality, desc = _corridor_coverage(None)
        assert quality == "UNKNOWN"

    def test_baltic_corridor_returns_good(self):
        from app.modules.evidence_export import _corridor_coverage

        quality, desc = _corridor_coverage("Baltic Transit Route")
        assert quality == "GOOD"
