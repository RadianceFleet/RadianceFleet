"""Tests for STS network association scoring signal."""
from unittest.mock import MagicMock

import pytest

from app.modules.risk_scoring import _sts_with_watchlisted_vessel


def _make_vessel(vessel_id=1):
    v = MagicMock()
    v.vessel_id = vessel_id
    return v


def _make_sts_event(vessel_1_id, vessel_2_id):
    sts = MagicMock()
    sts.vessel_1_id = vessel_1_id
    sts.vessel_2_id = vessel_2_id
    return sts


def _make_watchlist_entry(source, is_active=True):
    w = MagicMock()
    w.watchlist_source = source
    w.is_active = is_active
    return w


class TestSTSAssociation:
    def test_sts_with_sanctioned_vessel_scores_30(self):
        """Vessel doing STS with OFAC-sanctioned partner → 30 pts."""
        vessel = _make_vessel(vessel_id=1)

        # STS event: vessel 1 + vessel 2
        sts = _make_sts_event(1, 2)

        # Watchlist entry on vessel 2 (the partner)
        watchlist = _make_watchlist_entry("OFAC_SDN")

        db = MagicMock()
        # First query: STSEvents for our vessel
        db.query.return_value.filter.return_value.all.side_effect = [
            [sts],      # STS events
            [watchlist], # Watchlist entries for partner
        ]

        pts, source = _sts_with_watchlisted_vessel(db, vessel)

        assert pts == 30
        assert source == "OFAC_SDN"

    def test_sts_with_non_watchlisted_vessel_no_signal(self):
        """Vessel doing STS with clean partner → 0 pts."""
        vessel = _make_vessel(vessel_id=1)

        # STS event: vessel 1 + vessel 2
        sts = _make_sts_event(1, 2)

        db = MagicMock()
        # First query returns STS events, second returns empty watchlist
        db.query.return_value.filter.return_value.all.side_effect = [
            [sts],  # STS events
            [],     # No watchlist entries for partner
        ]

        pts, source = _sts_with_watchlisted_vessel(db, vessel)

        assert pts == 0
        assert source is None

    def test_no_sts_events_no_signal(self):
        """Vessel with no STS events → 0 pts."""
        vessel = _make_vessel(vessel_id=1)

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        pts, source = _sts_with_watchlisted_vessel(db, vessel)

        assert pts == 0
        assert source is None
