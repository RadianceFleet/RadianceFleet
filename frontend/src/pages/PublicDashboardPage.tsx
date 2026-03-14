import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { EmptyState } from "../components/ui/EmptyState";
import {
  usePublicDashboard,
  usePublicTrends,
  type RecentAlert,
} from "../hooks/usePublicDashboard";

// ---------------------------------------------------------------------------
// Tier colour mapping
// ---------------------------------------------------------------------------

const TIER_COLORS: Record<string, string> = {
  high: "var(--score-high, #ef4444)",
  medium: "var(--score-medium, #f59e0b)",
  low: "var(--score-low, #22c55e)",
};

const TYPE_COLORS: Record<string, string> = {
  gap: "#3b82f6",
  spoofing: "#ef4444",
  sts: "#f59e0b",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PublicDashboardPage() {
  const { data, isLoading, error, refetch } = usePublicDashboard();
  const { data: trends } = usePublicTrends();

  if (isLoading) return <Spinner text="Loading public dashboard..." />;
  if (error) return <ErrorMessage error={error} subject="public dashboard" onRetry={refetch} />;
  if (!data) return <EmptyState title="No data" description="No public data available yet." />;

  const {
    vessel_count,
    alert_counts,
    detection_coverage,
    recent_alerts,
    trend_buckets,
    detections_by_type,
  } = data;

  const statCards = [
    { label: "Vessels Monitored", value: vessel_count, color: "var(--accent, #3b82f6)" },
    { label: "High-Risk Alerts", value: alert_counts.high, color: TIER_COLORS.high },
    { label: "Medium Alerts", value: alert_counts.medium, color: TIER_COLORS.medium },
    { label: "Monitored Zones", value: detection_coverage.monitored_zones, color: "var(--text-bright, #e2e8f0)" },
  ];

  const typeData = Object.entries(detections_by_type).map(([type, count]) => ({
    name: type.charAt(0).toUpperCase() + type.slice(1),
    count,
    fill: TYPE_COLORS[type] || "#6b7280",
  }));

  // Prefer 90-day trends from dedicated endpoint, fall back to 30-day from dashboard
  const trendData = trends?.days ?? trend_buckets;

  return (
    <div>
      <h2
        style={{
          margin: "0 0 0.25rem",
          fontSize: "1.25rem",
          color: "var(--text-bright, #e2e8f0)",
        }}
      >
        Public Dashboard
      </h2>
      <p
        style={{
          margin: "0 0 1.5rem",
          fontSize: "0.8rem",
          color: "var(--text-muted, #94a3b8)",
        }}
      >
        Anonymised overview of maritime anomaly detection activity. Data refreshes every 5 minutes.
      </p>

      {/* Stat cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1.5rem",
        }}
      >
        {statCards.map((s) => (
          <Card key={s.label}>
            <div
              style={{
                fontSize: "0.75rem",
                color: "var(--text-muted, #94a3b8)",
                marginBottom: "0.25rem",
              }}
            >
              {s.label}
            </div>
            <div style={{ fontSize: "1.75rem", fontWeight: 700, color: s.color }}>
              {s.value}
            </div>
          </Card>
        ))}
      </div>

      {/* Trend chart */}
      {trendData.length > 0 && (
        <Card style={{ marginBottom: "1rem" }}>
          <h3
            style={{
              margin: "0 0 0.75rem",
              fontSize: "0.8rem",
              color: "var(--text-muted, #94a3b8)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Alert Trend
          </h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trendData}>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "var(--text-muted, #94a3b8)" }}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted, #94a3b8)" }}
                allowDecimals={false}
              />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="count"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Detections by type */}
      <Card style={{ marginBottom: "1rem" }}>
        <h3
          style={{
            margin: "0 0 0.75rem",
            fontSize: "0.8rem",
            color: "var(--text-muted, #94a3b8)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Detections by Type
        </h3>
        {typeData.some((d) => d.count > 0) ? (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={typeData}>
              <XAxis
                dataKey="name"
                tick={{ fontSize: 11, fill: "var(--text-muted, #94a3b8)" }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted, #94a3b8)" }}
                allowDecimals={false}
              />
              <Tooltip />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {typeData.map((entry, idx) => (
                  <Cell key={idx} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p style={{ color: "var(--text-muted, #94a3b8)", fontSize: "0.85rem" }}>
            No detections recorded yet.
          </p>
        )}
      </Card>

      {/* Recent alerts table */}
      <Card>
        <h3
          style={{
            margin: "0 0 0.75rem",
            fontSize: "0.8rem",
            color: "var(--text-muted, #94a3b8)",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Recent Alerts (anonymised)
        </h3>
        {recent_alerts.length > 0 ? (
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.85rem",
            }}
          >
            <thead>
              <tr
                style={{
                  borderBottom: "1px solid var(--border, #334155)",
                  color: "var(--text-muted, #94a3b8)",
                  textAlign: "left",
                }}
              >
                <th style={{ padding: "0.4rem 0.5rem", fontWeight: 600 }}>MMSI (last 4)</th>
                <th style={{ padding: "0.4rem 0.5rem", fontWeight: 600 }}>Flag</th>
                <th style={{ padding: "0.4rem 0.5rem", fontWeight: 600 }}>Tier</th>
                <th style={{ padding: "0.4rem 0.5rem", fontWeight: 600 }}>Date</th>
              </tr>
            </thead>
            <tbody>
              {recent_alerts.map((alert: RecentAlert, idx: number) => (
                <tr
                  key={idx}
                  style={{ borderBottom: "1px solid var(--border, #334155)" }}
                >
                  <td style={{ padding: "0.4rem 0.5rem" }}>...{alert.mmsi_suffix}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }}>{alert.flag}</td>
                  <td style={{ padding: "0.4rem 0.5rem" }}>
                    <span
                      style={{
                        display: "inline-block",
                        padding: "0.1rem 0.5rem",
                        borderRadius: "9999px",
                        fontSize: "0.75rem",
                        fontWeight: 600,
                        background: TIER_COLORS[alert.tier] || "#6b7280",
                        color: "white",
                      }}
                    >
                      {alert.tier}
                    </span>
                  </td>
                  <td
                    style={{
                      padding: "0.4rem 0.5rem",
                      color: "var(--text-muted, #94a3b8)",
                    }}
                  >
                    {alert.created_at ? new Date(alert.created_at).toLocaleDateString() : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: "var(--text-muted, #94a3b8)", fontSize: "0.85rem" }}>
            No recent alerts.
          </p>
        )}
      </Card>
    </div>
  );
}
