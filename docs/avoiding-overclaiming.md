# Avoiding Overclaiming

**This is required reading before publishing any findings based on RadianceFleet output.**

RadianceFleet is an investigative triage tool. It identifies patterns that warrant further investigation. It does not prove sanctions violations, criminal activity, or intent. Misrepresenting its output can cause serious harm: incorrect reporting, reputational damage to innocent vessel operators, and undermining of legitimate investigations.

---

## What RadianceFleet Proves vs. What It Does Not

### What it can show

- A vessel had a gap in AIS transmissions at a specific time and location
- The gap duration and location match patterns associated with shadow fleet behavior
- The vessel's metadata (age, flag, ownership changes) are consistent with known shadow fleet characteristics
- The vessel appears on a sanctions or watchlist database
- Two vessels were in sustained proximity consistent with a ship-to-ship transfer
- A vessel exhibited loitering behavior in a monitored corridor

### What it cannot show

- **Why** the AIS gap occurred (equipment failure, crew error, GPS jamming, and deliberate deactivation all look the same in the data)
- Whether a cargo transfer actually took place during a gap or STS event
- Whether any cargo was Russian-origin crude oil
- Whether the vessel operator intended to evade monitoring
- Whether any law was broken
- The beneficial ownership chain behind a vessel

A high risk score means: "This gap has characteristics consistent with patterns we have assigned weights to." It does not mean the vessel was conducting sanctioned activity.

---

## Score Interpretation

RadianceFleet assigns a composite risk score (0-100+) to each AIS gap event. The score is the sum of weighted signals from gap duration, spoofing indicators, vessel metadata, corridor context, and behavioral patterns.

### Score bands

| Band | Score | What it means | Appropriate action |
|------|-------|---------------|-------------------|
| Low | 0-20 | Normal operational pattern. A clean vessel in a standard corridor will score in this range. | No action. Log for baseline. |
| Medium | 21-50 | One or more signals warrant a closer look. Could be a coverage gap, equipment issue, or early indicator. | Check satellite imagery. Review vessel history. Do not publish without further verification. |
| High | 51-75 | Multiple corroborating signals. The pattern is consistent with documented shadow fleet behavior. | Publication-ready only with analyst review, satellite cross-check, and independent vessel research. |
| Critical | 76+ | Strong cluster of shadow fleet indicators: long gaps, spoofing, watchlist match, STS activity, high-risk corridor. | Escalate. Verify with commercial satellite imagery if possible. Consult maritime law expert before making legal claims. |

### What each score does NOT mean

- A score of 30 does not mean "30% chance of evasion." The score is an ordinal ranking, not a probability.
- A score above 75 does not confirm sanctions violation. It means the vessel's AIS pattern matches multiple heuristics that have historically correlated with shadow fleet operations.
- Two vessels with the same score may have very different risk profiles. Always read the score breakdown, which lists every signal that fired and its weight.

---

## Limitations of AIS Data

### AIS is self-reported

AIS transponders broadcast vessel identity, position, speed, and course. This data is self-reported by the vessel and can be manipulated. A vessel can turn off its transponder, change its reported MMSI, or broadcast false positions. RadianceFleet detects some of these manipulations (spoofing detection), but cannot detect all forms of tampering.

### Coverage is not global

AIS signals are received by terrestrial receivers (range: 40-60 nautical miles from shore) and by satellites. Terrestrial coverage is excellent near coastlines and in busy shipping lanes, but nonexistent in open ocean. Satellite AIS coverage depends on the constellation and provider. Free sources (aisstream.io, DMA archives) have significant gaps:

- **Baltic Sea**: Good terrestrial and satellite coverage
- **Black Sea**: AIS data is heavily falsified near Russian-controlled areas (documented by SkyTruth, 2025). Free sources are unreliable.
- **Persian Gulf**: No adequate free AIS source. Commercial providers (Spire Global, exactEarth) are required.
- **Open ocean**: Satellite AIS has intermittent coverage. A "gap" in mid-ocean may be a satellite coverage gap, not a vessel deactivation.

### Class A vs. Class B transmission intervals

- **Class A** (required for vessels over 300 GT on international voyages): transmits every 2-10 seconds while underway, every 3 minutes at anchor. This is the standard for large tankers.
- **Class B** (used by smaller vessels under 300 GT): transmits every 30 seconds to 3 minutes. Longer intervals between transmissions are normal and do not indicate tampering.

RadianceFleet applies a Class B noise filter (ignores gaps under 180 seconds) and flags large tankers using Class B as a red flag (+50 points). However, Class B gaps in areas with poor terrestrial coverage are often innocent.

### GPS jamming creates false positives

Russia operates extensive GPS jamming near its coastlines, throughout the Black Sea, and in parts of the Baltic. The Strait of Hormuz is also heavily affected (1000+ vessels per day). Vessels in these areas frequently experience AIS disruption that looks identical to deliberate deactivation.

RadianceFleet marks corridors with `is_jamming_zone: true` and reduces gap scores by 10 points for gaps inside known jamming zones, but this is an incomplete correction. Before reporting on a gap in or near a known jamming area, check [gpsjam.org](https://gpsjam.org) for the specific time period.

---

## Why Analyst Review Is Mandatory

RadianceFleet enforces an analyst review gate (PRD requirement NFR7):

- Alerts start in `new` status
- Evidence cards cannot be exported until an analyst changes the status to `under_review`, `confirmed`, or `dismissed`
- The analyst must add notes explaining what they verified and how

This is not a bureaucratic friction -- it is a safety mechanism. Automated scoring produces false positives. Every alert requires human judgment before it becomes part of a published investigation.

### Required steps before publication

1. **Set alert status** to `under_review` or higher -- never export a `new` alert
2. **Add analyst notes** explaining your verification steps
3. **Check satellite imagery** using the satellite check package (`radiancefleet satellite prepare --alert <id>`)
4. **Verify vessel identity** in public databases (MarineTraffic, VesselFinder, Equasis)
5. **Check vessel ownership** for recent changes (OpenCorporates, ICIJ Offshore Leaks)
6. **Cross-reference watchlists** -- a watchlist match is an indicator, not a conviction
7. **Consult a maritime law expert** before making sanctions or legal claims
8. **Export the evidence card**, which includes the mandatory disclaimer

---

## Cross-Referencing with Satellite Imagery

Satellite imagery is the strongest independent verification for AIS gap alerts. If a vessel's AIS went dark but a satellite image shows the vessel at the reported gap location, the gap was likely a transmission failure, not evasion. Conversely, if a satellite image shows no vessel at the last known position during the gap window, the vessel may have moved while dark.

### How to use the satellite workflow

1. Run `radiancefleet satellite prepare --alert <id>` to generate a check package
2. The package includes:
   - A bounding box around the gap area (last known position expanded by maximum plausible distance)
   - A time window (gap start minus 1 hour to gap end plus 1 hour)
   - A pre-filled Copernicus Browser URL for Sentinel-1 SAR imagery
3. Open the Copernicus Browser URL and check for available Sentinel-1 scenes in the time window
4. Sentinel-1 SAR can detect vessels regardless of weather or time of day, but resolution is limited (ships appear as bright pixels, not identifiable images)
5. For higher-resolution confirmation, consider commercial SAR providers (Umbra, Capella Space) at $50-$400 per scene

### Limitations of satellite verification

- Sentinel-1 revisit time is 6-12 days at most latitudes. There may be no scene available during the gap window.
- SAR detects metallic objects on water. It cannot identify specific vessels by name or MMSI. Matching a SAR detection to a specific vessel requires correlating its position with the drift ellipse from the gap event.
- Optical satellites (Sentinel-2, Planet Labs) are affected by cloud cover. SAR is preferred for maritime monitoring.

---

## Common False Positives

| Scenario | Why it triggers a score | Why it may be innocent |
|----------|------------------------|----------------------|
| Gap in GPS jamming zone | Duration-based gap score fires | Jamming causes involuntary AIS loss for thousands of vessels daily |
| Class B vessel at sea | Gap exceeds Class B interval threshold | Class B devices have longer normal intervals; poor satellite coverage amplifies this |
| Vessel at anchor outside port | Irregular transmission pattern scores as loitering | Anchoring vessels often have intermittent AIS due to low power mode |
| STS detection near port | Proximity + low speed triggers STS score | Legitimate STS transfers with Flag State notification are routine |
| Old vessel with flag change | Age + flag change + high-risk registry | Flag changes are common for commercial reasons (tax, regulation) |

---

## The Mandatory Disclaimer

Every exported evidence card includes:

> *DISCLAIMER: This is investigative triage, not a legal determination. This tool identifies patterns warranting further investigation. No conclusions about sanctions violations or criminal activity should be drawn from this output without independent expert verification.*

This disclaimer must be preserved in any downstream reporting that cites RadianceFleet output.

---

## Further Reading

- UNODC -- Guidelines on the Use of AIS Data for law enforcement
- C4ADS -- Shadow Shipping research methodology
- OCCRP -- AIS data verification guide
- Global Fishing Watch -- Vessel detection methodology documentation
- SkyTruth -- AIS falsification research (Black Sea, 2025)
