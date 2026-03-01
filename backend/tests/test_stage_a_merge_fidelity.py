"""Stage A — Merge fidelity fix tests.

Covers:
  A1: fingerprint_merge_bonus wiring in _score_candidate
  A2: Fuzzy name/callsign matching
  A3: Extended merge pass require_identity_anchor
  A4: SAR correlator threshold fixes
  A5: Confidence classifier multi-gap aggregation
  A6: Owner dedup sorted-token bucketing
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from collections import defaultdict

# ── A1: Fingerprint merge bonus ──────────────────────────────────────────────

def test_fingerprint_bonus_wired_in_score_candidate():
    """_score_candidate calls fingerprint_merge_bonus when FINGERPRINT_ENABLED."""
    from app.modules import identity_resolver
    import inspect
    src = inspect.getsource(identity_resolver._score_candidate)
    assert "fingerprint_merge_bonus" in src, \
        "fingerprint_merge_bonus should be called in _score_candidate"
    assert "FINGERPRINT_ENABLED" in src, \
        "fingerprint_merge_bonus should be gated on FINGERPRINT_ENABLED"


# ── A2: Fuzzy name/callsign matching ────────────────────────────────────────

def test_fuzzy_name_matching_in_score_candidate():
    """_score_candidate has fuzzy name matching code."""
    from app.modules import identity_resolver
    import inspect
    src = inspect.getsource(identity_resolver._score_candidate)
    assert "token_sort_ratio" in src, "Should use rapidfuzz token_sort_ratio"
    assert "similar_name" in src, "Should produce similar_name reason key"


def test_callsign_matching_in_score_candidate():
    """_score_candidate has callsign matching code."""
    from app.modules import identity_resolver
    import inspect
    src = inspect.getsource(identity_resolver._score_candidate)
    assert "same_callsign" in src, "Should produce same_callsign reason key"


# ── A3: Extended merge pass pre-filter ───────────────────────────────────────

@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.modules.identity_resolver.settings")
def test_extended_pass_requires_identity_anchor(mock_settings, mock_detect):
    """Extended pass passes require_identity_anchor=True."""
    mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
    mock_detect.return_value = {"candidates_created": 0, "auto_merged": 0, "skipped": 0}
    db = MagicMock()

    from app.modules.identity_resolver import extended_merge_pass
    extended_merge_pass(db)

    mock_detect.assert_called_once_with(
        db, max_gap_days=180, require_identity_anchor=True,
    )


def test_detect_merge_candidates_accepts_anchor_param():
    """detect_merge_candidates accepts require_identity_anchor param."""
    from app.modules.identity_resolver import detect_merge_candidates
    import inspect
    sig = inspect.signature(detect_merge_candidates)
    assert "require_identity_anchor" in sig.parameters


# ── A4: SAR correlator threshold fixes ───────────────────────────────────────

def test_sar_auto_link_threshold_is_40():
    """AUTO_LINK_THRESHOLD should be 40 (was 70)."""
    from app.modules.sar_correlator import AUTO_LINK_THRESHOLD
    assert AUTO_LINK_THRESHOLD == 40.0


def test_sar_candidate_threshold_is_25():
    """CANDIDATE_THRESHOLD should be 25 (was 40)."""
    from app.modules.sar_correlator import CANDIDATE_THRESHOLD
    assert CANDIDATE_THRESHOLD == 25.0


def test_sar_heading_no_data_partial_credit():
    """SAR heading code defaults to 0, gives +5 for no heading data (not +10)."""
    from app.modules import sar_correlator
    import inspect
    src = inspect.getsource(sar_correlator._score_vessel_match)
    # Verify heading defaults to 0 (not HEADING_WEIGHT)
    assert "heading_score = 0" in src or "heading_score = 0.0" in src, \
        "Heading score should default to 0"
    # Verify partial credit for missing heading
    assert "HEADING_WEIGHT / 2" in src or "HEADING_WEIGHT // 2" in src, \
        "Should give half credit when SAR has no heading"


# ── A5: Confidence classifier aggregation ────────────────────────────────────

def test_classifier_aggregates_all_gaps():
    """classify_all_vessels should aggregate breakdowns across all scored gaps."""
    from app.modules import confidence_classifier
    import inspect
    src = inspect.getsource(confidence_classifier.classify_all_vessels)
    assert "merged_breakdown" in src or "merge" in src.lower(), \
        "Should merge breakdowns across gaps"
    assert "_multi_gap_frequency_bonus" in src, \
        "Should add multi_gap_frequency_bonus for 5+ gaps"


def test_categorize_key_multi_gap_bonus():
    """_multi_gap_frequency_bonus should be categorized (or skipped as internal)."""
    from app.modules.confidence_classifier import _categorize_key
    # Internal keys starting with _ are skipped in classify_vessel_confidence
    # This tests that the key doesn't cause errors
    cat = _categorize_key("_multi_gap_frequency_bonus")
    # Internal keys should fall through to default AIS_GAP, but they're skipped
    # by the isinstance/value check in the classifier anyway


# ── A6: Owner dedup bucketing ────────────────────────────────────────────────

def test_owner_dedup_sorted_token_bucketing():
    """owner_dedup uses sorted-first-token bucketing, not first-letter."""
    from app.modules import owner_dedup
    import inspect
    src = inspect.getsource(owner_dedup.run_owner_dedup)
    assert "sorted" in src, "Should use sorted tokens for bucket key"
    assert "tokens" in src or "sorted(norm.split())" in src, \
        "Should split name into tokens and sort"


def test_owner_dedup_cleanup():
    """owner_dedup cleans up old clusters before re-running."""
    from app.modules import owner_dedup
    import inspect
    src = inspect.getsource(owner_dedup.run_owner_dedup)
    assert "delete" in src.lower(), "Should delete old clusters before creating new ones"


def test_normalize_owner_name_permutation():
    """Normalized names should produce same sorted-token bucket."""
    from app.modules.owner_dedup import _normalize_owner_name
    a = _normalize_owner_name("MARITIME ALPINE SOLUTIONS")
    b = _normalize_owner_name("SOLUTIONS ALPINE MARITIME")
    # Both should normalize to the same tokens (modulo corporate suffix stripping)
    a_sorted = sorted(a.split()) if a else []
    b_sorted = sorted(b.split()) if b else []
    assert a_sorted == b_sorted, \
        f"Sorted tokens should match: {a_sorted} vs {b_sorted}"
    # Both should land in the same bucket
    bucket_a = a_sorted[0] if a_sorted else ""
    bucket_b = b_sorted[0] if b_sorted else ""
    assert bucket_a == bucket_b, "Should land in same bucket"
