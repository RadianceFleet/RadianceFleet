import { useState } from "react";

const API_BASE = (import.meta.env.VITE_API_URL ?? "") + "/api/v1";

const BEHAVIOR_TYPES = [
  { value: "", label: "Select behavior type..." },
  { value: "AIS_MANIPULATION", label: "AIS Signal Manipulation" },
  { value: "DARK_PERIOD", label: "Suspicious Dark Period" },
  { value: "SUSPICIOUS_STS", label: "Suspicious Ship-to-Ship Transfer" },
  { value: "FLAG_CHANGE", label: "Flag/Identity Change" },
  { value: "OTHER", label: "Other Suspicious Behavior" },
];

export function TipForm({ mmsi, vesselName }: { mmsi: string; vesselName: string }) {
  const [open, setOpen] = useState(false);
  const [behaviorType, setBehaviorType] = useState("");
  const [detailText, setDetailText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [email, setEmail] = useState("");
  const [website, setWebsite] = useState(""); // honeypot field
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (submitted) {
    return (
      <div
        style={{
          padding: 12,
          background: "var(--bg-card)",
          borderRadius: "var(--radius)",
          border: "1px solid var(--border)",
          fontSize: 13,
          color: "var(--text-muted)",
        }}
      >
        Thank you — analysts will review this tip. Tips do not directly affect risk scores.
      </div>
    );
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          fontSize: 13,
          padding: "6px 14px",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          cursor: "pointer",
          background: "transparent",
          color: "var(--text-muted)",
        }}
      >
        Flag this vessel
      </button>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!behaviorType) {
      setError("Please select a behavior type.");
      return;
    }
    if (detailText.length < 50) {
      setError("Please provide at least 50 characters of detail.");
      return;
    }
    if (detailText.length > 500) {
      setError("Detail text must be 500 characters or less.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/tips/vessel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mmsi,
          behavior_type: behaviorType,
          detail_text: detailText,
          source_url: sourceUrl || null,
          submitter_email: email || null,
          website, // honeypot -- backend rejects if non-empty
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Submission failed. Please try again.");
      } else {
        setSubmitted(true);
      }
    } catch {
      setError("Network error. Please try again.");
    }
    setLoading(false);
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "7px",
    boxSizing: "border-box",
    background: "var(--bg)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "inherit",
    fontSize: 13,
  };

  return (
    <div
      style={{
        padding: 16,
        background: "var(--bg-card)",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--border)",
      }}
    >
      <h4 style={{ margin: "0 0 12px", fontSize: 14 }}>
        Flag suspicious behavior for {vesselName}
      </h4>
      <form onSubmit={handleSubmit}>
        {/* Honeypot -- must stay hidden from real users */}
        <input
          name="website"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
          style={{ display: "none" }}
          aria-hidden="true"
          tabIndex={-1}
          autoComplete="off"
        />

        <div style={{ marginBottom: 10 }}>
          <select
            value={behaviorType}
            onChange={(e) => setBehaviorType(e.target.value)}
            style={inputStyle}
          >
            {BEHAVIOR_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: 4 }}>
          <textarea
            value={detailText}
            onChange={(e) => setDetailText(e.target.value)}
            placeholder="Describe the suspicious behavior (minimum 50 characters)..."
            rows={4}
            style={{ ...inputStyle, resize: "vertical" }}
          />
        </div>
        <div
          style={{
            fontSize: 11,
            textAlign: "right",
            marginBottom: 10,
            color: detailText.length > 500 ? "var(--score-critical)" : "var(--text-dim)",
          }}
        >
          {detailText.length}/500
        </div>

        <div style={{ marginBottom: 10 }}>
          <input
            type="url"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="Source URL (optional -- marinetraffic.com, globalfishingwatch.org, etc.)"
            style={inputStyle}
          />
        </div>

        <div style={{ marginBottom: 10 }}>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Your email (optional -- for analyst follow-up)"
            style={inputStyle}
          />
        </div>

        <p style={{ fontSize: 11, color: "var(--text-dim)", margin: "0 0 10px" }}>
          Do not include personal information about individuals.
        </p>

        {error && (
          <p style={{ color: "var(--score-critical)", fontSize: 13, margin: "0 0 10px" }}>
            {error}
          </p>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => setOpen(false)}
            style={{
              padding: "6px 14px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              cursor: "pointer",
              background: "transparent",
              color: "inherit",
              fontSize: 13,
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={loading}
            style={{
              padding: "6px 14px",
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--radius)",
              cursor: loading ? "not-allowed" : "pointer",
              fontSize: 13,
            }}
          >
            {loading ? "Submitting..." : "Submit tip"}
          </button>
        </div>
      </form>
    </div>
  );
}
