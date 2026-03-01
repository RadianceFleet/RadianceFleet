"""Phase O: Fleet & Owner Intelligence tests.

Tests owner deduplication (fuzzy matching, Cyrillic transliteration, union-find
clustering) and fleet-level behavioural analysis (STS concentration, dark
coordination, flag diversity, high risk average, shared manager, shared P&I).
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.modules.owner_dedup import run_owner_dedup, _normalize_owner_name, _UnionFind
from app.modules.fleet_analyzer import (
    run_fleet_analysis,
    _check_sts_concentration,
    _check_dark_coordination,
    _check_flag_diversity,
    _check_high_risk_average,
    _check_shared_manager_different_owners,
    _check_shared_pi_club,
    _alert_exists,
    MAX_CLUSTERS_PER_RUN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_owner(owner_id, vessel_id, owner_name, country=None, is_sanctioned=False):
    o = MagicMock()
    o.owner_id = owner_id
    o.vessel_id = vessel_id
    o.owner_name = owner_name
    o.country = country
    o.is_sanctioned = is_sanctioned
    return o


def _make_vessel(vessel_id, flag=None, owner_name=None, pi_coverage_status=None, risk_score=0):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.flag = flag
    v.owner_name = owner_name
    v.pi_coverage_status = pi_coverage_status
    # For risk scoring
    v.risk_score = risk_score
    return v


def _make_cluster(cluster_id, canonical_name="Test Cluster", is_sanctioned=False):
    c = MagicMock()
    c.cluster_id = cluster_id
    c.canonical_name = canonical_name
    c.is_sanctioned = is_sanctioned
    return c


def _make_gap(vessel_id, gap_start, risk_score=0, corridor_id=1, gap_off_lat=60.0, gap_off_lon=25.0):
    g = MagicMock()
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start
    g.risk_score = risk_score
    g.corridor_id = corridor_id
    g.gap_off_lat = gap_off_lat
    g.gap_off_lon = gap_off_lon
    return g


def _make_sts(vessel_1_id, vessel_2_id, corridor_id, start_time):
    s = MagicMock()
    s.vessel_1_id = vessel_1_id
    s.vessel_2_id = vessel_2_id
    s.corridor_id = corridor_id
    s.start_time_utc = start_time
    return s


# ===========================================================================
# Owner Dedup — Name Normalization
# ===========================================================================

class TestNormalizeOwnerName:
    def test_strip_llc(self):
        result = _normalize_owner_name("Titan LLC")
        assert "LLC" not in result
        assert "TITAN" in result

    def test_strip_ltd(self):
        result = _normalize_owner_name("Titan Ltd")
        assert "LTD" not in result
        assert "TITAN" in result

    def test_strip_ooo_latin(self):
        result = _normalize_owner_name("Titan OOO")
        assert "OOO" not in result

    def test_strip_cyrillic_ooo(self):
        result = _normalize_owner_name("Титан ООО")
        # Cyrillic ООО stripped, then transliterated
        assert "TITAN" in result

    def test_cyrillic_transliteration(self):
        result = _normalize_owner_name("СОВКОМФЛОТ")
        assert "SOVKOMFLOT" in result or "SOVCOMFLOT" in result

    def test_uppercase(self):
        result = _normalize_owner_name("titan shipping")
        assert result == "TITAN SHIPPING"

    def test_strip_punctuation(self):
        result = _normalize_owner_name("Titan (Shipping) Co.")
        # punctuation removed, CO stripped
        assert "(" not in result
        assert ")" not in result

    def test_empty_string(self):
        assert _normalize_owner_name("") == ""

    def test_none_value(self):
        assert _normalize_owner_name(None) == ""


# ===========================================================================
# Owner Dedup — Union-Find
# ===========================================================================

class TestUnionFind:
    def test_basic_union(self):
        uf = _UnionFind()
        uf.union(1, 2)
        assert uf.find(1) == uf.find(2)

    def test_transitive_merge(self):
        """A matches B, B matches C -> all in same cluster."""
        uf = _UnionFind()
        uf.union(1, 2)
        uf.union(2, 3)
        assert uf.find(1) == uf.find(3)

    def test_separate_clusters(self):
        uf = _UnionFind()
        uf.union(1, 2)
        uf.union(3, 4)
        assert uf.find(1) != uf.find(3)

    def test_singleton(self):
        uf = _UnionFind()
        root = uf.find(99)
        assert root == 99


# ===========================================================================
# Owner Dedup — run_owner_dedup
# ===========================================================================

class TestRunOwnerDedup:
    @patch("app.modules.owner_dedup.settings")
    def test_feature_flag_disabled(self, mock_settings):
        mock_settings.FLEET_ANALYSIS_ENABLED = False
        db = MagicMock()
        result = run_owner_dedup(db)
        assert result["status"] == "disabled"
        assert result["clusters_created"] == 0

    @patch("app.modules.owner_dedup.settings")
    def test_no_owners(self, mock_settings):
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        db.query.return_value.all.return_value = []
        result = run_owner_dedup(db)
        assert result["status"] == "ok"
        assert result["clusters_created"] == 0
        assert result["owners_processed"] == 0

    @patch("app.modules.owner_dedup.settings")
    def test_fuzzy_match_titan_llc_vs_titan_ltd(self, mock_settings):
        """'TITAN LLC' and 'Titan Ltd' should cluster together."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        owners = [
            _make_owner(1, 10, "TITAN LLC", "PA"),
            _make_owner(2, 20, "Titan Ltd", "LR"),
        ]
        db.query.return_value.all.return_value = owners

        result = run_owner_dedup(db)
        assert result["status"] == "ok"
        # Both should end up in the same cluster (1 cluster total)
        assert result["clusters_created"] == 1
        assert result["owners_processed"] == 2

    @patch("app.modules.owner_dedup.settings")
    def test_cyrillic_vs_latin_clusters(self, mock_settings):
        """'СОВКОМФЛОТ' and 'SOVKOMFLOT' should cluster together."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        owners = [
            _make_owner(1, 10, "СОВКОМФЛОТ"),
            _make_owner(2, 20, "SOVKOMFLOT"),
        ]
        db.query.return_value.all.return_value = owners

        result = run_owner_dedup(db)
        assert result["status"] == "ok"
        assert result["clusters_created"] == 1

    @patch("app.modules.owner_dedup.settings")
    def test_different_names_not_clustered(self, mock_settings):
        """'Alpha Shipping' and 'Beta Maritime' should NOT cluster together."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        owners = [
            _make_owner(1, 10, "Alpha Shipping"),
            _make_owner(2, 20, "Beta Maritime"),
        ]
        db.query.return_value.all.return_value = owners

        result = run_owner_dedup(db)
        assert result["status"] == "ok"
        assert result["clusters_created"] == 2

    @patch("app.modules.owner_dedup.settings")
    def test_first_letter_bucketing(self, mock_settings):
        """Owners with different first letters should never be compared."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        # These would be similar if compared, but start with different letters
        owners = [
            _make_owner(1, 10, "Alpha Corp"),
            _make_owner(2, 20, "Zalpha Corp"),
        ]
        db.query.return_value.all.return_value = owners

        result = run_owner_dedup(db)
        assert result["clusters_created"] == 2

    @patch("app.modules.owner_dedup.settings")
    def test_sanctioned_status_propagation(self, mock_settings):
        """If any member is sanctioned, cluster.is_sanctioned=True."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        owners = [
            _make_owner(1, 10, "TITAN LLC", is_sanctioned=True),
            _make_owner(2, 20, "Titan Ltd", is_sanctioned=False),
        ]
        db.query.return_value.all.return_value = owners

        added_objects = []
        db.add.side_effect = lambda obj: added_objects.append(obj)
        db.flush.side_effect = lambda: None
        # We need to actually inspect the OwnerCluster object
        # Since we mock db.add, let's check what was added
        result = run_owner_dedup(db)

        # Find the OwnerCluster among added objects
        from app.models.owner_cluster import OwnerCluster
        clusters = [o for o in added_objects if isinstance(o, OwnerCluster)]
        assert len(clusters) == 1
        assert clusters[0].is_sanctioned is True

    @patch("app.modules.owner_dedup.settings")
    def test_union_find_transitive_merging(self, mock_settings):
        """A matches B, B matches C -> all end up in same cluster."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        # Names that are close enough pairwise for transitive merge
        owners = [
            _make_owner(1, 10, "TITAN SHIPPING LLC"),
            _make_owner(2, 20, "TITAN SHIPPING LTD"),
            _make_owner(3, 30, "TITAN SHIPPING CO"),
        ]
        db.query.return_value.all.return_value = owners

        result = run_owner_dedup(db)
        assert result["clusters_created"] == 1
        assert result["owners_processed"] == 3


# ===========================================================================
# Fleet Analyzer — STS Concentration
# ===========================================================================

class TestFleetSTSConcentration:
    def test_three_vessels_same_zone_triggers(self):
        """3 vessels from same cluster in same STS zone within 30d -> alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(i, flag="PA") for i in range(1, 4)]

        base_time = datetime(2025, 6, 1)
        sts_events = [
            _make_sts(1, 100, corridor_id=5, start_time=base_time),
            _make_sts(2, 101, corridor_id=5, start_time=base_time + timedelta(days=5)),
            _make_sts(3, 102, corridor_id=5, start_time=base_time + timedelta(days=10)),
        ]
        db.query.return_value.filter.return_value.all.return_value = sts_events

        alert = _check_sts_concentration(db, cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_sts_concentration"
        assert alert.risk_score_component == 30

    def test_fewer_than_three_no_alert(self):
        """Fewer than 3 vessels -> no STS concentration alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1), _make_vessel(2)]

        alert = _check_sts_concentration(db, cluster, vessels)
        assert alert is None

    def test_different_corridors_no_alert(self):
        """3 vessels in different corridors -> no concentration alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(i) for i in range(1, 4)]

        base_time = datetime(2025, 6, 1)
        sts_events = [
            _make_sts(1, 100, corridor_id=5, start_time=base_time),
            _make_sts(2, 101, corridor_id=6, start_time=base_time + timedelta(days=5)),
            _make_sts(3, 102, corridor_id=7, start_time=base_time + timedelta(days=10)),
        ]
        db.query.return_value.filter.return_value.all.return_value = sts_events

        alert = _check_sts_concentration(db, cluster, vessels)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — Dark Coordination
# ===========================================================================

class TestFleetDarkCoordination:
    def test_three_vessels_within_48h_triggers(self):
        """3 vessels going dark within 48h -> alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(i) for i in range(1, 4)]

        base_time = datetime(2025, 6, 1)
        gaps = [
            _make_gap(1, base_time),
            _make_gap(2, base_time + timedelta(hours=12)),
            _make_gap(3, base_time + timedelta(hours=40)),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = gaps

        alert = _check_dark_coordination(db, cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_dark_coordination"
        assert alert.risk_score_component == 25

    def test_gaps_outside_window_no_alert(self):
        """Gaps spread over >48h each -> no dark coordination alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(i) for i in range(1, 4)]

        base_time = datetime(2025, 6, 1)
        gaps = [
            _make_gap(1, base_time),
            _make_gap(2, base_time + timedelta(hours=60)),
            _make_gap(3, base_time + timedelta(hours=120)),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = gaps

        alert = _check_dark_coordination(db, cluster, vessels)
        assert alert is None

    def test_fewer_than_three_no_alert(self):
        """Fewer than 3 vessels -> no dark coordination alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1), _make_vessel(2)]

        alert = _check_dark_coordination(db, cluster, vessels)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — Flag Diversity
# ===========================================================================

class TestFleetFlagDiversity:
    def test_four_flags_triggers(self):
        """4 different flags in one cluster -> alert."""
        cluster = _make_cluster(1)
        vessels = [
            _make_vessel(1, flag="PA"),
            _make_vessel(2, flag="LR"),
            _make_vessel(3, flag="CM"),
            _make_vessel(4, flag="TZ"),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is not None
        assert alert.alert_type == "fleet_flag_diversity"
        assert alert.risk_score_component == 20
        assert alert.evidence_json["flag_count"] == 4

    def test_three_flags_no_alert(self):
        """3 different flags -> no flag diversity alert."""
        cluster = _make_cluster(1)
        vessels = [
            _make_vessel(1, flag="PA"),
            _make_vessel(2, flag="LR"),
            _make_vessel(3, flag="CM"),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is None

    def test_none_flags_ignored(self):
        """Vessels with flag=None should not count toward diversity."""
        cluster = _make_cluster(1)
        vessels = [
            _make_vessel(1, flag="PA"),
            _make_vessel(2, flag=None),
            _make_vessel(3, flag=None),
            _make_vessel(4, flag=None),
        ]

        alert = _check_flag_diversity(cluster, vessels)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — High Risk Average
# ===========================================================================

class TestFleetHighRiskAverage:
    def test_avg_above_50_triggers(self):
        """Cluster average risk >50 -> alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1), _make_vessel(2)]

        gaps = [
            _make_gap(1, datetime(2025, 6, 1), risk_score=60),
            _make_gap(2, datetime(2025, 6, 1), risk_score=70),
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps

        alert = _check_high_risk_average(cluster, vessels, db)
        assert alert is not None
        assert alert.alert_type == "fleet_high_risk_average"
        assert alert.risk_score_component == 15

    def test_avg_below_50_no_alert(self):
        """Cluster average risk <=50 -> no alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1), _make_vessel(2)]

        gaps = [
            _make_gap(1, datetime(2025, 6, 1), risk_score=30),
            _make_gap(2, datetime(2025, 6, 1), risk_score=20),
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps

        alert = _check_high_risk_average(cluster, vessels, db)
        assert alert is None

    def test_no_gaps_no_alert(self):
        """No gap events for vessels -> no alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1)]
        db.query.return_value.filter.return_value.all.return_value = []

        alert = _check_high_risk_average(cluster, vessels, db)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — Shared Manager Different Owners
# ===========================================================================

class TestSharedManagerDifferentOwners:
    def test_shared_manager_triggers(self):
        """Same vessel.owner_name but different VesselOwner names -> alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [
            _make_vessel(1, owner_name="MEGA MARITIME MGMT"),
            _make_vessel(2, owner_name="MEGA MARITIME MGMT"),
        ]

        mock_owners = [
            _make_owner(10, 1, "Alpha Holdings SA"),
            _make_owner(11, 2, "Beta Shipping Corp"),
        ]
        db.query.return_value.filter.return_value.all.return_value = mock_owners

        alert = _check_shared_manager_different_owners(cluster, vessels, db)
        assert alert is not None
        assert alert.alert_type == "shared_manager_different_owners"
        assert alert.risk_score_component == 15

    def test_single_vessel_no_alert(self):
        """Single vessel -> no shared manager alert."""
        db = MagicMock()
        cluster = _make_cluster(1)
        vessels = [_make_vessel(1, owner_name="MEGA MARITIME")]

        alert = _check_shared_manager_different_owners(cluster, vessels, db)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — Shared P&I Club
# ===========================================================================

class TestSharedPIClub:
    def test_shared_pi_sanctioned_cluster_triggers(self):
        """Sanctioned cluster with 2+ vessels sharing P&I status -> alert."""
        db = MagicMock()
        cluster = _make_cluster(1, is_sanctioned=True)
        vessels = [
            _make_vessel(1, pi_coverage_status="active"),
            _make_vessel(2, pi_coverage_status="active"),
        ]

        alert = _check_shared_pi_club(cluster, vessels, db)
        assert alert is not None
        assert alert.alert_type == "shared_pi_club_high_risk"
        assert alert.risk_score_component == 10

    def test_non_sanctioned_no_alert(self):
        """Non-sanctioned cluster -> no P&I alert."""
        db = MagicMock()
        cluster = _make_cluster(1, is_sanctioned=False)
        vessels = [
            _make_vessel(1, pi_coverage_status="active"),
            _make_vessel(2, pi_coverage_status="active"),
        ]

        alert = _check_shared_pi_club(cluster, vessels, db)
        assert alert is None

    def test_unknown_status_ignored(self):
        """pi_coverage_status='unknown' should not count."""
        db = MagicMock()
        cluster = _make_cluster(1, is_sanctioned=True)
        vessels = [
            _make_vessel(1, pi_coverage_status="unknown"),
            _make_vessel(2, pi_coverage_status="unknown"),
        ]

        alert = _check_shared_pi_club(cluster, vessels, db)
        assert alert is None


# ===========================================================================
# Fleet Analyzer — run_fleet_analysis
# ===========================================================================

class TestRunFleetAnalysis:
    @patch("app.modules.fleet_analyzer.settings")
    def test_feature_flag_disabled(self, mock_settings):
        mock_settings.FLEET_ANALYSIS_ENABLED = False
        db = MagicMock()
        result = run_fleet_analysis(db)
        assert result["status"] == "disabled"
        assert result["clusters_analyzed"] == 0

    @patch("app.modules.fleet_analyzer._alert_exists", return_value=False)
    @patch("app.modules.fleet_analyzer._get_cluster_vessels")
    @patch("app.modules.fleet_analyzer.settings")
    def test_basic_run(self, mock_settings, mock_get_vessels, mock_exists):
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        cluster = _make_cluster(1)
        db.query.return_value.limit.return_value.all.return_value = [cluster]
        mock_get_vessels.return_value = [
            _make_vessel(1, flag="PA"),
            _make_vessel(2, flag="LR"),
        ]

        result = run_fleet_analysis(db)
        assert result["status"] == "ok"
        assert result["clusters_analyzed"] == 1

    @patch("app.modules.fleet_analyzer.settings")
    def test_max_clusters_limit(self, mock_settings):
        """Should respect MAX_CLUSTERS_PER_RUN limit."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()
        # Verify that .limit() is called with MAX_CLUSTERS_PER_RUN
        db.query.return_value.limit.return_value.all.return_value = []
        run_fleet_analysis(db)
        db.query.return_value.limit.assert_called_with(MAX_CLUSTERS_PER_RUN)

    @patch("app.modules.fleet_analyzer._alert_exists", return_value=True)
    @patch("app.modules.fleet_analyzer._get_cluster_vessels")
    @patch("app.modules.fleet_analyzer._check_flag_diversity")
    @patch("app.modules.fleet_analyzer._check_sts_concentration", return_value=None)
    @patch("app.modules.fleet_analyzer._check_dark_coordination", return_value=None)
    @patch("app.modules.fleet_analyzer._check_high_risk_average", return_value=None)
    @patch("app.modules.fleet_analyzer._check_shared_manager_different_owners", return_value=None)
    @patch("app.modules.fleet_analyzer._check_shared_pi_club", return_value=None)
    @patch("app.modules.fleet_analyzer.settings")
    def test_dedup_no_duplicate_alerts(
        self, mock_settings, mock_pi, mock_mgr, mock_risk, mock_dark,
        mock_sts, mock_flag, mock_get_vessels, mock_exists
    ):
        """No duplicate alerts for same cluster+type (dedup via _alert_exists)."""
        mock_settings.FLEET_ANALYSIS_ENABLED = True
        db = MagicMock()

        cluster = _make_cluster(1)
        db.query.return_value.limit.return_value.all.return_value = [cluster]
        mock_get_vessels.return_value = [
            _make_vessel(1, flag="PA"),
            _make_vessel(2, flag="LR"),
            _make_vessel(3, flag="CM"),
            _make_vessel(4, flag="TZ"),
        ]
        # Flag diversity would trigger, but _alert_exists returns True
        mock_flag.return_value = MagicMock(alert_type="fleet_flag_diversity")

        result = run_fleet_analysis(db)
        assert result["alerts_created"] == 0

    def test_alert_exists_true(self):
        """_alert_exists returns True when alert exists."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = MagicMock()
        assert _alert_exists(db, 1, "fleet_sts_concentration") is True

    def test_alert_exists_false(self):
        """_alert_exists returns False when no alert exists."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        assert _alert_exists(db, 1, "fleet_sts_concentration") is False
