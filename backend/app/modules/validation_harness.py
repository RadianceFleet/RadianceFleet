"""Validation harness — compare risk scoring predictions against ground truth.

Ground truth sources (KSE shadow fleet list, OFAC SDN) are *proxy* labels,
not a gold-standard maritime anomaly benchmark.  Results should be interpreted
as directional guidance for tuning, not as absolute accuracy metrics.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.ground_truth import GroundTruthVessel
from app.modules.risk_scoring import _score_band

logger = logging.getLogger(__name__)

# Band hierarchy for threshold comparison
_BAND_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _percentile(values: list[float], pct: float) -> float:
    """Compute percentile using nearest-rank method. pct in [0, 100]."""
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(math.ceil(pct / 100.0 * len(values)) - 1)))
    return values[k]


def _f_beta(precision: float, recall: float, beta: float = 2.0) -> float:
    if precision + recall == 0:
        return 0.0
    return (1 + beta**2) * precision * recall / (beta**2 * precision + recall)


def _pr_auc(precisions: list[float], recalls: list[float]) -> float:
    """Trapezoidal PR-AUC from sorted (recall, precision) pairs."""
    if len(precisions) < 2:
        return 0.0
    # Sort by recall ascending
    pairs = sorted(zip(recalls, precisions))
    auc = 0.0
    for i in range(1, len(pairs)):
        dr = pairs[i][0] - pairs[i - 1][0]
        avg_p = (pairs[i][1] + pairs[i - 1][1]) / 2.0
        auc += dr * avg_p
    return auc


def _gather_vessel_scores(db: Session) -> list[dict]:
    """Fetch ground truth vessels with their 75th-percentile gap scores."""
    gt_records = (
        db.query(GroundTruthVessel)
        .filter(GroundTruthVessel.vessel_id.isnot(None))
        .all()
    )
    results = []
    for gt in gt_records:
        gaps = (
            db.query(AISGapEvent)
            .filter(AISGapEvent.vessel_id == gt.vessel_id)
            .all()
        )
        if not gaps:
            logger.warning(
                "Ground truth vessel %s (vessel_id=%s) has no gap events — skipping",
                gt.vessel_name or gt.imo,
                gt.vessel_id,
            )
            continue

        scores = [g.risk_score for g in gaps]
        p75 = _percentile(scores, 75)

        # Collect risk breakdown keys across all gaps
        breakdown_keys: set[str] = set()
        for g in gaps:
            bd = g.risk_breakdown_json
            if isinstance(bd, str):
                try:
                    bd = json.loads(bd)
                except (json.JSONDecodeError, TypeError):
                    bd = None
            if isinstance(bd, dict):
                breakdown_keys.update(bd.keys())

        results.append({
            "vessel_id": gt.vessel_id,
            "vessel_name": gt.vessel_name,
            "imo": gt.imo,
            "source": gt.source,
            "expected_band": gt.expected_band,
            "is_shadow_fleet": gt.is_shadow_fleet,
            "p75_score": p75,
            "predicted_band": _score_band(int(p75)),
            "breakdown_keys": breakdown_keys,
            "gap_count": len(gaps),
            "score_mean": sum(scores) / len(scores),
            "score_max": max(scores),
        })
    return results


def run_validation(db: Session, threshold_band: str = "high") -> dict:
    """Run full validation against ground truth.

    A vessel is predicted as shadow fleet if its predicted_band >= threshold_band.
    Returns confusion matrix, precision, recall, F2, PR-AUC, per-source breakdown,
    and score distribution stats.
    """
    entries = _gather_vessel_scores(db)
    if not entries:
        logger.warning("No linked ground truth vessels with gap events found")
        return {"error": "no_data", "n_linked": 0}

    threshold_rank = _BAND_ORDER.get(threshold_band, 2)

    tp = fp = tn = fn = 0
    source_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
    pos_scores: list[float] = []
    neg_scores: list[float] = []

    for e in entries:
        predicted_positive = _BAND_ORDER.get(e["predicted_band"], 0) >= threshold_rank
        actual_positive = e["is_shadow_fleet"]
        src = e["source"]

        if actual_positive:
            pos_scores.append(e["p75_score"])
        else:
            neg_scores.append(e["p75_score"])

        if predicted_positive and actual_positive:
            tp += 1
            source_counts[src]["tp"] += 1
        elif predicted_positive and not actual_positive:
            fp += 1
            source_counts[src]["fp"] += 1
        elif not predicted_positive and actual_positive:
            fn += 1
            source_counts[src]["fn"] += 1
        else:
            tn += 1
            source_counts[src]["tn"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f2 = _f_beta(precision, recall, beta=2.0)

    # PR-AUC via sweep
    sweep = sweep_thresholds(db)
    pr_pairs_p = [s["precision"] for s in sweep if s["precision"] is not None]
    pr_pairs_r = [s["recall"] for s in sweep if s["recall"] is not None]
    prauc = _pr_auc(pr_pairs_p, pr_pairs_r)

    def _dist_stats(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0}
        return {
            "n": len(vals),
            "mean": sum(vals) / len(vals),
            "p25": _percentile(vals, 25),
            "p50": _percentile(vals, 50),
            "p75": _percentile(vals, 75),
            "max": max(vals),
        }

    return {
        "threshold_band": threshold_band,
        "n_evaluated": len(entries),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f2_score": round(f2, 4),
        "pr_auc": round(prauc, 4),
        "per_source": dict(source_counts),
        "score_distribution": {
            "positives": _dist_stats(pos_scores),
            "negatives": _dist_stats(neg_scores),
        },
    }


def signal_effectiveness_report(db: Session) -> list[dict]:
    """Compute lift ratio for each signal key in risk breakdowns.

    Lift = (freq_in_TP / total_TP) / (freq_in_FP / total_FP).
    Signals with lift < 1.0 are spurious — they appear more in FPs than TPs.
    """
    entries = _gather_vessel_scores(db)
    # Use high band as default threshold for TP/FP classification
    threshold_rank = _BAND_ORDER["high"]

    tp_keys: dict[str, int] = defaultdict(int)
    fp_keys: dict[str, int] = defaultdict(int)
    total_tp = 0
    total_fp = 0

    for e in entries:
        predicted_positive = _BAND_ORDER.get(e["predicted_band"], 0) >= threshold_rank
        if predicted_positive and e["is_shadow_fleet"]:
            total_tp += 1
            for k in e["breakdown_keys"]:
                tp_keys[k] += 1
        elif predicted_positive and not e["is_shadow_fleet"]:
            total_fp += 1
            for k in e["breakdown_keys"]:
                fp_keys[k] += 1

    if total_tp == 0 or total_fp == 0:
        logger.warning("Cannot compute lift: TP=%d, FP=%d", total_tp, total_fp)
        return []

    all_keys = set(tp_keys.keys()) | set(fp_keys.keys())
    report = []
    for k in all_keys:
        tp_freq = tp_keys.get(k, 0) / total_tp
        fp_freq = fp_keys.get(k, 0) / total_fp
        lift = tp_freq / fp_freq if fp_freq > 0 else float("inf") if tp_freq > 0 else 0.0
        report.append({
            "signal": k,
            "tp_freq": round(tp_freq, 4),
            "fp_freq": round(fp_freq, 4),
            "lift": round(lift, 4) if lift != float("inf") else "inf",
            "spurious": lift < 1.0,
        })

    report.sort(key=lambda x: x["lift"] if isinstance(x["lift"], (int, float)) else 9999, reverse=True)
    return report


def sweep_thresholds(db: Session) -> list[dict]:
    """Sweep score thresholds from 0 to 200 in steps of 5.

    At each threshold, a vessel is predicted positive if p75_score >= threshold.
    Returns list of dicts with threshold, precision, recall, f2_score.
    """
    entries = _gather_vessel_scores(db)
    if not entries:
        return []

    results = []
    for threshold in range(0, 201, 5):
        tp = fp = fn = 0
        for e in entries:
            predicted_positive = e["p75_score"] >= threshold
            actual_positive = e["is_shadow_fleet"]
            if predicted_positive and actual_positive:
                tp += 1
            elif predicted_positive and not actual_positive:
                fp += 1
            elif not predicted_positive and actual_positive:
                fn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f2 = _f_beta(precision or 0, recall or 0, beta=2.0)

        results.append({
            "threshold": threshold,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f2_score": round(f2, 4),
        })

    return results
