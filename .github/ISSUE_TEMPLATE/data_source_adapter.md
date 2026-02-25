---
name: Data source adapter request
about: Request support for a new AIS, sanctions, or satellite data source
labels: data-source
---

## Source Details

**Name**: <!-- e.g. EU Council Sanctions List -->
**Publisher**: <!-- e.g. European Union External Action Service -->
**URL**: <!-- e.g. https://www.sanctionsmap.eu/ -->
**License**: <!-- e.g. CC BY 4.0, public domain, terms of use URL -->

## Access Method

- [ ] Free download (no account required)
- [ ] Free download (account / API key required)
- [ ] Paid API
- [ ] Web scraping (please confirm this is permitted by the site's terms of use)
- [ ] Other: <!-- describe -->

If an API key is required: <!-- describe the registration process and any rate limits -->

## Update Frequency

<!-- How often does the source publish new data? e.g. daily, weekly, on-demand -->

## Format

<!-- Describe the file format and how to obtain a sample. -->

- [ ] CSV
- [ ] JSON
- [ ] XML
- [ ] Other: <!-- describe -->

Download instructions or sample file link:

## Field Mapping

Map the source's fields to RadianceFleet fields. Add rows as needed.

| Source field | RadianceFleet field | Notes |
|-------------|--------------------|----|
| <!-- e.g. `vessel_name` --> | `Vessel.name` | <!-- e.g. may be a list; use first element --> |
| <!-- e.g. `imo_no` --> | `Vessel.imo` | |
| <!-- e.g. `mmsi_number` --> | `Vessel.mmsi` | <!-- must be 9-digit string --> |
| <!-- e.g. `flag_state` --> | `Vessel.flag` | <!-- ISO 3166-1 alpha-2 preferred --> |
| <!-- e.g. `listed_date` --> | `VesselWatchlist.date_listed` | |
| <!-- e.g. `reason_text` --> | `VesselWatchlist.reason` | |

Fields in the source that have no RadianceFleet equivalent:

| Source field | Suggested handling |
|-------------|-------------------|
| <!-- e.g. `associated_individuals` --> | <!-- e.g. store in `analyst_notes`, ignore, or new column --> |

## Sample Data

<!-- Paste 2–5 representative rows from the source. Redact any personal data that is not
     already in the public record. -->

```
<!-- paste sample rows here (CSV, JSON snippet, or XML fragment) -->
```

## Proposed Loader Location

<!-- Where should the new loader function live?
     Watchlist sources: backend/app/modules/watchlist_loader.py
     AIS sources: backend/app/modules/ingest.py
     Satellite/detection sources: backend/app/modules/gfw_import.py or a new module -->

Suggested function name: <!-- e.g. `load_eu_council_sanctions` -->
Suggested CLI command: <!-- e.g. `radiancefleet watchlist import --source eu_council <file>` -->

## Additional Context

<!-- Why is this source valuable for shadow fleet triage?
     Are there data quality concerns (e.g. irregular update schedule, incomplete MMSI coverage)?
     Links to related issues or prior discussion. -->
