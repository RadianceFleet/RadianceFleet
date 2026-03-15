import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { ScoreBadge } from "./ui/ScoreBadge";
import { StatusBadge } from "./ui/StatusBadge";
import { Spinner } from "./ui/Spinner";
import { useAuth } from "../hooks/useAuth";
import { tableStyle, theadRow, tbodyRow, tdStyle, btnStyle } from "../styles/tables";

const thStyle: React.CSSProperties = {
  padding: "8px 12px",
  textAlign: "left",
  fontWeight: 600,
  fontSize: "0.75rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "var(--text-muted)",
};

interface AlertGroupMember {
  gap_event_id: number;
  vessel_id: number;
  gap_start_utc: string;
  gap_end_utc: string;
  duration_minutes: number;
  risk_score: number;
  status: string;
}

interface AlertGroupData {
  group_id: number;
  vessel_id: number;
  corridor_id: number | null;
  group_key: string;
  primary_alert_id: number | null;
  alert_count: number;
  first_seen_utc: string;
  last_seen_utc: string;
  max_risk_score: number;
  status: string;
  created_at: string;
  members: AlertGroupMember[];
}

export function AlertGroupDetail({ groupId }: { groupId: number }) {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(true);

  const { data, isLoading, error } = useQuery({
    queryKey: ["alert-group", groupId],
    queryFn: () => apiFetch<AlertGroupData>(`/alert-groups/${groupId}`),
  });

  const dismissMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/alert-groups/${groupId}/dismiss`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alert-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["alert-groups"] });
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const verdictMutation = useMutation({
    mutationFn: (verdict: string) =>
      apiFetch(`/alert-groups/${groupId}/verdict`, {
        method: "POST",
        body: JSON.stringify({ verdict }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alert-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["alert-groups"] });
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  if (isLoading) return <Spinner text="Loading group..." />;
  if (error || !data)
    return <p style={{ color: "var(--score-critical)" }}>Error loading alert group.</p>;

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "1rem",
        marginBottom: "1rem",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "0.75rem",
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: "1rem" }}>
            Group #{data.group_id}
            <StatusBadge status={data.status} />
          </h3>
          <p style={{ margin: "0.25rem 0 0", fontSize: "0.8125rem", color: "var(--text-muted)" }}>
            {data.alert_count} alert{data.alert_count !== 1 ? "s" : ""} |{" "}
            Max score: <ScoreBadge score={data.max_risk_score} /> |{" "}
            {data.first_seen_utc.slice(0, 10)} to {data.last_seen_utc.slice(0, 10)}
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            style={btnStyle}
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? "Collapse" : "Expand"}
          </button>
          {isAuthenticated && data.status === "active" && (
            <>
              <button
                style={{ ...btnStyle, background: "var(--score-low)" }}
                onClick={() => dismissMutation.mutate()}
                disabled={dismissMutation.isPending}
              >
                Dismiss Group
              </button>
              <button
                style={{ ...btnStyle, background: "var(--score-high)" }}
                onClick={() => verdictMutation.mutate("true_positive")}
                disabled={verdictMutation.isPending}
              >
                True Positive
              </button>
              <button
                style={{ ...btnStyle, background: "var(--score-medium)" }}
                onClick={() => verdictMutation.mutate("false_positive")}
                disabled={verdictMutation.isPending}
              >
                False Positive
              </button>
            </>
          )}
        </div>
      </div>

      {expanded && (
        <table style={{ ...tableStyle, fontSize: "0.8125rem" }}>
          <thead>
            <tr style={theadRow}>
              <th style={thStyle}>Alert ID</th>
              <th style={thStyle}>Score</th>
              <th style={thStyle}>Gap Start</th>
              <th style={thStyle}>Duration</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Primary</th>
            </tr>
          </thead>
          <tbody>
            {data.members.map((m) => (
              <tr key={m.gap_event_id} style={tbodyRow}>
                <td style={tdStyle}>
                  <Link to={`/alerts/${m.gap_event_id}`}>#{m.gap_event_id}</Link>
                </td>
                <td style={tdStyle}>
                  <ScoreBadge score={m.risk_score} />
                </td>
                <td style={tdStyle}>
                  {typeof m.gap_start_utc === "string"
                    ? m.gap_start_utc.slice(0, 16).replace("T", " ")
                    : ""}
                </td>
                <td style={tdStyle}>{(m.duration_minutes / 60).toFixed(1)}h</td>
                <td style={tdStyle}>
                  <StatusBadge status={m.status} />
                </td>
                <td style={tdStyle}>
                  {m.gap_event_id === data.primary_alert_id ? "Primary" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
