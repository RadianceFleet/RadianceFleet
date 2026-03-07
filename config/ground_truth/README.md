# Ground Truth Data

Place CSV files here for validation. DO NOT commit real data — only templates.

## Sources

- **KSE Shadow Fleet List**: https://kse.ua/analytical-work/
  Download the latest vessel list, save as `kse_shadow_fleet.csv`
- **OFAC SDN**: https://sanctionssearch.ofac.treas.gov/
  Filter for vessel type, export, save as `ofac_sdn.csv`
- **EU Council**: https://www.sanctionsmap.eu/
  Filter for vessel restrictions
- **Clean baseline**: Use verified legitimate tanker operators (Maersk, Frontline, etc.)

## Import
```
radiancefleet gt-import kse config/ground_truth/kse_shadow_fleet.csv
radiancefleet gt-import ofac config/ground_truth/ofac_sdn.csv
radiancefleet gt-import clean config/ground_truth/clean_vessels.csv
radiancefleet validate --verbose --signal-report
```
