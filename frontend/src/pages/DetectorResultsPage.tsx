import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useVesselDetail } from "../hooks/useVessels";
import { useVesselDetectors } from "../hooks/useDetectors";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { EmptyState } from "../components/ui/EmptyState";
import { ScoreBadge } from "../components/ui/ScoreBadge";

const sectionHead: React.CSSProperties = {
  margin: "0 0 12px",
  fontSize: 14,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: 1,
  cursor: "pointer",
  userSelect: "none",
};

const cellStyle: React.CSSProperties = { padding: "0.5rem 0.75rem", fontSize: "0.8125rem" };
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: "var(--text-muted)",
  textAlign: "left" as const,
  borderBottom: "1px solid var(--border)",
};

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return "-";
  return ts.slice(0, 16).replace("T", " ");
}

function CollapsibleSection({
  title,
  count,
  defaultOpen,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? true);
  return (
    <Card style={{ marginBottom: 16 }}>
      <h3 style={sectionHead} onClick={() => setOpen(!open)}>
        <span style={{ fontSize: 11, marginRight: 6 }}>{open ? "\u25BC" : "\u25B6"}</span>
        {title}
        <span
          style={{
            marginLeft: 8,
            padding: "2px 8px",
            borderRadius: "var(--radius)",
            fontSize: 11,
            fontWeight: 700,
            background: count > 0 ? "var(--warning)" : "var(--bg-base)",
            color: count > 0 ? "white" : "var(--text-dim)",
          }}
        >
          {count}
        </span>
      </h3>
      {open && children}
    </Card>
  );
}

export function DetectorResultsPage() {
  const { id } = useParams<{ id: string }>();
  const { data: vessel, isLoading: vesselLoading } = useVesselDetail(id);
  const { gaps, flagHistory, portCalls } = useVesselDetectors(id);

  if (vesselLoading) return <Spinner text="Loading vessel..." />;
  if (!vessel) {
    return (
      <p style={{ color: "var(--score-critical)" }}>
        Vessel not found. <Link to="/vessels">Back to search</Link>
      </p>
    );
  }

  const spoofingList = vessel.spoofing_anomalies_30d ?? [];
  const loiteringList = vessel.loitering_events_30d ?? [];
  const stsList = vessel.sts_events_60d ?? [];
  const gapItems = gaps.data?.items ?? [];
  const flagHistoryItems = flagHistory.data ?? [];
  const portCallItems = portCalls.data?.items ?? [];

  return (
    <div style={{ maxWidth: 1100 }}>
      <Link to={`/vessels/${id}`} style={{ fontSize: 13 }}>
        &larr; Back to vessel
      </Link>

      <h2 style={{ margin: "12px 0 4px", fontSize: 18 }}>
        Detector Results: {vessel.name ?? "Unknown"}
      </h2>
      <p style={{ color: "var(--text-dim)", margin: "0 0 20px", fontSize: 13 }}>
        MMSI {vessel.mmsi ?? "?"} &middot; IMO {vessel.imo ?? "?"} &middot; {vessel.flag ?? "??"}
      </p>

      {/* Spoofing Anomalies */}
      <CollapsibleSection title="Spoofing Anomalies" count={spoofingList.length}>
        {spoofingList.length === 0 ? (
          <EmptyState title="No spoofing anomalies detected" />
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>ID</th>
                <th style={headStyle}>Type</th>
                <th style={headStyle}>Time</th>
                <th style={headStyle}>Score</th>
              </tr>
            </thead>
            <tbody>
              {spoofingList.map((s, i) => (
                <tr key={s.anomaly_id ?? i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={cellStyle}>#{s.anomaly_id}</td>
                  <td style={cellStyle}>
                    <span
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--radius)",
                        fontSize: 11,
                        background: "var(--bg-base)",
                        border: "1px solid var(--border)",
                        color: "var(--warning)",
                      }}
                    >
                      {s.anomaly_type.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td style={cellStyle}>{formatTimestamp(s.start_time_utc)}</td>
                  <td style={cellStyle}>
                    <ScoreBadge score={s.risk_score_component} size="sm" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>

      {/* Gap Events */}
      <CollapsibleSection title="AIS Gap Events" count={gapItems.length}>
        {gaps.isLoading && <Spinner text="Loading gaps..." />}
        {!gaps.isLoading && gapItems.length === 0 && <EmptyState title="No gap events" />}
        {gapItems.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>ID</th>
                <th style={headStyle}>Score</th>
                <th style={headStyle}>Gap Start</th>
                <th style={headStyle}>Duration</th>
                <th style={headStyle}>Status</th>
              </tr>
            </thead>
            <tbody>
              {gapItems.map((a) => (
                <tr key={a.gap_event_id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={cellStyle}>
                    <Link to={`/alerts/${a.gap_event_id}`}>#{a.gap_event_id}</Link>
                  </td>
                  <td style={cellStyle}>
                    <ScoreBadge score={a.risk_score} size="sm" />
                  </td>
                  <td style={cellStyle}>{formatTimestamp(a.gap_start_utc)}</td>
                  <td style={cellStyle}>
                    {a.duration_minutes != null ? `${(a.duration_minutes / 60).toFixed(1)}h` : "-"}
                  </td>
                  <td style={cellStyle}>{a.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>

      {/* STS Events */}
      <CollapsibleSection title="Ship-to-Ship Events" count={stsList.length}>
        {stsList.length === 0 ? (
          <EmptyState title="No STS events detected" />
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>ID</th>
                <th style={headStyle}>Partner</th>
                <th style={headStyle}>Time</th>
                <th style={headStyle}>Type</th>
              </tr>
            </thead>
            <tbody>
              {stsList.map((s, i) => {
                const partnerId = s.vessel_1_id === Number(id) ? s.vessel_2_id : s.vessel_1_id;
                return (
                  <tr key={s.sts_id ?? i} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={cellStyle}>#{s.sts_id}</td>
                    <td style={cellStyle}>
                      <Link to={`/vessels/${partnerId}`}>Vessel #{partnerId}</Link>
                    </td>
                    <td style={cellStyle}>{formatTimestamp(s.start_time_utc)}</td>
                    <td style={cellStyle}>{s.detection_type.replace(/_/g, " ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CollapsibleSection>

      {/* Loitering Events */}
      <CollapsibleSection title="Loitering Events" count={loiteringList.length}>
        {loiteringList.length === 0 ? (
          <EmptyState title="No loitering events detected" />
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>ID</th>
                <th style={headStyle}>Start Time</th>
                <th style={headStyle}>Duration</th>
                <th style={headStyle}>Corridor</th>
              </tr>
            </thead>
            <tbody>
              {loiteringList.map((l, i) => (
                <tr key={l.loiter_id ?? i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={cellStyle}>#{l.loiter_id}</td>
                  <td style={cellStyle}>{formatTimestamp(l.start_time_utc)}</td>
                  <td style={cellStyle}>
                    {l.duration_hours != null ? `${l.duration_hours.toFixed(1)}h` : "-"}
                  </td>
                  <td style={cellStyle}>
                    {l.corridor_id != null ? (
                      <Link to={`/corridors/${l.corridor_id}`}>Corridor #{l.corridor_id}</Link>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>

      {/* Flag / Identity History */}
      <CollapsibleSection
        title="Flag & Identity History"
        count={flagHistoryItems.length}
        defaultOpen={false}
      >
        {flagHistory.isLoading && <Spinner text="Loading history..." />}
        {!flagHistory.isLoading && flagHistoryItems.length === 0 && (
          <EmptyState title="No identity changes recorded" />
        )}
        {flagHistoryItems.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>Time</th>
                <th style={headStyle}>Field</th>
                <th style={headStyle}>Old Value</th>
                <th style={headStyle}>New Value</th>
              </tr>
            </thead>
            <tbody>
              {flagHistoryItems.map((h, i) => (
                <tr
                  key={h.vessel_history_id ?? i}
                  style={{ borderBottom: "1px solid var(--border)" }}
                >
                  <td style={cellStyle}>{formatTimestamp(h.observed_at)}</td>
                  <td style={cellStyle}>{h.field_changed}</td>
                  <td
                    style={{
                      ...cellStyle,
                      color: "var(--score-medium)",
                      textDecoration: "line-through",
                    }}
                  >
                    {h.old_value || "(none)"}
                  </td>
                  <td style={{ ...cellStyle, color: "var(--accent)", fontWeight: 600 }}>
                    {h.new_value || "(none)"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>

      {/* Port Calls */}
      <CollapsibleSection title="Port Calls" count={portCallItems.length} defaultOpen={false}>
        {portCalls.isLoading && <Spinner text="Loading port calls..." />}
        {!portCalls.isLoading && portCallItems.length === 0 && (
          <EmptyState
            title="No port calls available"
            description="Port call data may not be available for this vessel."
          />
        )}
        {portCallItems.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg-base)" }}>
                <th style={headStyle}>Port</th>
                <th style={headStyle}>Arrival</th>
                <th style={headStyle}>Departure</th>
                <th style={headStyle}>Source</th>
              </tr>
            </thead>
            <tbody>
              {portCallItems.map((p, i) => (
                <tr key={p.port_call_id ?? i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={cellStyle}>{p.port_name ?? "-"}</td>
                  <td style={cellStyle}>{formatTimestamp(p.arrival_utc)}</td>
                  <td style={cellStyle}>{formatTimestamp(p.departure_utc)}</td>
                  <td style={cellStyle}>{p.source ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>
    </div>
  );
}
