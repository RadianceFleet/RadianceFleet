# Corridor Configuration

Corridors define the geographic areas RadianceFleet monitors. They are stored in `config/corridors.yaml` and can be imported or managed via the API and CLI.

## YAML schema

```yaml
corridors:
  - name: "Baltic Export Route"
    corridor_type: import_route     # see type enum below
    risk_weight: 1.2                # multiplies all gap scores in this corridor
    is_jamming_zone: false          # if true, reduces gap scores by 10 pts
    description: "Russian Baltic oil export via Finnish Gulf"
    geometry_wkt: "POLYGON((...))"  # WGS84, SRID 4326
```

### `corridor_type` enum

| Value | Meaning |
|-------|---------|
| `import_route` | Major AIS route — gaps are inherently suspicious |
| `sts_zone` | Known ship-to-ship transfer anchorage area |
| `dark_zone` | GPS/AIS jamming zone — increases false positive rate |
| `chokepoint` | Maritime chokepoint (straits, canals) |
| `anchorage` | Port anchorage area — reduce anchor-spoof false positives |

### `risk_weight`

Multiplies all gap risk scores for alerts within this corridor.

- 1.0 = neutral (default)
- 1.5 = high-risk zone (e.g., known STS area off Ceuta)
- 0.5 = lower confidence area

### `is_jamming_zone`

When `true`, gap scores in this corridor are reduced by 10 points to account for expected AIS disruption. Use for corridors near Russian jamming infrastructure (Black Sea coast, Kaliningrad area).

## CLI usage

```bash
# Import from YAML (idempotent — updates existing by name)
uv run radiancefleet corridors import config/corridors.yaml

# Correlate existing gaps against corridors
uv run radiancefleet correlate-corridors
```

## API usage

```bash
# List all corridors
curl http://localhost:8000/api/v1/corridors

# Create a new corridor
curl -X POST http://localhost:8000/api/v1/corridors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ceuta STS Zone",
    "corridor_type": "sts_zone",
    "risk_weight": 1.4,
    "is_jamming_zone": false,
    "description": "Known shadow fleet STS anchorage near Ceuta",
    "geometry_wkt": "POLYGON((-5.5 35.8, -5.3 35.8, -5.3 35.9, -5.5 35.9, -5.5 35.8))"
  }'

# Update metadata (geometry not updatable via API)
curl -X PATCH http://localhost:8000/api/v1/corridors/5 \
  -H "Content-Type: application/json" \
  -d '{"risk_weight": 1.6}'
```

## Seed corridors

The 11 seed corridors in `config/corridors.yaml`:

| Name | Type | Notes |
|------|------|-------|
| Baltic-Primorsk Export Route | import_route | Russian Baltic crude |
| Turkish Straits Chokepoint | chokepoint | Bosphorus + Dardanelles |
| Black Sea Novorossiysk Export | import_route | Russian Black Sea crude |
| Persian Gulf Export Corridor | import_route | Gulf-to-Asia route |
| Mediterranean STS Zone | sts_zone | Known STS area |
| Singapore STS Zone | sts_zone | Riau Islands STS area |
| Laconian Gulf STS Zone | sts_zone | Greece STS area |
| Port Said Anchorage | anchorage | Pre-Suez staging area |
| Gulf of Oman STS Zone | sts_zone | UAE offshore STS |
| Kaliningrad Dark Zone | dark_zone | GPS jamming — Russian enclave |
| Black Sea Jamming Zone | dark_zone | GPS jamming — Russia/Ukraine theatre |

## Adding a new corridor

1. Add entry to `config/corridors.yaml`
2. Run `uv run radiancefleet corridors import config/corridors.yaml`
3. Run `uv run radiancefleet correlate-corridors` to link existing gaps
4. Run `uv run radiancefleet rescore-all-alerts` to update scores with new `risk_weight`

## Geometry tips

- Use WGS84 coordinates (SRID 4326)
- Close polygons: last coordinate must equal first
- Keep corridors focused — overly large polygons increase false positives
- Use tools like [geojson.io](https://geojson.io) to draw and export WKT
