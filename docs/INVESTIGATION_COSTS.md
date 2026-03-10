# Investigation Costs & Research Resources

This document catalogues the cost structure of commercial services that supplement the free-tier workflow, along with open data sources and research references useful for maritime investigations. All costs are estimates as of early 2026; verify with the provider before budgeting.

---

## Commercial Satellite Imagery

| Provider | Product | Unit | Approx. Cost | Notes |
|---|---|---|---|---|
| **Umbra** | SAR archive scene (16-25cm) | per scene | $50-$200 | Ad-hoc tasking; higher resolution than Sentinel-1. Open Data Program releases some free. |
| **Capella Space** | SAR archive (0.5-1m) | per scene (25km sq) | $100-$400 | Fastest tasking response (~4h); good for intercept-window use cases. |
| **Planet Labs** | SkySat optical (0.5m) | per km sq | $1-$5/km sq | Optical only; cloud-dependent. Best for post-confirmation detail. |
| **Spire Global** | Maritime AIS + SAR fusion | per vessel/month | $500-$2,000+ | Enterprise pricing; includes AIS and derived signals. Contact for quote. |
| **exactEarth** | Satellite AIS archive | per vessel-day | $0.10-$1.00 | Historical S-AIS records; Black Sea / Persian Gulf coverage. |
| **S&P Global Maritime** | AIS + vessel intelligence | subscription | $5,000-$20,000+/yr | Full coverage including dark zones; enterprise tier. |

**Practical budget for a newsroom (10 investigations/month):**
- Sentinel-1 confirmation only (free): $0
- 2-3 commercial SAR scenes per month for HIGH-confidence alerts: $200-$800/month
- Historical Black Sea / Persian Gulf AIS (exactEarth): $200-$500/month (case-by-case)

---

## AIS Data (Commercial Supplements)

| Provider | Coverage Gap Filled | Approx. Cost | Notes |
|---|---|---|---|
| **Spire Global** | Persian Gulf, Black Sea, Arctic | $500-$5,000+/month | Satellite AIS with near-global coverage. |
| **exactEarth** | Historical S-AIS archive | Per vessel-day / subscription | Good for retrospective investigations. |
| **MarineTraffic API** | Real-time + 30-day history | $200-$1,000+/month | Tier-based; includes vessel details. |
| **VesselFinder** | Real-time + history | $150-$500/month | Cheaper alternative to MarineTraffic. |

---

## OpenSanctions API

| Tier | Cost | Limit | Notes |
|---|---|---|---|
| Non-commercial (free) | $0 | Reasonable use | Free for journalism / NGO / academic |
| Commercial | $490-$990/month | Unlimited API | Required if used in a commercial product |

---

## Vessel Registry APIs

| Provider | Product | Approx. Cost | Notes |
|---|---|---|---|
| **Datalastic** | REST API (vessel_info) | €99-199/month | Authoritative DWT, vessel type, year built, callsign, gross tonnage. 600 req/min. Recommended for production enrichment. |
| **Equasis** | Manual lookup / scraping | Free (registration) | ToS prohibits automated access. Use Datalastic instead for production. |
| **IHS Markit / S&P Global** | Full vessel intelligence | $5,000-$20,000+/yr | Enterprise pricing. Comprehensive ownership + history. |

---

## Port State Control

- Free; no API cost. Manual download or light scraping of detention records acceptable.

---

## Cost by Use Case

Estimated cost per investigation:

| Scenario | Cost | Sources |
|---|---|---|
| Baltic Sea gap investigation, free tier only | $0 | DMA AIS + Sentinel-1 + Equasis |
| Baltic gap + 1 commercial SAR confirmation | $50-$200 | + Umbra or Capella scene |
| Persian Gulf investigation | $500-$2,000 | Commercial AIS + SAR required |
| Black Sea retrospective investigation | $200-$1,000 | exactEarth archive + SAR |
| Full sanctions cross-check (non-commercial) | $0 | OpenSanctions free tier |

---

## Open Data Sources & References

### AIS Data

| Source | Coverage | Cost | Historical | Notes |
|---|---|---|---|---|
| Danish Maritime Authority (dma.dk) | Baltic Sea | Free | 2006-2016; recent via aisstream.io | Best free Baltic source. Primary for Primorsk/Ust-Luga monitoring. |
| aisstream.io | Global | Free (API key) | None -- real-time only | WebSocket. Used in Baltic research. No history. |
| NOAA Coastal Management Archive (marinecadastre.gov) | US EEZ only | Free | 6 months | US waters only. Not useful for shadow fleet regions. |
| DTU AIS Trajectories (data.dtu.dk) | Danish waters | Free | 2020-era | Academic dataset; useful for algorithm testing. |
| AISHub (aishub.net) | Global | Free* | None | *Requires operating a physical AIS station (10+ vessels, 90% uptime). Not casual access. |
| Global Fishing Watch API (globalfishingwatch.org) | Global | Free | 2012-present | Fishing-focused. Tanker data exists but secondary. Useful for carrier encounter data and pre-computed Sentinel-1 detections. |
| Black Sea / Persian Gulf | None adequate | -- | -- | No free source. Commercial required (Spire Global, exactEarth, S&P Global). |

### Vessel Registry

- **Equasis** (equasis.org) -- free after registration; 85k+ vessels; ownership, ISM manager, classification society, PSC inspection history. Essential.
- **IMO GISIS** (gisis.imo.org) -- free public access; ship particulars, beneficial ownership (where disclosed), company data. No registration required for public data.

### Shadow Fleet / Sanctions Lists

- **OpenSanctions** (opensanctions.org) -- 325+ source aggregation; free non-commercial API (api.opensanctions.org); includes OFAC, EU, UN, national lists. Use for watchlist matching.
- **OFAC SDN List** (treasury.gov/ofac) -- 183+ tankers as of 2025; free, machine-readable; updated irregularly. Subscribe to change notifications.
- **EU Council Sanctions** (sanctionsmap.eu) -- 41+ shadow fleet vessels; free; JSON export.
- **KSE Institute Shadow Fleet Tracker** (kse.ua) -- vessel prioritization list used by coalition governments for designations. Most comprehensive open list.
- **OCCRP Aleph** (aleph.occrp.org) -- corporate and financial investigations database; useful for ownership chain research.

### Port State Control

- **Paris MOU PSC Database** (parismou.org) -- free; inspection history, detentions, deficiencies for EU/North Atlantic fleet. Excellent for vessel compliance signals.
- **Tokyo MOU PSC Database** (tokyo-mou.org) -- free; Asia-Pacific coverage.
- Both publish weekly updates; build an adapter to sync detentions.

### Satellite Imagery

- **Copernicus Open Access Hub / Browser** (browser.dataspace.copernicus.eu) -- Sentinel-1 SAR (ship detection without AIS); Sentinel-2 optical. Free; ESA. Sentinel-1 is the primary tool for verifying vessel presence during AIS gaps.
- **Global Fishing Watch vessel detections** -- pre-computed Sentinel-1 detections; downloadable; saves computation for common areas.
- **Umbra Open Data Program** (umbra.space) -- free SAR releases (ad-hoc; high resolution).
- **NASA Earthdata** (earthdata.nasa.gov) -- MODIS, Landsat; lower resolution; free.

### Insurance

- **International Group of P&I Clubs** (igpandi.org) -- free public member list; 13 clubs covering ~90% of world ocean-going tonnage. If a vessel's insurer is not on this list, that is a significant red flag (82% of shadow fleet lacks reputable P&I, KSE 2025).

### OSINT Tools & References

- **AISViz** (github.com/AISViz) -- open-source AIS processing toolbox
- **Bellingcat Online Investigation Toolkit** (bellingcat.gitbook.io/toolkit)
- **TankerTrackers.com** -- crude oil export tracking with satellite imagery
- **SkyTruth** (skytruth.org) -- vessel AIS falsification investigations (multiple documented cases of Russian Black Sea port entries with AIS off)

### Key Research

- "AIS Data Manipulation in the Illicit Global Oil Trade" -- MDPI JMSE 2024
- "Shadow Fleets: A Growing Challenge in Global Maritime Commerce" -- MDPI 2025
- "The Secret Lives of the Shadow Fleet: AIS Spoofing" -- Lloyd's List Intelligence
- "AIS Data Vulnerability Indicated by a Spoofing Case-Study" -- MDPI Applied Sciences 2021
- "Detecting Ship-to-Ship Transfers by MOSA" -- MDPI Remote Sensing 2025
- "SeaSpoofFinder: Automated AIS Spoofing Detection" -- arXiv 2025
- Bellingcat shadow fleet investigations (multiple, 2024-2026)
- DFRLab "Oil Laundering at Sea" -- Mediterranean shadow fleet analysis 2024

### Vessel Detection & Visual Re-ID

**ML Models & Tools:**
- **Skylight / AI2** (allenai/vessel-detection-sentinels) -- Apache 2.0; production pipeline for Sentinel-1/2 vessel detection + AIS correlation; used by 300+ orgs in 70 countries. skylight.global
- **xView3 First Place Solution** (github.com/BloodAxe/xView3-The-First-Place-Solution) -- best open-source SAR detection model; trained on 220k+ vessel instances
- **SARfish** (github.com/DIUx-xView/SARFish) -- WACV 2024 dataset + models
- **AssenSAR Wake Detector** (github.com/oktaykarakus/AssenSAR-Wake-Detector) -- wake detection for heading/speed inference

**Visual Re-ID:**
- **HOSS ReID** (Alioth2000/Hoss-ReID) -- cross-modal optical+SAR ship re-ID; ICCV 2025; arXiv:2506.22027
- **VesselReID-700** (vsislab/VesselReID) -- 56k images, 1248 vessels; baseline models included
- **ShipRSImageNet** (zzndream/ShipRSImageNet) -- 17k+ ship instances; detection + classification

**Key Datasets:**
- **xView3-SAR** (iuu.xview.us) -- 1.4 gigapixels, 220k+ instances; best SAR detection benchmark
- **GFW Vessel Detections** -- pre-computed Sentinel-1 detections 2017-present; download via GFW API

**Resolution Reality Check:**
- Text/OCR on hull names: NOT FEASIBLE from satellite (needs <10cm; Sentinel-1 = 20m, Umbra SAR = 16-25cm but too noisy)
- Visual detection + type classification: achievable at Sentinel-1 resolution (10-20m)
- Instance-level re-ID: requires commercial SAR (0.25-1m) or multi-modal fusion; 70-90% top-1 accuracy
- Wake pattern analysis (heading/speed): visible at Sentinel-2 resolution (10m)

**Satellite Tasking:**
- Sentinel-1 near-real-time latency: **1 hour** from acquisition to processed product
- Government intercept window: ~18h for a CG vessel 200nm away (25kn cutter vs 14kn tanker)
- Target alert SLA: <= 6h from satellite acquisition to analyst-ready alert
