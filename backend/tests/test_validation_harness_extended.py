"""Tests for analyst_feedback_metrics and detector_correlation_report."""

from unittest.mock import MagicMock, patch


def _make_reviewed_alert(
    gap_event_id=1, is_false_positive=False, risk_score=80, corridor_id=1, risk_breakdown_json=None
):
    alert = MagicMock()
    alert.gap_event_id = gap_event_id
    alert.is_false_positive = is_false_positive
    alert.risk_score = risk_score
    alert.corridor_id = corridor_id
    alert.risk_breakdown_json = risk_breakdown_json
    return alert


class TestAnalystFeedbackMetrics:
    @patch("app.modules.validation_harness.AISGapEvent")
    def test_empty_reviewed(self, MockGapEvent):
        from app.modules.validation_harness import analyst_feedback_metrics

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = analyst_feedback_metrics(db)
        assert result["total_reviewed"] == 0
        assert result["confirmed_tp"] == 0
        assert result["confirmed_fp"] == 0
        assert result["fp_rate"] == 0.0

    @patch("app.modules.validation_harness.AISGapEvent")
    def test_mixed_reviews(self, MockGapEvent):
        from app.modules.validation_harness import analyst_feedback_metrics

        db = MagicMock()

        alerts = [
            _make_reviewed_alert(1, is_false_positive=False, risk_score=85, corridor_id=1),
            _make_reviewed_alert(2, is_false_positive=True, risk_score=60, corridor_id=1),
            _make_reviewed_alert(3, is_false_positive=False, risk_score=30, corridor_id=2),
            _make_reviewed_alert(4, is_false_positive=True, risk_score=90, corridor_id=None),
        ]
        db.query.return_value.filter.return_value.all.return_value = alerts

        result = analyst_feedback_metrics(db)
        assert result["total_reviewed"] == 4
        assert result["confirmed_tp"] == 2
        assert result["confirmed_fp"] == 2
        assert result["fp_rate"] == 0.5
        assert "by_score_band" in result
        assert "by_corridor" in result

    @patch("app.modules.validation_harness.AISGapEvent")
    def test_all_tp(self, MockGapEvent):
        from app.modules.validation_harness import analyst_feedback_metrics

        db = MagicMock()

        alerts = [
            _make_reviewed_alert(1, is_false_positive=False, risk_score=80),
            _make_reviewed_alert(2, is_false_positive=False, risk_score=90),
        ]
        db.query.return_value.filter.return_value.all.return_value = alerts

        result = analyst_feedback_metrics(db)
        assert result["confirmed_fp"] == 0
        assert result["fp_rate"] == 0.0


class TestDetectorCorrelationReport:
    @patch("app.modules.validation_harness.AISGapEvent")
    def test_empty_reviewed(self, MockGapEvent):
        from app.modules.validation_harness import detector_correlation_report

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = detector_correlation_report(db)
        assert result == []

    @patch("app.modules.validation_harness.AISGapEvent")
    def test_co_occurrence_calculation(self, MockGapEvent):
        from app.modules.validation_harness import detector_correlation_report

        db = MagicMock()

        alerts = [
            _make_reviewed_alert(
                1,
                is_false_positive=False,
                risk_breakdown_json={"dark_zone": 10, "speed_anomaly": 5},
            ),
            _make_reviewed_alert(
                2,
                is_false_positive=True,
                risk_breakdown_json={"dark_zone": 10, "speed_anomaly": 5},
            ),
            _make_reviewed_alert(
                3,
                is_false_positive=False,
                risk_breakdown_json={"dark_zone": 10, "flag_risk": 15},
            ),
        ]
        db.query.return_value.filter.return_value.all.return_value = alerts

        result = detector_correlation_report(db)
        assert len(result) > 0
        # dark_zone+speed_anomaly should appear twice (alerts 1 and 2)
        dz_sa = next(
            (
                r
                for r in result
                if {r["category_a"], r["category_b"]} == {"dark_zone", "speed_anomaly"}
            ),
            None,
        )
        assert dz_sa is not None
        assert dz_sa["co_occurrence_count"] == 2
        assert dz_sa["fp_count"] == 1
        assert dz_sa["fp_rate"] == 0.5

    @patch("app.modules.validation_harness.AISGapEvent")
    def test_string_json_breakdown(self, MockGapEvent):
        """risk_breakdown_json stored as a JSON string should still work."""
        import json

        from app.modules.validation_harness import detector_correlation_report

        db = MagicMock()

        alerts = [
            _make_reviewed_alert(
                1,
                is_false_positive=False,
                risk_breakdown_json=json.dumps({"a": 1, "b": 2}),
            ),
        ]
        db.query.return_value.filter.return_value.all.return_value = alerts

        result = detector_correlation_report(db)
        assert len(result) == 1
        assert result[0]["co_occurrence_count"] == 1
