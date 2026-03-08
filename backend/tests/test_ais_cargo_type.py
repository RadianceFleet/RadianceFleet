"""Tests for AIS cargo type parsing and mismatch scoring (5B)."""

from __future__ import annotations

from app.modules.cargo_inference import (
    parse_ais_cargo_type,
    score_cargo_type_mismatch,
)

# ---------- parse_ais_cargo_type ----------


class TestParseAisCargoType:
    def test_tanker_general(self):
        assert parse_ais_cargo_type(80) == "tanker_general"

    def test_tanker_dg_a(self):
        assert parse_ais_cargo_type(81) == "tanker_dg_a"

    def test_tanker_dg_d(self):
        assert parse_ais_cargo_type(84) == "tanker_dg_d"

    def test_cargo_general(self):
        assert parse_ais_cargo_type(70) == "cargo_general"

    def test_cargo_dg_b(self):
        assert parse_ais_cargo_type(72) == "cargo_dg_b"

    def test_cargo_no_additional(self):
        assert parse_ais_cargo_type(79) == "cargo_no_additional"

    def test_tanker_no_additional(self):
        assert parse_ais_cargo_type(89) == "tanker_no_additional"

    def test_none_input(self):
        assert parse_ais_cargo_type(None) is None

    def test_zero_code(self):
        assert parse_ais_cargo_type(0) is None

    def test_non_cargo_code(self):
        """Passenger vessel (60) should return None — not cargo/tanker."""
        assert parse_ais_cargo_type(60) is None

    def test_fishing_vessel(self):
        assert parse_ais_cargo_type(30) is None

    def test_out_of_range(self):
        assert parse_ais_cargo_type(99) is None

    def test_string_numeric_input(self):
        """Should handle string-wrapped integers."""
        assert parse_ais_cargo_type(80) == "tanker_general"

    def test_invalid_string(self):
        assert parse_ais_cargo_type("not_a_number") is None

    def test_all_cargo_codes_covered(self):
        """All codes 70-79 should map to cargo_* types."""
        for code in range(70, 80):
            result = parse_ais_cargo_type(code)
            assert result is not None
            assert result.startswith("cargo_")

    def test_all_tanker_codes_covered(self):
        """All codes 80-89 should map to tanker_* types."""
        for code in range(80, 90):
            result = parse_ais_cargo_type(code)
            assert result is not None
            assert result.startswith("tanker_")


# ---------- score_cargo_type_mismatch ----------


class TestScoreCargoTypeMismatch:
    def test_no_cargo_type(self):
        """No cargo type -> empty result (no mismatch possible)."""
        assert score_cargo_type_mismatch(None, "laden") == {}

    def test_tanker_laden_no_mismatch(self):
        """Tanker that is laden -> no mismatch."""
        result = score_cargo_type_mismatch("tanker_general", "laden")
        assert result == {}

    def test_tanker_ballast_mismatch(self):
        """Tanker always in ballast -> suspicious."""
        result = score_cargo_type_mismatch("tanker_general", "ballast")
        assert result["mismatch"] == "tanker_always_ballast"
        assert result["score"] == 10

    def test_tanker_dg_a_ballast(self):
        """DG category tanker in ballast -> also flagged."""
        result = score_cargo_type_mismatch("tanker_dg_a", "ballast")
        assert result["score"] == 10
        assert result["ais_cargo_type"] == "tanker_dg_a"

    def test_cargo_at_oil_terminal(self):
        """Cargo vessel visiting oil terminals -> mismatch."""
        result = score_cargo_type_mismatch(
            "cargo_general", None, port_types=["oil_terminal", "container"]
        )
        assert result["mismatch"] == "cargo_at_oil_terminal"
        assert result["score"] == 10
        assert result["oil_terminal_visits"] == 1

    def test_cargo_at_dry_bulk_no_mismatch(self):
        """Cargo vessel at dry bulk port -> no mismatch."""
        result = score_cargo_type_mismatch(
            "cargo_general", None, port_types=["dry_bulk", "container"]
        )
        assert result == {}

    def test_cargo_no_ports(self):
        """Cargo vessel with no port visits -> no mismatch."""
        result = score_cargo_type_mismatch("cargo_general", "laden", port_types=[])
        assert result == {}

    def test_tanker_at_oil_terminal_no_double_flag(self):
        """Tanker at oil terminal is normal, should not flag cargo_at_oil_terminal."""
        result = score_cargo_type_mismatch("tanker_general", "laden", port_types=["oil_terminal"])
        assert result == {}

    def test_unknown_cargo_type_string(self):
        """Unknown cargo type string -> empty result."""
        result = score_cargo_type_mismatch("unknown_type", "laden")
        assert result == {}

    def test_cargo_multiple_oil_visits(self):
        """Multiple oil terminal visits should be counted."""
        result = score_cargo_type_mismatch(
            "cargo_dg_b", None, port_types=["oil_terminal", "tanker_berth", "container"]
        )
        assert result["oil_terminal_visits"] == 2
