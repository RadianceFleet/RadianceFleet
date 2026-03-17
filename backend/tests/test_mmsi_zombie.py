"""Tests for MMSI Zombie Vessel Detection (v4.0).

Covers:
  - detect_mmsi_zombie_reuse with known scrapped MMSI
  - No match when MMSI not in registry
  - Different IMO -> higher score
  - Disabled flag
  - YAML loading of scrapped_mmsis section
  - SpoofingAnomaly creation
  - Duplicate detection (don't create if already exists)
  - Empty registry
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ── Disabled flag ────────────────────────────────────────────────────────────


class TestMmsiZombieDisabled:
    """When MMSI_ZOMBIE_DETECTION_ENABLED is False, detection returns early."""

    def test_disabled_flag_returns_early(self):
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = False
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            db = MagicMock()
            result = detect_mmsi_zombie_reuse(db)
            assert result["status"] == "disabled"
            db.query.assert_not_called()


# ── Detection with known scrapped MMSI ───────────────────────────────────────


class TestMmsiZombieDetection:
    """MMSI zombie matched and unmatched vessel scenarios."""

    def test_vessel_with_scrapped_mmsi_detected(self):
        """A vessel whose MMSI matches the scrapped registry gets an anomaly."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "538006890"
        mock_vessel.imo = "9145012"  # Same IMO as scrapped record

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                # query(Vessel).filter(...).all() -> vessels list
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                # query(SpoofingAnomaly).filter(...).all() -> no existing anomalies
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            result = detect_mmsi_zombie_reuse(mock_db)
            assert result["status"] == "ok"
            assert result["anomalies_created"] == 1
            assert result["matches"] == 1
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_vessel_with_clean_mmsi_not_flagged(self):
        """A vessel with an MMSI not in the scrapped registry is not flagged."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "999999999"
        mock_vessel.imo = "9999999"

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vessel]

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            result = detect_mmsi_zombie_reuse(mock_db)
            assert result["status"] == "ok"
            assert result["matches"] == 0
            assert result["anomalies_created"] == 0
            mock_db.add.assert_not_called()


# ── Different IMO -> higher score ────────────────────────────────────────────


class TestMmsiZombieDifferentImo:
    """When vessel has a different IMO than the scrapped record, score is higher."""

    def test_different_imo_higher_score(self):
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "538006890"
        mock_vessel.imo = "9999999"  # Different IMO than scrapped record

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            result = detect_mmsi_zombie_reuse(mock_db)
            assert result["anomalies_created"] == 1

            # Verify the anomaly was created with score 50 (different IMO)
            added_anomaly = mock_db.add.call_args[0][0]
            assert added_anomaly.risk_score_component == 50
            assert added_anomaly.evidence_json["different_imo"] is True


# ── YAML loading ─────────────────────────────────────────────────────────────


class TestMmsiYamlLoading:
    """Test that scrapped_mmsis section is loaded correctly from YAML."""

    def test_load_scrapped_mmsis_from_yaml(self):
        yaml_content = {
            "scrapped_mmsis": [
                {
                    "mmsi": "538006890",
                    "name": "OCEAN PRIDE",
                    "imo": "9145012",
                    "year_scrapped": 2021,
                    "notes": "Broken at Alang",
                },
                {
                    "mmsi": "354789000",
                    "name": "GULF NAVIGATOR",
                    "imo": "9078532",
                    "year_scrapped": 2020,
                    "notes": "Demolished at Gadani",
                },
            ]
        }

        import app.modules.scrapped_registry as mod

        mod._SCRAPPED_MMSIS = None

        with patch("app.modules.scrapped_registry.Path") as mock_path:
            mock_path.return_value.exists.return_value = True
            with patch(
                "builtins.open",
                create=True,
            ) as mock_file:
                mock_file.return_value.__enter__ = lambda s: s
                mock_file.return_value.__exit__ = MagicMock(return_value=False)
                with patch(
                    "app.modules.scrapped_registry.yaml.safe_load",
                    return_value=yaml_content,
                ):
                    result = mod._load_scrapped_mmsis()

        assert len(result) == 2
        assert "538006890" in result
        assert result["538006890"]["name"] == "OCEAN PRIDE"
        assert result["354789000"]["imo"] == "9078532"

        # Clean up
        mod._SCRAPPED_MMSIS = None

    def test_load_scrapped_mmsis_file_missing(self):
        import app.modules.scrapped_registry as mod

        mod._SCRAPPED_MMSIS = None

        with patch("app.modules.scrapped_registry.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = mod._load_scrapped_mmsis()

        assert result == {}
        mod._SCRAPPED_MMSIS = None


# ── SpoofingAnomaly creation ─────────────────────────────────────────────────


class TestMmsiZombieAnomalyCreation:
    """Test that the SpoofingAnomaly object is created correctly."""

    def test_anomaly_has_correct_fields(self):
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 42
        mock_vessel.mmsi = "538006890"
        mock_vessel.imo = "9145012"

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            detect_mmsi_zombie_reuse(mock_db)

            added_anomaly = mock_db.add.call_args[0][0]
            assert added_anomaly.vessel_id == 42
            ev = added_anomaly.evidence_json
            assert ev["subtype"] == "mmsi_zombie"
            assert ev["scrapped_mmsi"] == "538006890"
            assert ev["scrapped_vessel_name"] == "OCEAN PRIDE"
            assert ev["year_scrapped"] == 2021


# ── Duplicate detection ──────────────────────────────────────────────────────


class TestMmsiZombieDuplicateDetection:
    """Don't create a new anomaly if one already exists for same vessel+subtype."""

    def test_existing_anomaly_skipped(self):
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "538006890"
        mock_vessel.imo = "9145012"

        existing_anomaly = MagicMock()
        existing_anomaly.evidence_json = {"subtype": "mmsi_zombie"}

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                # Existing anomaly found
                mock_q.filter.return_value.all.return_value = [existing_anomaly]
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            result = detect_mmsi_zombie_reuse(mock_db)
            assert result["matches"] == 1
            assert result["anomalies_created"] == 0
            mock_db.add.assert_not_called()


# ── Empty registry ───────────────────────────────────────────────────────────


class TestMmsiZombieEmptyRegistry:
    """When the scrapped MMSI registry is empty, no detection occurs."""

    def test_empty_registry_returns_zero(self):
        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value={},
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            db = MagicMock()
            result = detect_mmsi_zombie_reuse(db)
            assert result["status"] == "ok"
            assert result["matches"] == 0
            assert result["anomalies_created"] == 0
            db.query.assert_not_called()


# ── Same IMO -> base score ───────────────────────────────────────────────────


class TestMmsiZombieSameImo:
    """When vessel has the same IMO as scrapped record, score is base (45)."""

    def test_same_imo_base_score(self):
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "538006890"
        mock_vessel.imo = "9145012"  # Same IMO

        registry = {
            "538006890": {
                "name": "OCEAN PRIDE",
                "imo": "9145012",
                "year_scrapped": 2021,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with (
            patch("app.modules.scrapped_registry.settings") as mock_settings,
            patch(
                "app.modules.scrapped_registry._load_scrapped_mmsis",
                return_value=registry,
            ),
        ):
            mock_settings.MMSI_ZOMBIE_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_mmsi_zombie_reuse

            detect_mmsi_zombie_reuse(mock_db)

            added_anomaly = mock_db.add.call_args[0][0]
            assert added_anomaly.risk_score_component == 45
            assert added_anomaly.evidence_json["different_imo"] is False
