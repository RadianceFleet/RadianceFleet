import { useState } from "react";
import { useNarrative } from "../hooks/useNarrative";
import { card, sectionHead, btnStyle } from "../styles/tables";

interface NarrativePanelProps {
  alertId: string;
}

const formatOptions = [
  { value: "text", label: "Text" },
  { value: "md", label: "Markdown" },
  { value: "html", label: "HTML" },
] as const;

function strengthColor(strength: number): string {
  if (strength >= 0.7) return "#27ae60";
  if (strength >= 0.4) return "#f39c12";
  return "#e74c3c";
}

export function NarrativePanel({ alertId }: NarrativePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [format, setFormat] = useState<string>("md");
  const { data, isLoading, error } = useNarrative(
    expanded ? alertId : undefined,
    format
  );

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
          Investigation Narrative {expanded ? "\u25B2" : "\u25BC"}
        </h3>
      </div>

      {expanded && (
        <div style={{ marginTop: 12 }}>
          {/* Format toggle */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            {formatOptions.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setFormat(opt.value)}
                style={{
                  ...btnStyle,
                  background:
                    format === opt.value
                      ? "var(--bg-accent, #2563eb)"
                      : "var(--bg-card)",
                  color:
                    format === opt.value ? "#fff" : "var(--text-muted)",
                  fontWeight: format === opt.value ? 600 : 400,
                }}
              >
                {opt.label}
              </button>
            ))}

            {data && (
              <button
                style={{ ...btnStyle, marginLeft: "auto" }}
                onClick={() => {
                  navigator.clipboard.writeText(data.narrative);
                }}
              >
                Copy
              </button>
            )}
          </div>

          {/* Strength indicator */}
          {data && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                  color: "var(--text-dim)",
                  marginBottom: 4,
                }}
              >
                <span>Narrative Strength</span>
                <span style={{ color: strengthColor(data.strength) }}>
                  {(data.strength * 100).toFixed(0)}%
                </span>
              </div>
              <div
                style={{
                  height: 6,
                  background: "var(--bg-base)",
                  borderRadius: 3,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: `${data.strength * 100}%`,
                    background: strengthColor(data.strength),
                    borderRadius: 3,
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
            </div>
          )}

          {/* Completeness warnings */}
          {data && data.warnings.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              {data.warnings.map((w, i) => (
                <div
                  key={i}
                  style={{
                    background: "rgba(243, 156, 18, 0.1)",
                    border: "1px solid rgba(243, 156, 18, 0.3)",
                    borderRadius: "var(--radius)",
                    padding: "8px 12px",
                    fontSize: 12,
                    color: "#f39c12",
                    marginBottom: 4,
                  }}
                >
                  {w}
                </div>
              ))}
            </div>
          )}

          {/* Content */}
          {isLoading && (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              Generating narrative...
            </p>
          )}
          {error && (
            <p style={{ fontSize: 13, color: "var(--score-critical)" }}>
              Failed to generate narrative.
            </p>
          )}
          {data && (
            <div
              style={{
                background: "var(--bg-base)",
                borderRadius: "var(--radius)",
                padding: 16,
                fontSize: 13,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                overflowX: "auto",
                maxHeight: 600,
                overflowY: "auto",
              }}
            >
              {format === "html" ? (
                <div dangerouslySetInnerHTML={{ __html: data.narrative }} />
              ) : (
                data.narrative
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
