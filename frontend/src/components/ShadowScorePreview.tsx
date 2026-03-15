import { useState } from "react";
import { apiFetch } from "../lib/api";

interface ShadowResult {
  alert_id: number;
  original_score: number;
  proposed_score: number;
  original_band: string;
  proposed_band: string;
}

interface Props {
  corridorId: number;
  overrides: Record<string, number>;
}

function bandColor(band: string): string {
  if (band === "critical") return "#ef4444";
  if (band === "high") return "#f59e0b";
  if (band === "medium") return "#3b82f6";
  return "#22c55e";
}

function scoreBand(score: number): string {
  if (score >= 76) return "critical";
  if (score >= 51) return "high";
  if (score >= 26) return "medium";
  return "low";
}

export function ShadowScorePreview({ corridorId, overrides }: Props) {
  const [results, setResults] = useState<ShadowResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handlePreview = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ShadowResult[]>(
        `/corridors/${corridorId}/shadow-score`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ overrides }),
        }
      );
      setResults(
        data.map((r) => ({
          ...r,
          original_band: r.original_band || scoreBand(r.original_score),
          proposed_band: r.proposed_band || scoreBand(r.proposed_score),
        }))
      );
    } catch {
      setError("Failed to generate shadow scores");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="shadow-preview" style={{ marginTop: "1rem" }}>
      <button
        onClick={handlePreview}
        disabled={loading}
        style={{
          padding: "0.5rem 1rem",
          backgroundColor: "#7c3aed",
          color: "white",
          border: "none",
          borderRadius: 4,
          cursor: loading ? "wait" : "pointer",
        }}
      >
        {loading ? "Computing..." : "Preview Impact"}
      </button>

      {error && <p style={{ color: "#ef4444", marginTop: "0.5rem" }}>{error}</p>}

      {results.length > 0 && (
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            marginTop: "0.75rem",
          }}
        >
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "0.5rem", borderBottom: "1px solid #e5e7eb" }}>Alert</th>
              <th style={{ textAlign: "right", padding: "0.5rem", borderBottom: "1px solid #e5e7eb" }}>Original</th>
              <th style={{ textAlign: "center", padding: "0.5rem", borderBottom: "1px solid #e5e7eb" }}>Band</th>
              <th style={{ textAlign: "right", padding: "0.5rem", borderBottom: "1px solid #e5e7eb" }}>Proposed</th>
              <th style={{ textAlign: "center", padding: "0.5rem", borderBottom: "1px solid #e5e7eb" }}>Band</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.alert_id}>
                <td style={{ padding: "0.5rem" }}>#{r.alert_id}</td>
                <td style={{ padding: "0.5rem", textAlign: "right" }}>
                  {r.original_score}
                </td>
                <td
                  style={{
                    padding: "0.5rem",
                    textAlign: "center",
                    color: bandColor(r.original_band),
                    fontWeight: 600,
                  }}
                >
                  {r.original_band}
                </td>
                <td style={{ padding: "0.5rem", textAlign: "right" }}>
                  {r.proposed_score}
                </td>
                <td
                  style={{
                    padding: "0.5rem",
                    textAlign: "center",
                    color: bandColor(r.proposed_band),
                    fontWeight: 600,
                  }}
                >
                  {r.proposed_band}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
