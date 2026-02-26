# Coverage Limitations

This document describes the known limitations of AIS data sources used by RadianceFleet. Understanding these limitations is essential for interpreting alerts correctly and avoiding false conclusions.

---

## AIS Terrestrial vs. Satellite Coverage

AIS signals are received through two independent infrastructure types, each with different coverage characteristics.

### Terrestrial AIS

Terrestrial AIS receivers are land-based antennas that pick up VHF radio transmissions from vessel transponders. Their effective range is 40-60 nautical miles from shore, depending on antenna height and atmospheric conditions.

**Strengths:**
- Near-real-time reception (sub-second latency)
- Continuous coverage within range -- no revisit gaps
- High message throughput -- handles dense traffic areas

**Limitations:**
- Zero coverage beyond 60nm from shore
- Mountainous coastlines or island chains can create shadow zones
- No coverage in open ocean, mid-passage routes, or remote archipelagos

**Practical impact on RadianceFleet:** A vessel transiting from the Baltic to the Mediterranean will have continuous terrestrial coverage near European coastlines but may show apparent "gaps" when passing through areas where terrestrial receiver density is low (e.g., parts of the North African coast). These are coverage gaps, not vessel behavior.

### Satellite AIS (S-AIS)

Satellite AIS uses low-earth-orbit satellites to receive AIS transmissions from vessels beyond terrestrial range. Coverage is global in theory but intermittent in practice.

**Strengths:**
- Global footprint -- can receive signals from any ocean
- Independent of shore infrastructure

**Limitations:**
- Each satellite pass covers a given point for only minutes. Revisit intervals range from 30 minutes to several hours depending on the constellation.
- In high-traffic areas, satellite receivers experience message collision: too many vessels transmitting at once causes signal overlap and lost messages. The International Telecommunications Union estimates 40-60% message loss in congested zones like the English Channel and Strait of Malacca.
- Free satellite AIS sources (aisstream.io) have smaller constellations than commercial providers, resulting in longer coverage gaps.
- Satellite AIS latency is typically 1-15 minutes, not real-time.

**Practical impact on RadianceFleet:** An apparent AIS gap of 2-6 hours in open ocean may simply reflect the gap between satellite passes rather than deliberate transponder deactivation. This is why RadianceFleet's gap detection uses duration thresholds (minimum 2 hours) and the scoring engine assigns lower scores to shorter gaps.

---

## Known Dark Zones

Dark zones are geographic areas where AIS coverage is systematically degraded due to GPS jamming, intentional interference, or infrastructure absence. RadianceFleet ships with two pre-configured dark zones in `config/corridors.yaml`.

### Strait of Hormuz / Persian Gulf

- **Cause:** Active GPS jamming, attributed to Iranian military operations, affecting 1000+ vessels per day (Windward, 2025).
- **Effect:** Vessels transiting the Strait of Hormuz frequently lose GPS lock, causing AIS positions to freeze, jump, or stop transmitting entirely. This affects all vessel classes indiscriminately.
- **RadianceFleet handling:** The corridor is marked `is_jamming_zone: true`. Gap scores inside this zone receive a -10 point reduction. However, this is a coarse correction. Investigators should treat all Hormuz gap alerts with additional skepticism.
- **Free AIS coverage:** None adequate. Commercial satellite AIS (Spire Global, exactEarth) is required for reliable monitoring in this region.

### Black Sea / Crimea / Russian EEZ

- **Cause:** Systematic Russian GPS jamming in the Black Sea and Crimea region. Additionally, AIS data from Russian-controlled ports (Novorossiysk, Kavkaz) is heavily falsified, with vessels reporting anchor positions while actually underway (documented by SkyTruth, February 2025).
- **Effect:** Vessels in this area may show false positions, frozen tracks, or complete AIS blackouts. The falsification is deliberate and systematic, making it difficult to distinguish between equipment issues and intentional manipulation.
- **RadianceFleet handling:** Marked as `is_jamming_zone: true` with -10 score reduction. The adjacent export route corridor (Kavkaz/Novorossiysk Approaches) is scored separately at risk_weight 1.5.
- **Free AIS coverage:** POOR. Free sources show significant data quality issues in this region.

### Other areas with known interference

The following areas are not pre-configured as dark zones but are known to experience intermittent GPS jamming or AIS interference:

- **Eastern Mediterranean** -- sporadic GPS jamming incidents, often near conflict zones
- **South China Sea** -- localized interference near disputed territories
- **Russian Arctic EEZ** -- systematic jamming along the Northern Sea Route
- **Gulf of Guinea** -- poor terrestrial coverage combined with piracy-related AIS manipulation

Users can add custom dark zone corridors in `config/corridors.yaml` by setting `corridor_type: dark_zone` and `is_jamming_zone: true`.

### How dark zones affect scoring

RadianceFleet applies three dark zone scoring rules:

| Scenario | Score effect | Rationale |
|----------|-------------|-----------|
| Gap occurs entirely inside a known jamming zone | -10 points | Gap is expected; likely caused by jamming, not evasion |
| Gap begins immediately before entering a dark zone | +20 points | Suspicious timing suggests deliberate deactivation before entering area where gaps are expected |
| Vessel exits dark zone with impossible position jump | +35 points | Vessel reappears at a location inconsistent with plausible transit speed, suggesting it moved while dark |

---

## Class A vs. Class B Transmission Intervals

AIS transponders come in two classes with very different transmission behaviors.

### Class A

- **Required for:** All vessels over 300 gross tonnage on international voyages, and all cargo ships over 500 GT (IMO SOLAS Convention).
- **Transmission interval while underway:** Every 2-10 seconds, depending on speed and course change rate.
- **Transmission interval at anchor:** Every 3 minutes.
- **Practical gap threshold:** Any Class A gap exceeding 10 minutes is unusual. RadianceFleet's minimum gap threshold is 2 hours, which provides a wide margin above normal Class A behavior.

### Class B

- **Used by:** Smaller vessels (typically under 300 GT), fishing boats, recreational craft. Not legally required for all vessel classes.
- **Transmission interval while underway:** Every 30 seconds (Class B+ / CS) or every 3 minutes (standard Class B / SO).
- **Transmission interval at anchor:** Every 3 minutes.
- **Key differences from Class A:**
  - Lower transmission power (2W vs. 12.5W for Class A), meaning shorter reception range
  - No guaranteed slot reservation in the TDMA protocol, leading to message loss in congested areas
  - Some Class B devices reduce transmission frequency further when stationary

### Impact on gap detection

RadianceFleet applies a Class B noise filter: gaps shorter than 180 seconds for Class B devices are ignored. However, the fundamental issue remains -- Class B vessels in areas with poor terrestrial coverage and infrequent satellite passes will naturally show longer gaps than Class A vessels in the same location.

**Red flag:** A large tanker (over 300 GT or over 1000 DWT) reporting as AIS Class B is itself a serious anomaly. Class A is mandatory for these vessels under international law. RadianceFleet assigns +50 points for this signal. Class switching (a vessel that previously transmitted as Class A now transmitting as Class B) receives +25 points.

---

## Temporal Gaps in Data Feeds

### Archive vs. real-time sources

| Source type | Temporal characteristics | Gap behavior |
|-------------|------------------------|-------------|
| Historical archives (DMA CSV) | Complete for the archive period; no updates | Gaps within the archive reflect actual coverage at the time of recording |
| Real-time feeds (aisstream.io) | Continuous but dependent on network availability | Feed outages create artificial gaps across all vessels simultaneously |
| Satellite detections (GFW) | Daily batches with processing lag | Detection timestamps may lag actual observation by hours to days |

### How to identify feed outages vs. vessel gaps

If multiple unrelated vessels in the same region all show AIS gaps starting at the same time, the cause is almost certainly a feed outage or receiver downtime, not coordinated vessel behavior. RadianceFleet does not currently detect feed-level outages automatically. Analysts should check whether gap clusters are geographically and temporally correlated before attributing them to individual vessel behavior.

### Data freshness

- DMA Baltic archives cover 2006-2016. More recent Baltic data requires aisstream.io or commercial sources.
- aisstream.io provides real-time data only; it does not maintain a historical archive. Export time-windowed batches manually.
- GFW vessel detection data has variable update lag depending on region and satellite revisit schedule.

---

## Geographic Coverage Summary

| Region | Terrestrial AIS | Satellite AIS (free) | Overall quality | Notes |
|--------|----------------|---------------------|----------------|-------|
| Baltic Sea | Excellent | Good | GOOD | DMA archive + aisstream.io. Best-covered region for free sources. |
| Turkish Straits | Excellent | Good | GOOD | Well-monitored chokepoint. Dense terrestrial receiver network. |
| Mediterranean (near coast) | Good | Moderate | MODERATE | Good near European ports; sparse off North Africa and open sea. |
| Mediterranean (open sea) | None | Moderate | MODERATE | Satellite-only. Gaps of 1-4 hours are common and often innocent. |
| Singapore Strait | Good | Moderate | PARTIAL | Terrestrial coverage is good in the strait; outer port limits are patchy. |
| Far East / Nakhodka | Limited | Limited | PARTIAL | Terrestrial coverage only near port approaches. Open sea coverage is poor. |
| Black Sea | Limited | Poor | POOR | AIS data actively falsified near Russian-controlled areas. Free sources unreliable. |
| Persian Gulf | Limited | None (free) | NONE | No adequate free AIS source. Commercial subscription required (Spire, exactEarth, S&P Global Maritime). |
| Open ocean (general) | None | Intermittent | POOR | Satellite-only with multi-hour gaps between passes. Most "gaps" here are coverage gaps. |
| Arctic / Northern Sea Route | None | Very limited | POOR | Minimal satellite coverage. GPS jamming documented along Russian Arctic EEZ. |

### What this means for investigations

- **Baltic and Turkish Straits:** RadianceFleet is most reliable in these regions. Gaps exceeding 2 hours are likely genuine anomalies worth investigating.
- **Mediterranean:** Gaps near European coastlines are more meaningful than gaps in open sea. Apply additional scrutiny to open-sea gaps.
- **Black Sea and Persian Gulf:** Do not rely on RadianceFleet's free-source pipeline for these regions. False positive rates will be very high. Commercial AIS data is necessary for any credible investigation.
- **Open ocean:** Treat all open-ocean gaps with caution. Cross-reference with satellite imagery before drawing conclusions.

---

## Recommendations for Analysts

1. Always check the data source and its known coverage quality for the alert's geographic region before acting on a score.
2. Correlate gaps with known jamming zones using [gpsjam.org](https://gpsjam.org).
3. If multiple vessels show simultaneous gaps in the same area, suspect a feed outage or receiver issue rather than coordinated vessel behavior.
4. For investigations in the Black Sea or Persian Gulf, budget for commercial AIS data. Free sources will not provide reliable coverage.
5. Remember that a gap is an absence of data. An absence of data is not evidence of wrongdoing -- it is evidence that you need more data.
