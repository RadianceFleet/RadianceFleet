"""Shared vessel classification utilities.

Ensures consistent vessel-class speed thresholds across gap_detector and risk_scoring.
"""
from __future__ import annotations


def classify_vessel_speed(dwt: float | None) -> tuple[float, float]:
    """Return (max_speed_kn, spoof_threshold_kn) for a given deadweight tonnage.

    Consistent classification used by both gap_detector (envelope calculation)
    and risk_scoring (speed anomaly thresholds).

    DWT ranges and speeds per IMO tanker class:
      >= 200,000  VLCC:       (18, 22) kn
      >= 120,000  Suezmax:    (19, 23) kn
      >= 80,000   Aframax:    (20, 24) kn
      >= 60,000   Panamax:    (20, 24) kn
      < 60,000 or None:       (17, 22) kn  (sub-Panamax / unknown)
    """
    if dwt is None:
        return 17.0, 22.0
    if dwt >= 200_000:
        return 18.0, 22.0
    if dwt >= 120_000:
        return 19.0, 23.0
    if dwt >= 80_000:
        return 20.0, 24.0
    if dwt >= 60_000:
        return 20.0, 24.0
    return 17.0, 22.0
