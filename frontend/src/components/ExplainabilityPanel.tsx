import { useState } from "react";
import {
  useExplainability,
  type SignalExplanation,
} from "../hooks/useExplainability";
import { WaterfallChart } from "./charts/WaterfallChart";
import { card, sectionHead, btnStyle } from "../styles/tables";

interface ExplainabilityPanelProps {
  alertId: string;
}

const TIER_COLORS: Record<number, { bg: string; border: string; label: string }> = {
  1: { bg: "rgba(39, 174, 96, 0.1)", border: "rgba(39, 174, 96, 0.3)", label: "High confidence" },
  2: { bg: "rgba(243, 156, 18, 0.1)", border: "rgba(243, 156, 18, 0.3)", label: "Pattern matched" },
  3: { bg: "rgba(149, 165, 166, 0.1)", border: "rgba(149, 165, 166, 0.3)", label: "Auto-generated" },
};

const CATEGORY_LABELS: Record<string, string> = {
  behavioral: "Behavioral",
  spatial: "Spatial",
  temporal: "Temporal",
  identity: "Identity",
  sanctions: "Sanctions",
  environmental: "Environmental",
};

function SignalCard({ signal }: { signal: SignalExplanation }) {
  const style = TIER_COLORS[signal.tier] ?? TIER_COLORS[3];
  return (
    <div
      style={{
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: "var(--radius, 6px)",
        padding: "8px 12px",
        marginBottom: 6,
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ fontWeight: 600, color: "var(--text-bright, #fff)" }}>
          {signal.key.replace(/_/g, " ")}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-dim, #888)" }}>
          {signal.value >= 0 ? "+" : ""}{signal.value} pts &middot; {style.label}
        </span>
      </div>
      <div style={{ color: "var(--text-muted, #aaa)" }}>{signal.explanation}</div>
    </div>
  );
}

export function ExplainabilityPanel({ alertId }: ExplainabilityPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, error } = useExplainability(expanded ? alertId : undefined);

  return (
    <section style={card}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <h3 style={{ ...sectionHead, margin: 0 }}>
          Score Explainability {expanded ? "\u25B2" : "\u25BC"}
        </h3>
      </div>

      {expanded && (
        <div style={{ marginTop: 12 }}>
          {isLoading && (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              Generating explanation...
            </p>
          )}
          {error && (
            <p style={{ fontSize: 13, color: "var(--score-critical, #e74c3c)" }}>
              Failed to generate explanation.
            </p>
          )}
          {data && (
            <>
              {/* Summary */}
              <div
                style={{
                  background: "var(--bg-base, #111)",
                  borderRadius: "var(--radius, 6px)",
                  padding: 12,
                  marginBottom: 16,
                  fontSize: 13,
                  lineHeight: 1.6,
                  color: "var(--text-muted, #ccc)",
                }}
              >
                {data.summary}
              </div>

              {/* Score badge */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  marginBottom: 16,
                }}
              >
                <span
                  style={{
                    fontSize: 24,
                    fontWeight: 700,
                    color: data.total_score >= 76
                      ? "var(--score-critical, #e74c3c)"
                      : data.total_score >= 51
                        ? "var(--score-high, #e67e22)"
                        : data.total_score >= 21
                          ? "var(--score-medium, #f39c12)"
                          : "var(--score-low, #27ae60)",
                  }}
                >
                  {data.total_score}
                </span>
                <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
                  total risk score from {data.signals.length} signals
                </span>
              </div>

              {/* Waterfall chart */}
              {data.waterfall.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <h4
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: 1,
                      marginBottom: 8,
                    }}
                  >
                    Score Waterfall
                  </h4>
                  <WaterfallChart data={data.waterfall} />
                </div>
              )}

              {/* Signals grouped by category */}
              {Object.entries(data.categories).map(([cat, signals]) => (
                <div key={cat} style={{ marginBottom: 16 }}>
                  <h4
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: 1,
                      marginBottom: 8,
                    }}
                  >
                    {CATEGORY_LABELS[cat] ?? cat} ({signals.length})
                  </h4>
                  {signals.map((s) => (
                    <SignalCard key={s.key} signal={s} />
                  ))}
                </div>
              ))}

              {/* Copy button */}
              <button
                style={btnStyle}
                onClick={() => {
                  const text = data.signals
                    .map((s) => `[${s.category}] ${s.key}: ${s.value} pts - ${s.explanation}`)
                    .join("\n");
                  navigator.clipboard.writeText(text);
                }}
              >
                Copy Explanation
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
