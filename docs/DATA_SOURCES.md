# Data Sources

A practical guide mapping each supported data source to its import steps, format, and data quality characteristics.

---

## Quick Reference

| Source | Command | Update Frequency | Format | Notes |
|--------|---------|-----------------|--------|-------|
| DMA Baltic AIS CSV | `radiancefleet ingest ais <file>` | 2006–2016 archive | CSV | Free from Danish Maritime Authority |
| aisstream.io WebSocket | manual export | Real-time | JSON -> CSV | Free API key required; not yet automated |
| GFW vessel detections | `radiancefleet gfw import <csv>` | Daily | CSV | Global Fishing Watch API |
| OFAC SDN | `radiancefleet watchlist import --source ofac <file>` | Weekly | CSV | US Treasury sanctions list |
| KSE Shadow Fleet | `radiancefleet watchlist import --source kse <file>` | Monthly | CSV | Kyiv School of Economics |
| OpenSanctions | `radiancefleet watchlist import --source opensanctions <file>` | Daily | JSON | Aggregated sanctions database |

---

## AIS Position Data

### Danish Maritime Authority (DMA) — Baltic AIS Archive

- **Download**: https://www.dma.dk/safety-at-sea/navigational-information/ais-data
- **Coverage**: Baltic Sea, 2006–2016. Free for research and non-commercial use.
- **Format**: CSV with the following required columns:

  ```
  MMSI, Timestamp, Latitude, Longitude, SOG, COG, Heading, IMO, Name, CallSign, VesselType, Status, Length, Width, Draft, Cargo, Transceiver
  ```

- **Import**:

  ```bash
  radiancefleet ingest ais ./data/aisdk_2024_01.csv
  ```

- **Output**: `accepted`, `rejected`, and `duplicates` counts are printed. Rejected rows have parse errors logged (non-numeric lat/lon, invalid MMSI, etc.).

- **Data quality notes**:
  - Timestamps are UTC. The ingestor rejects rows where timestamp cannot be parsed.
  - MMSI must be 9 digits. Short or all-zero MMSIs are rejected.
  - Duplicate detection is by (MMSI, timestamp) pair — safe to re-run on the same file.
  - AIS Class B vessels (recreational/small commercial) are tagged separately and apply a stricter gap threshold (DWT > 1000 for illegal Class B flag).
  - The DMA dataset does not include AIS Class B for all years. Check the DMA release notes for each year's file.

### aisstream.io — Real-time WebSocket Feed

- **URL**: https://aisstream.io/
- **Access**: Free API key; sign up at the website.
- **Format**: JSON messages, one per AIS transmission. Fields: `mmsi`, `latitude`, `longitude`, `sog`, `cog`, `heading`, `timestamp`, `name`, `imo`, etc.
- **Current status**: Automated ingestion is not yet implemented. Export a time-windowed JSON batch to CSV matching the DMA column schema, then use `radiancefleet ingest ais`.
- **Data quality notes**:
  - Coverage is global and near-real-time but dependent on terrestrial receiver network density.
  - Satellite AIS coverage gaps are common in open ocean — do not confuse satellite dead zones with deliberate AIS disabling.

---

## Satellite Vessel Detections

### Global Fishing Watch (GFW)

- **Download**: https://globalfishingwatch.org/data-download/
- **Access**: Free account required; accept terms of use.
- **Coverage**: Global, near-daily updates from SAR (Synthetic Aperture Radar) satellite passes.
- **Format**: CSV with the following required columns:

  ```
  detect_id, timestamp, lat, lon, vessel_length_m, vessel_score, vessel_type
  ```

- **Import**:

  ```bash
  radiancefleet gfw import ./data/gfw_detections_2024_q1.csv
  ```

- **Output**: `total`, `matched` (AIS-correlated), `dark` (no AIS match), `rejected` (parse errors).

- **Data quality notes**:
  - `vessel_score` is GFW's confidence that the radar return is a vessel (0–1). Rows with score < 0.5 are not recommended for high-confidence analysis.
  - `dark` ships are those with no matching AIS transmission within a spatial-temporal window. These are the primary signal for dark vessel detection (FR8).
  - GFW may change CSV column names between data product versions. If the import rejects all rows, check that column names match expectations.
  - GFW data is licensed CC BY-SA 4.0. Attribution is required in published work.

---

## Sanctions and Watchlists

All watchlist loaders use the same three-step matching strategy:
1. MMSI exact match (9-digit string)
2. IMO exact match
3. Fuzzy name match via rapidfuzz at >= 85% confidence (with optional flag pre-filter)

Unmatched rows emit a warning and are skipped — they do not abort the batch. Re-running the import on updated files is safe; existing entries are re-activated rather than duplicated.

### OFAC Specially Designated Nationals (SDN)

- **Download**: https://www.treasury.gov/ofac/downloads/sdn.csv
- **Publisher**: US Department of the Treasury, Office of Foreign Assets Control
- **Update frequency**: Weekly, sometimes more often for urgent designations
- **Format**: CSV

  Required columns (case-sensitive as published by OFAC):
  ```
  SDN_TYPE, SDN_NAME, VESSEL_ID, ent_num, REMARKS
  ```

  Only rows where `SDN_TYPE == "Vessel"` are processed. All other entity types (individuals, companies) are skipped silently.

- **Import**:

  ```bash
  radiancefleet watchlist import --source ofac ./data/sdn.csv
  ```

- **Output**: `matched`, `unmatched`, `skipped` counts.

- **Data quality notes**:
  - OFAC periodically restructures the CSV column layout and adds supplemental files (`sdn_advanced.xml`). If `VESSEL_ID` or `ent_num` columns are absent, MMSI/IMO matching will fall back to fuzzy name matching.
  - The XML version (`sdn.xml`) is not currently supported. Use the flat CSV download.
  - MMSI in the OFAC CSV is stored in the `VESSEL_ID` column and may include non-numeric prefixes or suffixes — the loader validates strict 9-digit format and falls through to IMO/name match if invalid.
  - Sanctions listings are legal designations, not confirmed operational status. Cross-reference with AIS data.

### KSE Institute Shadow Fleet List

- **Download**: https://kse.ua/information-department/shadow-fleet/
- **Publisher**: Kyiv School of Economics
- **Update frequency**: Monthly (roughly); no fixed release schedule.
- **Format**: CSV with flexible column naming. The loader tries multiple common variants:

  | Data field | Accepted column names |
  |-----------|----------------------|
  | Vessel name | `vessel_name`, `name`, `ship_name`, `VESSEL_NAME`, `NAME` |
  | Flag | `flag`, `flag_state`, `FLAG`, `FLAG_STATE` |
  | IMO | `imo`, `imo_number`, `IMO`, `IMO_NUMBER` |
  | MMSI | `mmsi`, `MMSI` |

- **Import**:

  ```bash
  radiancefleet watchlist import --source kse ./data/kse_shadow_fleet.csv
  ```

- **Output**: `matched`, `unmatched` counts.

- **Data quality notes**:
  - The KSE list tracks vessels suspected of carrying Russian oil in violation of the G7 price cap. It is not a legal sanctions list — listings are analytical assessments.
  - MMSI and IMO coverage varies by row. Many vessels are identified by name and flag only, relying on fuzzy matching.
  - Flag field may use 2-letter ISO codes, 3-letter codes, or full country names. The loader passes the value as-is to the flag pre-filter; mismatches fall through to unfiltered fuzzy name matching.

### OpenSanctions

- **Download**: https://www.opensanctions.org/datasets/vessels/
- **Publisher**: OpenSanctions (aggregates OFAC, EU Council, UN, and other lists)
- **Update frequency**: Daily
- **Format**: JSON array of entity objects

  Each entity must have `schema == "Vessel"`. Properties are read from the nested `properties` object:
  ```json
  {
    "schema": "Vessel",
    "caption": "VESSEL NAME",
    "datasets": ["ofac_sdn"],
    "properties": {
      "name": ["VESSEL NAME"],
      "mmsi": ["123456789"],
      "imoNumber": ["1234567"],
      "flag": ["RU"]
    }
  }
  ```

  The `dataset_id` or first entry in `datasets` controls the `watchlist_source` label stored in the database:
  - Dataset contains `"ofac"` -> stored as `OFAC_SDN`
  - Dataset starts with `"eu"` or contains `"eu_"` -> stored as `EU_COUNCIL`
  - Otherwise -> stored as `OPENSANCTIONS`

- **Import**:

  ```bash
  radiancefleet watchlist import --source opensanctions ./data/opensanctions_vessels.json
  ```

- **Output**: `matched`, `unmatched` counts.

- **Data quality notes**:
  - OpenSanctions normalises multiple source lists into a single schema, making it the most convenient source for broad coverage.
  - The `name` field may be a JSON string or a JSON array; the loader uses the first element if it is a list.
  - Not all OpenSanctions vessel entities include MMSI or IMO. Name-only matches will depend on whether the vessel is already in the RadianceFleet database from AIS ingestion.
  - OpenSanctions data is licensed CC BY-NC 4.0. Commercial use requires a separate agreement with OpenSanctions.

---

## Adding a New Data Source

To add a new watchlist or AIS data source, see the issue template at `.github/ISSUE_TEMPLATE/data_source_adapter.md`. The minimum requirements for a new loader are:

1. A field mapping table from the source schema to RadianceFleet fields.
2. An idempotent upsert (no duplicates on re-run).
3. A returned summary dict with at minimum `matched` and `unmatched` counts.
4. Unit tests covering at least: normal match, no match (warning, no crash), and duplicate re-activation.
