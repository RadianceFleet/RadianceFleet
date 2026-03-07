# Configuration Files

This directory contains RadianceFleet's detection and scoring configuration.

## Default vs. Production Config

Files in this directory are **tracked in git** and contain sensible defaults. They serve as both documentation and a working baseline for development and testing.

**No custom config is needed to get started** -- the defaults work out of the box.

For production deployments, you should customize scoring thresholds and whitelists. The config file paths are controlled by environment variables, so you can point to files anywhere on the filesystem.

## Production Config (Deployed Instances)

Set environment variables to point to your custom config files:

```env
RISK_SCORING_CONFIG=/etc/radiancefleet/risk_scoring.yaml
CORRIDORS_CONFIG=/etc/radiancefleet/corridors.yaml
```

Copy the defaults as a starting point, then tune:

```bash
sudo mkdir -p /etc/radiancefleet
sudo cp config/risk_scoring.yaml /etc/radiancefleet/
sudo cp config/corridors.yaml /etc/radiancefleet/
# Edit to taste, then set paths in .env
```

### Docker deployments

Mount your config directory as a volume:

```yaml
# docker-compose.yml
services:
  web:
    volumes:
      - ./my-config:/etc/radiancefleet:ro
    environment:
      RISK_SCORING_CONFIG: /etc/radiancefleet/risk_scoring.yaml
      CORRIDORS_CONFIG: /etc/radiancefleet/corridors.yaml
```

Or with `docker run`:

```bash
docker run -v ./my-config:/etc/radiancefleet:ro \
  -e RISK_SCORING_CONFIG=/etc/radiancefleet/risk_scoring.yaml \
  -e CORRIDORS_CONFIG=/etc/radiancefleet/corridors.yaml \
  radiancefleet
```

### PaaS deployments (Render, Railway, Fly.io)

These platforms don't support volume mounts. The baked-in defaults work out of the box. For targeted tuning, use env var overrides:

```env
# Patch specific scoring thresholds (JSON, merged on top of YAML defaults)
SCORING_OVERRIDES={"gap_duration":{"24h_plus":60},"corridor":{"sts_zone":2.0}}

# Add extra whitelisted MMSIs (comma-separated, merged with legitimate_operators.yaml)
EXTRA_WHITELISTED_MMSIS=219010207,224006160,265609960
```

This avoids migrating 200+ nested YAML values to flat env vars while still allowing the most operationally sensitive values to be customized per instance.

### Local development

For local dev/testing, you can also use `*.local.yaml` files (gitignored):

```env
RISK_SCORING_CONFIG=config/risk_scoring.local.yaml
CORRIDORS_CONFIG=config/corridors.local.yaml
```

## Why this matters

The detection algorithms are open source by design -- transparency is RadianceFleet's competitive advantage for investigative journalism credibility. However, **deployment-specific threshold calibration** should remain private:

- **Scoring thresholds**: Tuned values make evasion harder when they differ per instance
- **Operator whitelists**: MMSI lists are instance-specific false positive suppression
- **Corridor definitions**: Custom corridors may reveal monitoring focus areas
- **Exclusion zones**: Bunkering and anchorage exclusions are operationally sensitive

The default files in git provide a working baseline. Your production tuning stays outside the repo.

## File Reference

### Customizable via env var path (high priority for production)

These files are loaded relative to `RISK_SCORING_CONFIG` or `CORRIDORS_CONFIG` -- setting those env vars automatically picks up all related files from the same directory:

| File | Purpose | Sensitive? |
|------|---------|-----------|
| `risk_scoring.yaml` | Scoring weights for all 35+ signal sections | **High** -- thresholds are operationally sensitive |
| `corridors.yaml` | Monitored corridor definitions with multipliers | **High** -- reveals monitoring focus |
| `legitimate_operators.yaml` | MMSI whitelist for FP suppression | **High** -- instance-specific |
| `legitimate_pi_clubs.yaml` | P&I club names for validation | Low |
| `fraudulent_registries.yaml` | Known fraudulent flag registries | Low |

### Use defaults from repo (lower sensitivity)

These files are currently loaded from hardcoded paths relative to the source tree. The git defaults are typically sufficient:

| File | Purpose | Sensitive? |
|------|---------|-----------|
| `bunkering_exclusions.yaml` | STS FP suppression zones | Moderate |
| `laundering_intermediaries.yaml` | Route laundering waypoints | Moderate |
| `scrapped_vessels.yaml` | Known scrapped IMOs for reuse detection | Low |
| `vessel_filter.yaml` | Vessel type filtering rules | Low |
| `coverage.yaml` | Regional AIS coverage quality ratings | Low |
