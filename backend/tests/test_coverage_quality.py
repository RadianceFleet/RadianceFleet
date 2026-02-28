"""Tests for H2: Coverage quality mapping from corridor name to coverage.yaml."""
from app.api.routes import _get_coverage_quality


class TestCoverageQuality:
    def test_baltic_returns_good(self):
        assert _get_coverage_quality("Baltic Export Gate") == "GOOD"

    def test_fujairah_returns_none(self):
        # "fujairah" matches Persian Gulf, quality=NONE
        assert _get_coverage_quality("Fujairah Anchorage (UAE)") == "NONE"

    def test_cape_verde_returns_unknown(self):
        # No matching keyword for "Cape Verde"
        assert _get_coverage_quality("Cape Verde / South Atlantic STS") == "UNKNOWN"

    def test_diacritic_normalized(self):
        # unidecode normalizes "Lome" (West Africa) -- no matching keyword
        result = _get_coverage_quality("Lom\u00e9 (West Africa)")
        assert result == "UNKNOWN"

    def test_nakhodka_precedence_over_far_east(self):
        # "Nakhodka / Kozmino" -- Nakhodka appears first in match order
        result = _get_coverage_quality("Nakhodka / Kozmino")
        assert result == "PARTIAL"

    def test_turkish_straits_returns_good(self):
        assert _get_coverage_quality("Turkish Straits Zone") == "GOOD"

    def test_black_sea_returns_poor(self):
        assert _get_coverage_quality("Black Sea Transit") == "POOR"

    def test_singapore_returns_partial(self):
        assert _get_coverage_quality("Singapore Strait") == "PARTIAL"

    def test_mediterranean_returns_moderate(self):
        assert _get_coverage_quality("Mediterranean STS Laconian") == "MODERATE"

    def test_empty_string(self):
        assert _get_coverage_quality("") == "UNKNOWN"

    def test_primorsk_maps_to_baltic(self):
        assert _get_coverage_quality("Primorsk Terminal") == "GOOD"
