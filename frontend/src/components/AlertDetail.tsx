import { useParams, Link, useNavigate } from "react-router-dom";
import {
  useAlert,
  useUpdateAlertStatus,
  useUpdateAlertNotes,
  useSubmitAlertVerdict,
} from "../hooks/useAlerts";
import { AlertMap } from "./AlertMap";
import { ScoreBreakdown } from "./ScoreBreakdown";
import { Spinner } from "./ui/Spinner";
import { ScoreBadge } from "./ui/ScoreBadge";
import { AlertStatusPanel } from "./AlertStatusPanel";
import { AlertExportPanel } from "./AlertExportPanel";
import { NarrativePanel } from "./NarrativePanel";
import { useAuth } from "../hooks/useAuth";
import {
  sectionHead,
  labelCell as _labelCell,
  valueCell,
  card,
  btnStyle,
  tableStyle,
  theadRow,
  tbodyRow,
} from "../styles/tables";

const labelCell: React.CSSProperties = { ..._labelCell, width: 180 };

export function AlertDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { isAuthenticated } = useAuth();
  const { data: alert, isLoading, error } = useAlert(id);
  const statusMutation = useUpdateAlertStatus(id ?? "");
  const notesMutation = useUpdateAlertNotes(id ?? "");
  const verdictMutation = useSubmitAlertVerdict(id ?? "");

  if (isLoading) return <Spinner text="Loading alert…" />;
  if (error || !alert) {
    return (
      <p style={{ color: "var(--score-critical)" }}>
        Alert not found. <Link to="/alerts">← Back</Link>
      </p>
    );
  }

  return (
    <div style={{ maxWidth: 860 }}>
      <Link to="/alerts" style={{ fontSize: 13 }}>
        ← All alerts
      </Link>
      <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "12px 0 4px" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Alert #{alert.gap_event_id}</h2>
        <button
          onClick={() => navigator.clipboard.writeText(window.location.href)}
          style={{
            fontSize: 12,
            padding: "4px 10px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            cursor: "pointer",
            background: "transparent",
            color: "var(--text-muted)",
          }}
          title="Copy link to clipboard"
        >
          Share
        </button>
      </div>
      <p style={{ color: "var(--text-dim)", margin: "0 0 20px", fontSize: 13 }}>
        {alert.vessel_id ? (
          <Link to={`/vessels/${alert.vessel_id}`}>{alert.vessel_name ?? "Unknown vessel"}</Link>
        ) : (
          (alert.vessel_name ?? "Unknown vessel")
        )}
        {" · MMSI "}
        {alert.vessel_mmsi ?? "?"}
        {" · "}
        {alert.vessel_flag ?? "?"}
        {alert.vessel_deadweight != null && ` · ${alert.vessel_deadweight.toLocaleString()} DWT`}
        {alert.corridor_name && ` · ${alert.corridor_name}`}
      </p>

      <AlertMap
        lastPoint={alert.last_point}
        firstPointAfter={alert.first_point_after}
        envelope={alert.movement_envelope}
        corridorId={alert.corridor_id ?? undefined}
      />

      <section style={card}>
        <h3 style={sectionHead}>Gap Details</h3>
        <table>
          <tbody>
            <tr>
              <td style={labelCell}>Start</td>
              <td style={valueCell}>{alert.gap_start_utc.slice(0, 19).replace("T", " ")} UTC</td>
            </tr>
            <tr>
              <td style={labelCell}>End</td>
              <td style={valueCell}>{alert.gap_end_utc.slice(0, 19).replace("T", " ")} UTC</td>
            </tr>
            <tr>
              <td style={labelCell}>Duration</td>
              <td style={valueCell}>{(alert.duration_minutes / 60).toFixed(1)} h</td>
            </tr>
            <tr>
              <td style={labelCell}>In dark zone</td>
              <td style={valueCell}>{alert.in_dark_zone ? "🌑 Yes (GPS jamming zone)" : "No"}</td>
            </tr>
            {alert.velocity_plausibility_ratio != null && (
              <tr>
                <td style={labelCell}>Velocity ratio</td>
                <td style={valueCell}>
                  {alert.velocity_plausibility_ratio.toFixed(3)}
                  {alert.impossible_speed_flag && " ⚠️ Physically impossible"}
                </td>
              </tr>
            )}
            {alert.max_plausible_distance_nm != null && (
              <tr>
                <td style={labelCell}>Max plausible dist.</td>
                <td style={valueCell}>{alert.max_plausible_distance_nm.toFixed(0)} nm</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section style={card}>
        <h3 style={sectionHead}>
          Risk Score: <ScoreBadge score={alert.risk_score} size="md" />
        </h3>
        {alert.risk_breakdown_json && Object.keys(alert.risk_breakdown_json).length > 0 && (
          <ScoreBreakdown breakdown={alert.risk_breakdown_json as Record<string, unknown>} />
        )}
      </section>

      {alert.satellite_check && (
        <section style={card}>
          <h3 style={sectionHead}>Satellite Check</h3>
          <p style={{ fontSize: 13, margin: "0 0 8px" }}>
            Status: <b>{alert.satellite_check.review_status.replace(/_/g, " ")}</b>
          </p>
          {alert.satellite_check.copernicus_url && (
            <a
              href={alert.satellite_check.copernicus_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontSize: 13 }}
            >
              Open Copernicus Browser ↗
            </a>
          )}
        </section>
      )}

      {/* Linked Spoofing Anomalies */}
      {alert.spoofing_anomalies && alert.spoofing_anomalies.length > 0 && (
        <section style={card}>
          <h3 style={sectionHead}>Linked Spoofing Anomalies</h3>
          <table style={tableStyle}>
            <thead>
              <tr style={theadRow}>
                <th style={{ ...labelCell, width: "auto" }}>ID</th>
                <th style={{ ...labelCell, width: "auto" }}>Type</th>
                <th style={{ ...labelCell, width: "auto" }}>Time</th>
                <th style={{ ...labelCell, width: "auto" }}>Risk Score</th>
              </tr>
            </thead>
            <tbody>
              {alert.spoofing_anomalies.map((s) => (
                <tr key={s.anomaly_id} style={tbodyRow}>
                  <td style={valueCell}>#{s.anomaly_id}</td>
                  <td style={valueCell}>
                    <span style={{ color: "#9b59b6" }}>{s.anomaly_type.replace(/_/g, " ")}</span>
                  </td>
                  <td style={valueCell}>
                    {s.start_time_utc?.slice(0, 19).replace("T", " ") ?? "--"} UTC
                  </td>
                  <td style={valueCell}>{s.risk_score_component ?? "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Linked Loitering Events */}
      {alert.loitering_events && alert.loitering_events.length > 0 && (
        <section style={card}>
          <h3 style={sectionHead}>Linked Loitering Events</h3>
          <table style={tableStyle}>
            <thead>
              <tr style={theadRow}>
                <th style={{ ...labelCell, width: "auto" }}>ID</th>
                <th style={{ ...labelCell, width: "auto" }}>Start Time</th>
                <th style={{ ...labelCell, width: "auto" }}>Duration</th>
                <th style={{ ...labelCell, width: "auto" }}>Position</th>
                <th style={{ ...labelCell, width: "auto" }}>Median SOG</th>
              </tr>
            </thead>
            <tbody>
              {alert.loitering_events.map((l) => (
                <tr key={l.loiter_id} style={tbodyRow}>
                  <td style={valueCell}>#{l.loiter_id}</td>
                  <td style={valueCell}>
                    {l.start_time_utc?.slice(0, 19).replace("T", " ") ?? "--"} UTC
                  </td>
                  <td style={valueCell}>
                    {l.duration_hours != null ? `${l.duration_hours.toFixed(1)}h` : "--"}
                  </td>
                  <td style={valueCell}>
                    {l.mean_lat != null && l.mean_lon != null
                      ? `${l.mean_lat.toFixed(4)}, ${l.mean_lon.toFixed(4)}`
                      : "--"}
                  </td>
                  <td style={valueCell}>
                    {l.median_sog_kn != null ? `${l.median_sog_kn.toFixed(1)} kn` : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Linked STS Transfers */}
      {alert.sts_events && alert.sts_events.length > 0 && (
        <section style={card}>
          <h3 style={sectionHead}>Linked STS Transfers</h3>
          <table style={tableStyle}>
            <thead>
              <tr style={theadRow}>
                <th style={{ ...labelCell, width: "auto" }}>ID</th>
                <th style={{ ...labelCell, width: "auto" }}>Partner</th>
                <th style={{ ...labelCell, width: "auto" }}>Detection Type</th>
                <th style={{ ...labelCell, width: "auto" }}>Start Time</th>
              </tr>
            </thead>
            <tbody>
              {alert.sts_events.map((s) => (
                <tr key={s.sts_id} style={tbodyRow}>
                  <td style={valueCell}>#{s.sts_id}</td>
                  <td style={valueCell}>
                    {s.partner_name ?? s.partner_mmsi ?? "--"}
                    {s.partner_name && s.partner_mmsi && (
                      <span style={{ color: "var(--text-dim)", marginLeft: 4, fontSize: 11 }}>
                        ({s.partner_mmsi})
                      </span>
                    )}
                  </td>
                  <td style={valueCell}>{s.detection_type?.replace(/_/g, " ") ?? "--"}</td>
                  <td style={valueCell}>
                    {s.start_time_utc?.slice(0, 19).replace("T", " ") ?? "--"} UTC
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Extracted: analyst workflow (status + notes) */}
      <AlertStatusPanel
        currentStatus={alert.status}
        analystNotes={alert.analyst_notes}
        statusMutation={statusMutation}
        notesMutation={notesMutation}
        is_false_positive={alert.is_false_positive}
        reviewed_by={alert.reviewed_by}
        review_date={alert.review_date}
        verdictMutation={verdictMutation}
        readOnly={!isAuthenticated}
      />

      {/* Investigation Narrative */}
      <NarrativePanel alertId={id!} />

      {/* Extracted: export + satellite check actions */}
      <section style={card}>
        <AlertExportPanel alertId={id!} />
      </section>

      <button
        onClick={() => navigate("/alerts")}
        style={{
          ...btnStyle,
          background: "var(--bg-base)",
          color: "var(--text-dim)",
          marginTop: 8,
        }}
      >
        ← Back to alerts
      </button>
    </div>
  );
}
