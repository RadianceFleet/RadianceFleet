"""Regression tests for dark vessel scoring signal fix.

The original bug: DarkVesselDetection.matched_vessel_id == gap.vessel_id
combined with ais_match_result == "unmatched" was a logical contradiction —
unmatched detections have NULL matched_vessel_id, so the query always
returned 0 rows. The fix uses spatial+temporal proximity instead.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _make_gap(vessel_id=1, gap_start=None, gap_end=None, off_lat=60.0, off_lon=25.0,
              corridor_id=None, max_dist=200.0):
    """Create a mock AISGapEvent with the fields scoring needs."""
    gap = MagicMock()
    gap.vessel_id = vessel_id
    gap.gap_start_utc = gap_start or datetime(2025, 12, 1, 0, 0)
    gap.gap_end_utc = gap_end or datetime(2025, 12, 2, 0, 0)
    gap.gap_off_lat = off_lat
    gap.gap_off_lon = off_lon
    gap.gap_on_lat = 61.0
    gap.gap_on_lon = 26.0
    gap.corridor_id = corridor_id
    gap.max_plausible_distance_nm = max_dist
    gap.start_point = None
    gap.end_point = None
    gap.start_point_id = None
    gap.end_point_id = None
    gap.duration_minutes = 1440
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = 0.5
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.pre_gap_sog = 12.0
    gap.risk_score = 0
    gap.risk_breakdown_json = None
    gap.status = "new"
    gap.actual_gap_distance_nm = 50.0
    gap.source = "gfw"

    vessel = MagicMock()
    vessel.vessel_id = vessel_id
    vessel.mmsi = "636017000"
    vessel.deadweight = 100000
    vessel.year_built = 2005
    vessel.flag = "LR"
    vessel.flag_risk_category = "STANDARD"
    vessel.vessel_type = "Crude Oil Tanker"
    vessel.ais_class = "A"
    vessel.imo = "9876543"
    gap.vessel = vessel

    return gap, vessel


def _make_dark_detection(det_id, lat, lon, time_utc, corridor_id=None, match_result="unmatched"):
    det = MagicMock()
    det.detection_id = det_id
    det.detection_lat = lat
    det.detection_lon = lon
    det.detection_time_utc = time_utc
    det.corridor_id = corridor_id
    det.ais_match_result = match_result
    det.matched_vessel_id = None if match_result == "unmatched" else 1
    return det


class TestDarkVesselScoringFix:
    """Regression tests for the dark vessel scoring bug fix."""

    def test_dark_vessel_signal_fires_for_unmatched(self):
        """Create DarkVesselDetection with matched_vessel_id=NULL near gap.
        Assert score includes dark_vessel points."""
        from app.modules.risk_scoring import compute_gap_score

        config = {
            "gap_duration": {},
            "dark_vessel": {
                "unmatched_detection_in_corridor": 35,
                "unmatched_detection_outside_corridor": 20,
            },
            "gap_frequency": {},
            "speed_anomaly": {},
            "movement_envelope": {},
            "spoofing": {},
            "metadata": {},
            "vessel_age": {},
            "flag_state": {},
            "vessel_size_multiplier": {},
            "watchlist": {},
            "dark_zone": {},
            "sts": {},
            "behavioral": {},
            "legitimacy": {},
            "corridor": {},
            "score_bands": {},
            "ais_class": {},
            "pi_insurance": {},
            "psc_detention": {},
            "identity_merge": {},
        }

        gap, vessel = _make_gap(off_lat=60.0, off_lon=25.0, corridor_id=5)

        # Create a dark detection near the gap position, within the time window
        dark_det = _make_dark_detection(
            1, 60.01, 25.01,
            datetime(2025, 12, 1, 12, 0),
            corridor_id=5,
        )

        db = MagicMock()

        # Mock the dark vessel query to return our detection
        def query_side_effect(model):
            mock_q = MagicMock()
            from app.models.stubs import DarkVesselDetection
            if model is DarkVesselDetection:
                mock_q.filter.return_value.all.return_value = [dark_det]
            else:
                mock_q.filter.return_value.all.return_value = []
                mock_q.filter.return_value.first.return_value = None
                mock_q.filter.return_value.count.return_value = 0
            return mock_q

        db.query.side_effect = query_side_effect

        score, breakdown = compute_gap_score(gap, config, db=db)

        # The dark_vessel signal should fire (was 0 before the fix)
        assert (
            breakdown.get("dark_vessel_unmatched_in_corridor", 0) > 0
            or breakdown.get("dark_vessel_unmatched", 0) > 0
        ), f"Dark vessel signal did not fire! breakdown={breakdown}"

    def test_dark_vessel_signal_zero_when_no_detections(self):
        """Gap with no nearby dark detections → dark_vessel score = 0."""
        from app.modules.risk_scoring import compute_gap_score

        config = {
            "gap_duration": {},
            "dark_vessel": {
                "unmatched_detection_in_corridor": 35,
                "unmatched_detection_outside_corridor": 20,
            },
            "gap_frequency": {},
            "speed_anomaly": {},
            "movement_envelope": {},
            "spoofing": {},
            "metadata": {},
            "vessel_age": {},
            "flag_state": {},
            "vessel_size_multiplier": {},
            "watchlist": {},
            "dark_zone": {},
            "sts": {},
            "behavioral": {},
            "legitimacy": {},
            "corridor": {},
            "score_bands": {},
            "ais_class": {},
            "pi_insurance": {},
            "psc_detention": {},
            "identity_merge": {},
        }

        gap, vessel = _make_gap(off_lat=60.0, off_lon=25.0)

        db = MagicMock()

        def query_side_effect(model):
            mock_q = MagicMock()
            mock_q.filter.return_value.all.return_value = []
            mock_q.filter.return_value.first.return_value = None
            mock_q.filter.return_value.count.return_value = 0
            return mock_q

        db.query.side_effect = query_side_effect

        score, breakdown = compute_gap_score(gap, config, db=db)

        assert breakdown.get("dark_vessel_unmatched_in_corridor", 0) == 0
        assert breakdown.get("dark_vessel_unmatched", 0) == 0
