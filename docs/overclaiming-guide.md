# Responsible Use Guide: Avoiding Overclaiming

**This is the most important document in this repository.**

RadianceFleet is a triage tool. It identifies patterns that *warrant investigation* — it does not prove sanctions violations, criminal activity, or intent. Misrepresenting its output can cause serious harm: incorrect reporting, reputational damage to innocent operators, and undermining of legitimate investigations.

---

## What a risk score means

A score of 85 means: *"This gap has characteristics consistent with patterns we have assigned weights to."*

A score of 85 does **not** mean:
- The vessel was conducting a sanctioned cargo transfer
- The vessel was behaving illegally
- The vessel operator intended to evade monitoring
- You can publish a story based on this alone

The same score can arise from:
- Equipment failure in a poor-coverage region
- Crew error (AIS power cycle)
- Legitimate STS transfer with all proper notifications
- GPS jamming affecting the vessel's ability to transmit
- A vessel transiting a corridor we've assigned high weight to, for unrelated reasons

---

## Common false positives

### 1. GPS jamming zones

Russia operates extensive GPS jamming near its coastlines, in the Black Sea, and in the Baltic. Vessels in these areas frequently experience AIS disruption that looks identical to deliberate deactivation. RadianceFleet marks corridors with `is_jamming_zone: true` and reduces gap scores by 10 points, but this is not a complete correction.

**Before reporting:** Verify whether the gap occurred in a known jamming zone. Check [gpsjam.org](https://gpsjam.org) for the time period.

### 2. Class B AIS devices

Small vessels (<1000 DWT) use Class B AIS, which is not legally required to transmit continuously. Class B devices may have coverage gaps at sea that are entirely normal. RadianceFleet applies a noise filter (ignores gaps <3 minutes for Class B), but longer gaps for small vessels in areas with poor terrestrial coverage are not inherently suspicious.

### 3. Poor satellite AIS coverage at sea

Terrestrial AIS range is typically 40–60nm from shore. At sea, coverage depends on satellite AIS providers. `aisstream.io` (our primary free source) has patchy open-ocean coverage. A "gap" in an open-ocean corridor may simply be a coverage gap, not a vessel deactivation.

### 4. Anchor areas and anchorages

Vessels waiting at anchor outside major ports often have irregular AIS transmission patterns. RadianceFleet attempts to suppress anchor-spoofing false positives using the ports table, but this suppression is incomplete.

### 5. Legitimate STS transfers

Ship-to-ship transfers are routinely conducted legally, with proper Flag State notifications, for fuel, provisions, and cargo. Detection of proximity alone is not evidence of evasion. The STS detector flags Phase B (both vessels dark) but this requires cross-checking with vessel ownership, cargo declarations, and Flag State records.

---

## Regional AIS coverage

| Region | Free AIS Quality | Notes |
|--------|-----------------|-------|
| Baltic Sea | GOOD | DMA CSV + aisstream.io — good terrestrial coverage |
| Turkish Straits | GOOD | aisstream.io — well-monitored chokepoint |
| Mediterranean | MODERATE | Good near ports, sparse open sea |
| Singapore Strait | PARTIAL | Gaps in outer anchorage areas |
| Far East / Nakhodka | PARTIAL | Limited outside port approaches |
| Black Sea | POOR | AIS heavily falsified in Russian-controlled areas |
| Persian Gulf | NONE | No free source — commercial subscription required |

**If your investigation targets the Black Sea or Persian Gulf:** You need commercial AIS data (Spire Maritime, exactEarth, MarineTraffic historical) for any reliable gap detection. RadianceFleet's free-source pipeline will produce many false positives in these regions.

---

## Required steps before publication

1. **Set alert status** to `under_review` or higher — never export a `new` alert
2. **Add analyst notes** explaining what you verified and how
3. **Check satellite imagery** (use the satellite prepare command to generate Copernicus URLs)
4. **Independently verify** vessel ownership, cargo, and any supporting documentation
5. **Cross-reference** the vessel in public shipping databases (MarineTraffic, VesselFinder, EquasisWeb)
6. **Consult a maritime law expert** before making sanctions or legal claims
7. **Export the evidence card** which includes the mandatory disclaimer

---

## What RadianceFleet cannot do

| Capability | Status |
|-----------|--------|
| Real-time AIS monitoring | No — requires continuous data feed (aisstream.io WebSocket or commercial) |
| Cargo identification | No — requires declaration databases (not publicly available) |
| Vessel re-identification from satellite | No — v1.1 feature only |
| Legal analysis | No — consult maritime law counsel |
| Ownership tracing (beneficial ownership) | No — use OpenCorporates, ICIJ databases |
| Proving intent | No — impossible from AIS alone |

---

## The disclaimer

Every exported evidence card includes:

> *DISCLAIMER: This is investigative triage, not a legal determination. This tool identifies patterns warranting further investigation. No conclusions about sanctions violations or criminal activity should be drawn from this output without independent expert verification.*

This disclaimer must be preserved in any downstream reporting that cites RadianceFleet output.

---

## Further reading

- UNODC [Guidelines on the Use of AIS Data](https://www.unodc.org) for law enforcement
- C4ADS [Shadow Shipping research methodology](https://c4ads.org)
- OCCRP [AIS data verification guide](https://occrp.org)
