---
name: Feature request
about: Propose a new feature or enhancement for RadianceFleet
labels: enhancement
---

## Use Case

**Who needs this?**
<!-- Describe the analyst, journalist, or researcher role that would use this feature.
     e.g. "An OSINT analyst who needs to track a specific vessel across MMSI changes" -->

**What problem does it solve?**
<!-- Describe the current limitation or workflow pain point.
     e.g. "Currently there is no way to persist a named search for a vessel and receive alerts when it reappears." -->

**How does it fit the project's mission?**
<!-- RadianceFleet is focused on maritime anomaly detection for Russian shadow fleet triage.
     Briefly explain how this feature serves that mission. -->

## Proposed Change

**API change (if applicable)**:

```
# Example endpoint sketch
POST /api/v1/search-missions
{
  "name": "Hunt LUCKY STAR",
  "target_mmsi": "273338710",
  "target_imo": "9284673",
  "notes": "MMSI may have changed — also match by name"
}
```

**CLI change (if applicable)**:

```bash
radiancefleet search-mission create --name "Hunt LUCKY STAR" --mmsi 273338710
```

**Schema change (if applicable)**:
<!-- Describe any new database tables or columns needed. Check existing v1.1 stubs
     (VesselTargetProfile, SearchMission, HuntCandidate, DarkVesselDetection) before
     proposing a new model — the stub may already exist. -->

## Data Requirements

<!-- Does this feature need a new data source, new AIS fields, or a new external API?
     If so, describe what data is required and where it comes from. -->

## Acceptance Criteria

- [ ] <!-- Specific, testable criterion 1 -->
- [ ] <!-- Specific, testable criterion 2 -->
- [ ] <!-- Unit test added -->
- [ ] <!-- CLI command documented in docs/CLI_REFERENCE.md (if applicable) -->
- [ ] <!-- API endpoint documented in docs/API.md (if applicable) -->

## Alternatives Considered

<!-- Have you considered any workarounds or alternative approaches? Why did you prefer this one? -->

## Additional Context

<!-- Screenshots, links to relevant PRD sections (PRD.md §xx), related issues, etc. -->
