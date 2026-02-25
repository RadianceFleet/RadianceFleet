# Risk Scoring Configuration

Scoring weights are defined in `config/risk_scoring.yaml`. All scores are additive and capped at 100.

## Score bands

| Score | Severity | Suggested action |
|-------|----------|-----------------|
| 0–20 | Low | Informational only |
| 21–50 | Medium | Flag for monitoring |
| 51–75 | High | Requires investigation |
| 76–100 | Critical | Immediate analyst review |

## Configuration file structure

```yaml
gap_detection:
  min_gap_minutes: 60            # Gaps shorter than this are ignored
  class_b_min_gap_minutes: 180   # Class B noise filter (legally not required <180s)
  ais_class_b_dwt_threshold: 1000 # Above this DWT, Class B is suspicious

scoring:
  # Gap frequency (only highest tier fires — subsumption logic)
  gap_7d_score: 18
  gap_14d_score: 32
  gap_30d_score: 50              # Largest window overrides smaller

  # Impossible speed
  impossible_speed_base: 25
  impossible_speed_vessel_multiplier_vlcc: 1.5   # VLCC (>200k DWT)
  impossible_speed_vessel_multiplier_suezmax: 1.3 # Suezmax (100–200k DWT)
  impossible_speed_vessel_multiplier_panamax: 0.8  # Panamax (<60k DWT)

  # Spoofing patterns
  circle_spoof_score: 35
  anchor_spoof_score: 30
  slow_roll_score: 20
  mmsi_reuse_score: 25
  nav_status_mismatch_score: 15
  erratic_nav_status_score: 20

  # Dark zone scenarios
  dark_zone_interior_adjustment: -10   # Expected noise — reduce score
  dark_zone_entry_score: 20            # Entered dark zone before gap
  dark_zone_exit_impossible_jump: 35   # Exited dark zone with impossible speed

  # Loitering
  loitering_score: 15

  # STS transfer
  sts_confirmed_score: 20
  sts_one_vessel_dark_score: 15

  # Watchlist
  watchlist_ofac_score: 30
  watchlist_kse_score: 25
  watchlist_opensanctions_score: 20
  watchlist_local_score: 15

  # Identity changes
  flag_change_score: 20
  name_change_score: 15
  imo_change_score: 25

  # Legitimacy signals (subtract from total)
  pi_coverage_discount: -10          # P&I insurance on file
  low_risk_flag_discount: -5         # EU/UK/Norway/Japan flag
  not_detained_discount: -5          # No PSC detentions
  class_a_device_discount: -5        # Class A AIS (legally required)

  # New MMSI
  new_mmsi_score: 10                 # MMSI first seen <90 days
```

## Customization

Edit `config/risk_scoring.yaml` and run:

```bash
uv run radiancefleet rescore-all-alerts
```

This clears and recomputes all existing scores. Old scores are preserved in `risk_breakdown_json` for auditability (NFR3 — reproducibility).

## NFR3: Reproducibility

Each evidence card records the `scoring_date` used when the score was computed. Running `rescore-all-alerts` with the same config on the same date will produce identical scores.

## Vessel size multipliers

Size multipliers apply to impossible speed calculations only. A VLCC appearing 2000nm away in 12 hours is more suspicious than a Panamax because:
- VLCCs move slower (max ~16 knots laden)
- VLCCs are harder to legitimately redirect at short notice
- Shadow fleet operations disproportionately involve VLCCs

DWT thresholds:
- VLCC: >200,000 DWT (1.5× multiplier)
- Suezmax: 100,000–200,000 DWT (1.3×)
- Panamax: <60,000 DWT (0.8×)
