# RadianceFleet Methodology

This document describes the detection methods, scoring logic, validation framework, and known limitations of RadianceFleet. It is intended for journalists, OSINT researchers, NGO analysts, and other external stakeholders who need to understand how the tool works before interpreting its output.

RadianceFleet was built to address three challenges facing investigators tracking Russian oil flows: too much ocean for manual monitoring, AIS data that is incomplete or deliberately manipulated, and evidence fragmented across multiple tools and formats. It combines AIS anomaly detection, route-based prioritization, satellite imagery checks, and exportable evidence cards into a single open-source workflow.

---

## 1. Purpose & Scope

RadianceFleet is an investigative triage tool for maritime anomaly detection. It identifies AIS (Automatic Identification System) patterns that are consistent with shadow fleet behavior and warrant further investigation. It produces **anomaly indicators, not legal determinations**.

RadianceFleet does not prove sanctions violations, criminal activity, or intent. It cannot determine why an AIS gap occurred, whether cargo was transferred during a proximity event, whether any cargo was Russian-origin crude oil, or whether any law was broken. A high risk score means the vessel's AIS pattern matches multiple heuristics that have historically correlated with shadow fleet operations. It does not mean the vessel was conducting sanctioned activity.

> **DISCLAIMER:** This is investigative triage, not a legal determination. This tool identifies patterns warranting further investigation. No conclusions about sanctions violations or criminal activity should be drawn from this output without independent expert verification.

This disclaimer is included in every exported evidence card and must be preserved in any downstream reporting that cites RadianceFleet output.

---

## 2. Data Sources

### AIS fundamentals

AIS (Automatic Identification System) transponders broadcast vessel identity, position, speed, and course over VHF radio. This data is self-reported by the vessel and can be manipulated. A vessel can turn off its transponder, change its reported MMSI (Maritime Mobile Service Identity), or broadcast false positions.

AIS signals are received through two independent infrastructure types:

- **Terrestrial AIS receivers** are land-based antennas with an effective range of 40-60 nautical miles from shore, depending on antenna height and atmospheric conditions. They provide near-real-time, continuous coverage within range but have zero coverage beyond approximately 60nm from shore.
- **Satellite AIS (S-AIS)** uses low-earth-orbit satellites to receive AIS transmissions globally. However, coverage is intermittent: each satellite pass covers a given point for only minutes, with revisit intervals ranging from 30 minutes to several hours depending on the constellation. In high-traffic areas, satellite receivers experience message collision (the ITU estimates 40-60% message loss in congested zones like the English Channel). Free satellite AIS sources have smaller constellations than commercial providers, resulting in longer coverage gaps.

### Coverage quality by region

The following table reflects the coverage quality of free AIS sources used by RadianceFleet. Coverage quality directly affects the reliability of gap detection and scoring in each region.

| Region | Source | Quality | Notes |
|--------|--------|---------|-------|
| Baltic Sea | DMA CSV archive + aisstream.io | GOOD | Best-covered region for free sources. Good terrestrial and satellite coverage. |
| Turkish Straits | aisstream.io | GOOD | Well-monitored chokepoint with dense terrestrial receiver network. |
| Mediterranean | aisstream.io | MODERATE | Good near European ports; sparse off North Africa and open sea. Satellite-only gaps of 1-4 hours are common and often innocent. |
| Singapore Strait | aisstream.io | PARTIAL | Terrestrial coverage is good in the strait; outer anchorage areas are patchy. |
| Far East / Nakhodka | aisstream.io | PARTIAL | Limited coverage outside port approaches. Open sea coverage is poor. |
| Black Sea | Limited | POOR | AIS data is actively falsified near Russian-controlled areas (documented by SkyTruth, 2025). Free sources are unreliable. |
| Persian Gulf | None (free) | NONE | No adequate free AIS source. Commercial subscription required (Spire Global, exactEarth, S&P Global Maritime). |
| Open ocean | Satellite only | POOR | Multi-hour gaps between satellite passes. Most apparent "gaps" in open ocean are coverage gaps, not vessel behavior. |

### Key AIS limitations

- **AIS is self-reported.** Vessels can turn off transponders, change MMSI, or broadcast false positions. RadianceFleet detects some forms of manipulation (see Spoofing Detection in Section 3), but cannot detect all forms of tampering.
- **Coverage gaps are not suspicious by default.** An apparent AIS gap of 2-6 hours in open ocean may reflect the gap between satellite passes, not deliberate transponder deactivation. This is why RadianceFleet uses duration thresholds and assigns lower scores to shorter gaps.
- **Class A vs. Class B.** Class A transponders (mandatory for vessels over 300 GT on international voyages under SOLAS) transmit every 2-10 seconds while underway and every 3 minutes at anchor. Class B transponders (used by smaller vessels, typically under 300 GT) transmit every 30 seconds to 3 minutes with lower power (2W vs. 12.5W), resulting in naturally longer gaps and shorter reception range. A large tanker (over 300 GT or over 1000 DWT) transmitting as Class B is itself a serious anomaly, as Class A is mandatory for these vessels under international law.
- **GPS jamming.** Russia operates extensive GPS jamming near its coastlines, throughout the Black Sea, and in parts of the Baltic. The Strait of Hormuz is also heavily affected, with 1000+ vessels per day experiencing disruption. Vessels in these areas frequently experience AIS disruption that looks identical to deliberate deactivation. RadianceFleet marks known jamming zones and applies a -10 point score reduction for gaps inside them, but this is an incomplete correction. Analysts should check [gpsjam.org](https://gpsjam.org) for the specific time period before reporting on gaps in or near known jamming areas.

---

## 3. Detection Methods

RadianceFleet uses multiple independent detectors. Each detector identifies a specific type of anomalous behavior. Detectors run independently and their outputs feed into the risk scoring engine as additive signals.

### AIS gap detection

The core detector identifies gaps in a vessel's AIS transmission history. A gap is defined as a time delta between consecutive AIS observations exceeding a configurable threshold (default: 2 hours for Class A, 3 minutes for Class B noise filtering). The detector computes the implied velocity between the last position before the gap and the first position after the gap, comparing it against class-specific maximum speeds (e.g., VLCC max 18 knots, Aframax max 20 knots) to assess whether the vessel could have plausibly traveled between the two positions.

Gaps are evaluated in the context of the corridor where they occur: a gap in a well-covered region like the Baltic is more significant than the same-duration gap in open ocean. The detector also computes gap frequency over rolling windows (7, 14, and 30 days) to identify vessels with repeated gap patterns, using provenance-aware identity tracking so that gaps are counted against the correct vessel identity even after MMSI changes.

Gaps caused by detected feed outages (where multiple unrelated vessels in the same region lose coverage simultaneously) are automatically excluded from scoring.

### Spoofing detection

Five distinct spoofing patterns are detected:

- **Impossible speed.** The implied speed between two consecutive AIS positions exceeds the physical capability of the vessel class. Speed thresholds are calibrated per vessel size (VLCC, Suezmax, Aframax, Panamax), with larger vessels having lower plausible maximum speeds.
- **MMSI conflict.** Two or more vessels transmit the same MMSI simultaneously from different geographic locations, or a single MMSI appears at locations implying impossible transit speed. This indicates either identity sharing or identity theft.
- **Anchor-in-ocean.** A vessel reports navigation status "at anchor" while positioned in deep open water far from any anchorage. This pattern is associated with AIS data fabrication.
- **Circle spoofing.** A vessel's reported positions form a tight circular pattern inconsistent with natural vessel movement, suggesting pre-programmed false position broadcasting. This is the highest-scored spoofing signal (35 points).
- **Route laundering.** Detection of Russian-origin cargo being routed through intermediary ports to obscure its origin. The detector identifies multi-hop patterns (Russian port to intermediary to final destination) and scores them based on the number of hops and corroborating signals.

### Loitering detection

Identifies vessels maintaining very low speed (below 0.5 knots SOG) for extended periods. A minimum of 4 consecutive hours qualifies as a loitering event; 12 or more hours in a monitored corridor is scored as sustained loitering. The detector also identifies laid-up vessels (stationary for 30 or 60+ days within a tight positional bounding box of approximately 2 nautical miles), which is a known shadow fleet staging pattern.

### STS transfer detection

Ship-to-ship transfer detection identifies pairs of vessels in sustained proximity at low speed, consistent with cargo transfer operations. The detector accounts for anchorage exclusion zones (configured per corridor) to reduce false positives from vessels legitimately anchored near each other. Scoring escalates when one vessel goes dark during the proximity event, when the event occurs in a known STS zone, or when a preceding loiter-gap-loiter pattern is detected within a 48-hour window.

### Identity merge detection

Uses breadth-first search (BFS) over connected components of MMSI identity relationships to detect vessels that have changed identity. When a vessel's MMSI, IMO number, or call sign links to another vessel record, the merge detector builds a chain of connected identities. Chains of 3 or more vessels indicate systematic identity switching. Confidence is scored in bands: chains involving scrapped IMO numbers or fabricated IMO checksums receive the highest scores. The detector also identifies when a vessel transmits an IMO number belonging to a known-scrapped vessel, a strong indicator of identity fraud.

---

## 4. Risk Scoring

### Scoring architecture

Risk scoring uses three-phase composition:

1. **Phase 1 -- Additive signals.** Each detector contributes flat point values for its findings (e.g., gap duration 8-12 hours = 25 points, circle spoofing = 35 points, OFAC watchlist match = 50 points). Only the highest-tier signal fires within each category (subsumption logic prevents double-counting).
2. **Phase 2 -- Corridor multiplier.** The sum of risk signals is multiplied by a corridor factor: 1.5x for known STS zones and high-risk export corridors, 1.0x for standard corridors, 0.7x for legitimate trade routes.
3. **Phase 3 -- Vessel size multiplier.** The corridor-adjusted score is multiplied by a vessel size factor: 1.3x for VLCC (>200k DWT), 1.2x for Suezmax, 1.0x for Aframax, 0.8x for Panamax.

Legitimacy deductions (negative signals such as clean P&I insurance, white-list flag state, clean PSC record) are added after amplification so they always deduct their face value regardless of zone or vessel size.

The final formula is:

```
final_score = round(risk_signals x corridor_factor x vessel_size_factor + legitimacy_signals)
```

There is no hard cap on the upper bound; any score of 76 or above is classified as CRITICAL. A clean vessel in a standard corridor will typically score 0-15 points.

### Signal categories

The scoring configuration (`config/risk_scoring.yaml`) defines signals across these categories:

| Category | Examples | Point range |
|----------|----------|-------------|
| Gap duration | 2-4h: 5 pts, 8-12h: 25 pts, 24h+: 55 pts | 5 to 55 |
| Gap frequency | 2 gaps in 7d: 18 pts, 5 gaps in 30d: 50 pts | 18 to 50 |
| Spoofing | Circle pattern: 35 pts, MMSI reuse (100kn implied): 55 pts | 10 to 55 |
| Watchlist match | KSE shadow fleet: 30 pts, OFAC SDN: 50 pts, EU sanctions: 50 pts | 20 to 50 |
| Vessel metadata | Flag change: 15-35 pts, vessel age 25y+: 20 pts | 10 to 45 |
| STS indicators | STS event visible: 25 pts, one vessel dark: 15 pts | 8 to 35 |
| Dark zone context | Interior gap: -10 pts, exit with impossible jump: +35 pts | -10 to 35 |
| Behavioral | Russian port + gap + STS pattern: 40 pts, laid-up 60d+: 25 pts | 8 to 40 |
| Legitimacy (negative) | IG P&I club member: -15 pts, clean PSC record: -10 pts | -15 to -5 |

### Temporal decay

Recent anomalies are weighted more heavily than historical events. This is based on Windward Behavioral Intelligence and Kpler (2024) consensus research showing that recent anomalies are 2x more predictive of active evasion than events older than 90 days. The temporal decay multiplier is applied to individual signal scores before aggregation.

| Recency | Multiplier | Rationale |
|---------|------------|-----------|
| Last 7 days | 2.0x | Active evasion window -- highest predictive value |
| Last 30 days | 1.5x | Recent pattern -- still operationally relevant |
| Last 90 days | 1.0x (baseline) | Standard reference period |
| Older than 90 days | 0.8x | Historical decay -- event may no longer reflect current operations |

This mechanism ensures that a vessel with a single old incident gradually returns to a lower score over time, while a vessel with ongoing anomalous behavior maintains or increases its score.

### Data completeness cap

Vessels with limited tracking history (fewer than 50 AIS observations or less than 14 days since first seen) accumulate points from data absence signals (no P&I insurance on file, no port call history, new MMSI) without any actual suspicious behavior being observed. To prevent under-tracked vessels from reaching CRITICAL scores based solely on missing data, their scores are capped at 50 (the ceiling of the MEDIUM band) unless high-confidence behavioral signals fire.

### Score bands

| Score | Band | Meaning | Appropriate action |
|-------|------|---------|-------------------|
| 0-20 | LOW | Normal operational pattern. A clean vessel in a standard corridor will score in this range. | No action. Log for baseline. |
| 21-50 | MEDIUM | One or more signals warrant a closer look. Could be a coverage gap, equipment issue, or early indicator. | Check satellite imagery. Review vessel history. Do not publish without further verification. |
| 51-75 | HIGH | Multiple corroborating signals. The pattern is consistent with documented shadow fleet behavior. | Publication-ready only with analyst review, satellite cross-check, and independent vessel research. |
| 76-100 | CRITICAL | Strong cluster of shadow fleet indicators: long gaps, spoofing, watchlist match, STS activity, high-risk corridor. | Escalate. Verify with commercial satellite imagery. Consult maritime law expert before making legal claims. |

Scores are ordinal rankings, not probabilities. A score of 30 does not mean "30% chance of evasion." Two vessels with the same score may have very different risk profiles. Always read the score breakdown, which lists every signal that fired and its weight.

### Cross-detector corroboration

When multiple independent detector families produce findings for the same vessel, a corroboration bonus is applied: +10 points for 2 detector families, +20 for 3, +30 for 4 or more. This bonus is applied after multipliers so it adds exact points without amplification.

### False positive suppression

Several mechanisms reduce noise from legitimate vessel operations:

- Non-commercial vessel types (pilot, SAR, tug, port tender, law enforcement) have their scores capped at 30 to prevent them from reaching MEDIUM or HIGH bands.
- EU/NATO-flagged vessels receive additional legitimacy discounts and have data-absence signals suppressed, since missing enrichment data for these vessels typically indicates incomplete data rather than suspicious behavior.
- The corridor multiplier is capped at 1.0x for low-risk flag vessels to prevent legitimate EU maritime traffic from receiving inflated scores.

---

## 5. Validation Framework

### Ground truth sources

RadianceFleet's validation harness compares risk scoring predictions against three ground truth datasets:

- **KSE shadow fleet list.** The Kyiv School of Economics maintains a list of vessels identified as operating in the Russian shadow fleet. This serves as the primary positive-class ground truth, though it is a proxy label (KSE identification methodology is independent and may use different criteria).
- **OFAC SDN list.** The U.S. Treasury's Specially Designated Nationals list includes sanctioned vessels. Vessels on this list are treated as confirmed positives.
- **Clean baseline.** A curated set of vessels with no known sanctions involvement, used as negative-class ground truth to measure false positive rates.

These are proxy labels, not a gold-standard maritime anomaly benchmark. Results should be interpreted as directional guidance for scoring calibration, not as absolute accuracy metrics.

### Metrics

The validation harness computes:

- **Confusion matrix.** True positives (known shadow fleet vessel scored HIGH or CRITICAL), false positives (clean vessel scored HIGH or CRITICAL), true negatives (clean vessel scored LOW or MEDIUM), false negatives (known shadow fleet vessel scored LOW or MEDIUM).
- **Precision.** Of vessels flagged as HIGH/CRITICAL, what fraction are actually on shadow fleet lists.
- **Recall.** Of known shadow fleet vessels, what fraction does the tool flag as HIGH/CRITICAL.
- **F2 score.** The F-beta score with beta=2, which weights recall twice as heavily as precision. F2 is chosen over F1 because for an investigative triage tool, missing a real shadow fleet vessel (false negative) is more costly than flagging a clean vessel for review (false positive). Analysts can dismiss false positives; they cannot investigate vessels the tool failed to surface.
- **PR-AUC.** Precision-Recall Area Under Curve, computed via trapezoidal integration across score thresholds. This provides a threshold-independent measure of scoring quality -- a high PR-AUC means the scoring engine ranks shadow fleet vessels above clean vessels regardless of where the band boundaries are set.

### Analyst feedback integration

The harness tracks false positive rates by score band using analyst review status. When analysts mark alerts as `dismissed`, the tool computes the FP rate within each band (LOW, MEDIUM, HIGH, CRITICAL). This feedback loop identifies whether specific score ranges have unacceptable noise levels and guides threshold adjustment.

### Detector correlation analysis

The validation framework analyzes which detectors tend to fire together and which produce independent signal. Highly correlated detectors (e.g., gap duration and gap frequency will naturally co-occur) provide less incremental information than uncorrelated detectors (e.g., spoofing detection and watchlist match). This analysis informs the cross-detector corroboration bonus and helps identify redundant signals.

### Threshold sweep

The harness performs a threshold sweep across all possible score cutoffs to identify the optimal boundary between "flag for investigation" and "no action." At each threshold, it computes precision, recall, and F2 score against the ground truth labels. The resulting precision-recall curve is used to select scoring band boundaries that maximize recall (surfacing shadow fleet vessels) while keeping false positive rates manageable for analyst workflows.

---

## 6. Known Limitations

### Coverage gaps by region

RadianceFleet is most reliable in the Baltic Sea and Turkish Straits, where free AIS sources provide good coverage. In the Black Sea, AIS data is actively falsified near Russian-controlled areas, and free sources are unreliable. In the Persian Gulf, no adequate free AIS source exists. For investigations in these regions, commercial AIS data (Spire Global, exactEarth) is necessary for credible results. Open-ocean gaps should always be treated with caution, as most apparent gaps reflect satellite revisit intervals rather than vessel behavior. See the coverage table in Section 2 for the full regional breakdown.

### Common false positive sources

| Scenario | Why it triggers a score | Why it may be innocent |
|----------|------------------------|----------------------|
| Gap in GPS jamming zone | Duration-based gap score fires | Jamming causes involuntary AIS loss for thousands of vessels daily |
| Class B vessel at sea | Gap exceeds Class B interval threshold | Class B devices have longer normal intervals; poor satellite coverage amplifies this |
| Vessel at anchor outside port | Irregular transmission pattern scores as loitering | Anchoring vessels often have intermittent AIS due to low power mode |
| STS detection near port | Proximity + low speed triggers STS score | Legitimate STS transfers with Flag State notification are routine |
| Old vessel with flag change | Age + flag change + high-risk registry scores compound | Flag changes are common for commercial reasons (tax, regulation) |
| Feed outage | Multiple vessels show simultaneous gaps | Infrastructure issue, not coordinated vessel behavior |

### Data freshness constraints

- DMA Baltic archives cover 2006-2016. More recent Baltic data requires aisstream.io or commercial sources.
- aisstream.io provides real-time data only with no historical archive. Time-windowed batches must be exported manually.
- GFW vessel detection data has variable update lag depending on region and satellite revisit schedule.
- If multiple unrelated vessels in the same region all show AIS gaps starting at the same time, the cause is almost certainly a feed outage or receiver downtime, not coordinated vessel behavior.

### Identity resolution limitations

Identity merge detection relies on observed MMSI, IMO, and call sign relationships. It cannot detect identity changes that occur entirely outside the tool's observation window or in regions with no AIS coverage. Vessels that adopt a completely new identity with no overlapping transmissions will not be linked. The BFS merge chain algorithm requires at least one shared identifier (MMSI, IMO, or position-time coincidence) to establish a connection.

### Scoring is heuristic, not probabilistic

Risk scores are the sum of weighted heuristic signals, not outputs of a calibrated probabilistic model. The weights in `config/risk_scoring.yaml` are informed by investigative research (KSE, C4ADS, Windward, GFW peer-reviewed literature) and tuned against ground truth datasets, but they are not derived from a statistical training process. Scores should be treated as ordinal rankings for triage prioritization, not as probabilities or confidence levels.

### What RadianceFleet cannot detect

- Identity changes that occur entirely outside the tool's observation window or in regions with no AIS coverage.
- Cargo contents, origin, or destination -- AIS carries no cargo information.
- Beneficial ownership chains -- the tool can flag ownership-related risk signals but cannot resolve the full corporate structure behind a vessel.
- Intent -- equipment failure, GPS jamming, crew error, and deliberate evasion all produce identical AIS patterns.
- Feed-level outages are not currently detected automatically; analysts should check whether gap clusters are geographically and temporally correlated before attributing them to individual vessel behavior.

---

## 7. Interpretation Guidelines

### Score bands and what they mean

- **LOW (0-20):** Normal operational pattern. The vessel's AIS behavior is consistent with legitimate commercial operations. No action required.
- **MEDIUM (21-50):** One or more anomaly signals fired. This could reflect a genuine coverage gap, equipment issue, or early indicator of suspicious behavior. Investigate further before drawing any conclusions. Check satellite imagery and review vessel history.
- **HIGH (51-75):** Multiple corroborating signals from independent detectors. The pattern is consistent with documented shadow fleet behavior. Publication is appropriate only after analyst review, satellite cross-check, and independent vessel research through public databases (MarineTraffic, Equasis, OpenCorporates).
- **CRITICAL (76+):** Strong cluster of shadow fleet indicators across multiple detector families. Escalate for priority investigation. Verify with commercial satellite imagery if possible. Consult a maritime law expert before making sanctions or legal claims.

### Anomaly indicator, not guilt indicator

A RadianceFleet score identifies that a vessel's AIS pattern deviates from normal commercial behavior in ways that are consistent with patterns historically associated with shadow fleet operations. It does not identify guilt, intent, or legal violation. The same AIS pattern can result from equipment failure, GPS jamming, crew error, poor satellite coverage, or deliberate evasion. The tool cannot distinguish between these causes.

### Recommended workflow

1. **Triage.** Use score bands to prioritize which vessels to investigate first. Start with CRITICAL and HIGH alerts.
2. **Investigate.** Read the score breakdown for each alert. Understand which signals fired and their individual weights. Check whether the alert region has known coverage or jamming issues.
3. **Verify independently.** Cross-reference with satellite imagery (Sentinel-1 SAR via Copernicus Browser), vessel identity databases (MarineTraffic, VesselFinder, Equasis), ownership records (OpenCorporates, ICIJ Offshore Leaks), and watchlist databases.
4. **Apply analyst judgment.** Set the alert status to `under_review`, `confirmed`, or `dismissed`. Add analyst notes explaining verification steps and conclusions.
5. **Export with context.** Evidence cards include the mandatory disclaimer and the full score breakdown. Preserve both in any downstream reporting.

### Analyst review gate

RadianceFleet enforces a mandatory analyst review gate:

- Alerts start in `new` status.
- Evidence cards cannot be exported until an analyst changes the status to `under_review`, `confirmed`, or `dismissed`.
- The analyst must add notes explaining what they verified and how.

This is a safety mechanism, not bureaucratic friction. Automated scoring produces false positives. Every alert requires human judgment before it becomes part of a published investigation.

### Publication standards

- Never publish findings based solely on RadianceFleet output.
- Always verify through at least one independent source (satellite imagery, vessel registry, or human intelligence).
- Preserve the mandatory disclaimer on all exported evidence cards.
- Describe scores as "anomaly indicators" or "patterns consistent with shadow fleet behavior," not as evidence of sanctions violations.
- When reporting on specific vessels, note the score breakdown and which signals contributed, not just the total score.
- Acknowledge coverage limitations for the region where the alert occurred.
- Cross-reference watchlists -- a watchlist match is an indicator, not a conviction.
- Consult a maritime law expert before making sanctions or legal claims.

### Satellite imagery verification

Satellite imagery is the strongest independent verification for AIS gap alerts. RadianceFleet includes a satellite check workflow (`radiancefleet satellite prepare --alert <id>`) that generates a bounding box and time window for checking Sentinel-1 SAR imagery via the Copernicus Browser. SAR can detect vessels regardless of weather or time of day, though resolution is limited and vessel identification requires position correlation with the gap event's drift ellipse.

---

## Further Reading

- UNODC -- Guidelines on the Use of AIS Data for Law Enforcement
- C4ADS -- Shadow Shipping Research Methodology
- OCCRP -- AIS Data Verification Guide
- Global Fishing Watch -- Vessel Detection Methodology Documentation
- SkyTruth -- AIS Falsification Research (Black Sea, 2025)
- KSE Institute -- Shadow Fleet Vessel Identification Reports
- GFW Science Advances (2022) -- EEZ Boundary Proximity as Predictor of Intentional AIS Disabling
