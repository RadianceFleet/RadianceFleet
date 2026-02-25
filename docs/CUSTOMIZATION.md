# Customization Guide

This guide shows analysts and operators how to tune RadianceFleet for specific
investigation contexts. All scoring weights live in `config/risk_scoring.yaml`;
corridor definitions live in `config/corridors.yaml`. Neither file requires
code changes — edit, save, and re-score.

After any configuration change, always run:

```bash
radiancefleet rescore-all-alerts
```

This clears cached scores and recomputes from the updated weights. The command
logs a short config hash so you can verify which version of `risk_scoring.yaml`
was applied.

---

## Scenario 1: "Focus on Black Sea tankers"

The Black Sea has poor free AIS coverage and active GPS jamming near Crimea.
The default configuration is calibrated for the Baltic and Mediterranean. To
shift sensitivity toward Black Sea operations:

### Step 1 — Increase watchlist weight

Edit `config/risk_scoring.yaml`:

```yaml
watchlist:
  vessel_on_ofac_sdn_list: 50      # default — keep or increase
  vessel_on_kse_shadow_fleet_list: 40   # increase from 30 (KSE list covers many Black Sea operators)
  owner_or_manager_on_sanctions_list: 45  # increase from 35
```

### Step 2 — Add a Black Sea STS zone corridor

Edit `config/corridors.yaml` and add:

```yaml
  - name: "Black Sea STS — Odesa / Constanta Approaches"
    geometry: "POLYGON((29.5 45.0, 32.0 45.0, 32.0 46.5, 29.5 46.5, 29.5 45.0))"
    corridor_type: sts_zone
    risk_weight: 1.9
    is_jamming_zone: false
    description: "Black Sea STS activity near Odesa and Constanta. Confirm with satellite before reporting."
    tags: [ship_to_ship, black_sea]
```

A `risk_weight: 1.9` is slightly below the Mediterranean STS zones (2.0) to
account for the lower AIS reliability in this region.

Import the updated corridors:

```bash
radiancefleet corridors import config/corridors.yaml
```

### Step 3 — Reduce minimum gap threshold for Suezmax class

Suezmax tankers (DWT ~120,000–200,000) dominate the Black Sea Russian crude
trade. They transit at lower SOG than smaller vessels, so the default gap
duration thresholds may under-score short but meaningful gaps.

In `config/risk_scoring.yaml`, the `gap_duration` section controls this. The
thresholds are hard-coded in hours in the gap detector, but the *scoring* can
be shifted by lowering the `4h_to_8h` weight to catch borderline cases:

```yaml
gap_duration:
  2h_to_4h: 8         # increased from 5 (flag shorter gaps for this region)
  4h_to_8h: 15        # increased from 12
  8h_to_12h: 25       # unchanged
  12h_to_24h: 40      # unchanged
  24h_plus: 55        # unchanged
```

Note: if you need per-class gap thresholds (e.g. 4h for Suezmax instead of 8h),
that requires a code change in `backend/app/modules/gap_detector.py`. See
CONTRIBUTING.md section 7 for the signal-addition walkthrough.

### Step 4 — Re-score

```bash
radiancefleet rescore-all-alerts
```

---

## Scenario 2: "Reduce false positives in the Baltic"

The Baltic has the best free AIS coverage, but it also generates many Class B
noise gaps (fishing vessels, small ferries) and legitimate tanker anchorages
outside Primorsk. If your alert queue is filling with low-confidence noise:

### Step 1 — Lower gap frequency weights for short windows

```yaml
gap_frequency:
  2_gaps_in_7d: 10    # reduced from 18 (common for vessels calling at anchor)
  3_gaps_in_14d: 25   # reduced from 32
  5_gaps_in_30d: 50   # keep — a vessel with 5 gaps in 30 days is still suspicious
```

### Step 2 — Understand the Class B noise filter

The gap detector already suppresses AIS gaps shorter than 180 seconds for Class B
vessels (`ais_class_b_noise_filter_secs = 180` in
`backend/app/modules/gap_detector.py`). This filters burst-mode Class B
retransmissions. If you are seeing gaps from very small vessels (DWT < 500),
you can also raise the `large_tanker_using_class_b` score to separate them
from genuine shadow fleet candidates:

```yaml
ais_class:
  large_tanker_using_class_b: 50     # keep high — penalises large vessels on Class B
  class_switching_a_to_b: 25        # keep
  transmission_frequency_mismatch: 5  # reduce from 8 (less diagnostic in Baltic)
```

### Step 3 — Increase legitimacy rewards for Baltic-pattern vessels

```yaml
legitimacy:
  gap_free_90d_clean: -20         # more reward for clean history (was -15)
  consistent_eu_port_calls: -8    # was -5; Baltic vessels frequently call EU ports
  ais_class_a_consistent: -8     # was -5
```

### Step 4 — Re-score after tuning

```bash
radiancefleet detect-gaps && radiancefleet rescore-all-alerts
```

Running `detect-gaps` first ensures any newly-suppressed gaps are re-evaluated
with the current thresholds before scoring.

---

## How to add a custom signal

The scoring engine is designed for extension. New signals follow the pattern
established by the 12 existing signal categories in `risk_scoring.py`.

The full walkthrough is in [CONTRIBUTING.md](../CONTRIBUTING.md) section 7,
which covers:

1. Declaring the weight in `config/risk_scoring.yaml`
2. Adding the scoring phase to `backend/app/modules/risk_scoring.py`
   (following the Phase 6.13 pattern — append as the next numbered phase)
3. Writing positive and negative test cases in `tests/test_risk_scoring_complete.py`
4. Running `radiancefleet rescore-all-alerts` after deployment

The `breakdown` dict that each phase writes to is what drives the explainability
UI — descriptive key names appear directly in the evidence card output.

---

## Understanding `config/risk_scoring.yaml`

### Weight system

Scores are additive: each signal that fires contributes its declared points to a
running subtotal. After all additive signals are summed, two multipliers are applied:

```
final_score = round(additive_subtotal x corridor_factor x vessel_size_factor)
```

- `corridor_factor` — drawn from the `corridor:` section; 2.0 for STS zones,
  1.5 for high-risk export corridors
- `vessel_size_factor` — drawn from `vessel_size_multiplier:`; 1.5 for VLCC,
  0.8 for Panamax (larger vessels carry higher geopolitical risk)

Negative weights (the `legitimacy:` section) reduce the subtotal before
multipliers are applied, so they are most effective when used alongside strong
positive signals.

### Score bands

| Band | Range | Meaning |
|------|-------|---------|
| Low | 0–20 | No action needed |
| Medium | 21–50 | Investigate — check satellite data |
| High | 51–75 | High confidence anomaly — publication-ready with analyst review |
| Critical | 76+ | Strong shadow fleet indicators — escalate |

### `scoring_date` parameter (NFR3 reproducibility)

The CLI and API both accept a `--scoring-date` flag (ISO format, e.g.
`2024-06-01`). When set, the scoring engine uses that date as "now" for all
rolling-window calculations (gap frequency, 30d/60d laid-up, flag changes in
last 7/30d). This allows you to reproduce the exact score a vessel would have
received at a point in time — critical for audit trails and peer review.

```bash
radiancefleet rescore-all-alerts --scoring-date 2024-06-01
```

### Where the config is loaded

`backend/app/modules/risk_scoring.py` loads the YAML once at module import via
`load_scoring_config()`. The path defaults to `config/risk_scoring.yaml`
relative to the project root, but can be overridden with the
`RISK_SCORING_CONFIG` environment variable for testing or multi-instance
deployments:

```bash
RISK_SCORING_CONFIG=/path/to/my_custom_scoring.yaml radiancefleet rescore-all-alerts
```
