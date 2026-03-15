import { useState } from "react";

const SIGNAL_SECTIONS = [
  {
    section: "gap_duration",
    label: "Gap Duration",
    signals: [
      { key: "gap_duration_multiplier", label: "Duration Multiplier", default: 1.0 },
    ],
  },
  {
    section: "proximity",
    label: "Proximity",
    signals: [
      { key: "eez_proximity_weight", label: "EEZ Proximity Weight", default: 1.0 },
      { key: "sanctioned_port_weight", label: "Sanctioned Port Weight", default: 1.0 },
    ],
  },
  {
    section: "vessel_profile",
    label: "Vessel Profile",
    signals: [
      { key: "flag_risk_weight", label: "Flag Risk Weight", default: 1.0 },
      { key: "age_risk_weight", label: "Age Risk Weight", default: 1.0 },
    ],
  },
  {
    section: "behavioral",
    label: "Behavioral",
    signals: [
      { key: "sts_recency_weight", label: "STS Recency Weight", default: 1.0 },
      { key: "spoofing_weight", label: "Spoofing Weight", default: 1.0 },
    ],
  },
];

interface Props {
  overrides: Record<string, number>;
  onChange: (overrides: Record<string, number>) => void;
}

export function SignalOverrideEditor({ overrides, onChange }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["gap_duration"]));

  const toggleSection = (section: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  const handleChange = (key: string, value: string) => {
    const num = parseFloat(value);
    if (!isNaN(num)) {
      onChange({ ...overrides, [key]: num });
    }
  };

  return (
    <div data-testid="signal-override-editor">
      {SIGNAL_SECTIONS.map((section) => (
        <div
          key={section.section}
          style={{
            border: "1px solid #e5e7eb",
            borderRadius: 6,
            marginBottom: "0.75rem",
          }}
        >
          <button
            onClick={() => toggleSection(section.section)}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              width: "100%",
              padding: "0.75rem 1rem",
              background: "none",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "0.875rem",
            }}
          >
            {section.label}
            <span>{expanded.has(section.section) ? "\u25B2" : "\u25BC"}</span>
          </button>
          {expanded.has(section.section) && (
            <div style={{ padding: "0 1rem 0.75rem" }}>
              {section.signals.map((signal) => (
                <div
                  key={signal.key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.75rem",
                    marginBottom: "0.5rem",
                  }}
                >
                  <label
                    style={{
                      flex: 1,
                      fontSize: "0.8125rem",
                      color: "#374151",
                    }}
                  >
                    {signal.label}
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="5"
                    value={overrides[signal.key] ?? signal.default}
                    onChange={(e) => handleChange(signal.key, e.target.value)}
                    style={{
                      width: 80,
                      padding: "0.25rem 0.5rem",
                      border: "1px solid #d1d5db",
                      borderRadius: 4,
                      fontSize: "0.8125rem",
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
