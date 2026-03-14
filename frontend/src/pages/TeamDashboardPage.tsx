import { useState, useEffect } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { apiFetch } from "../lib/api";

interface WorkloadEntry {
  analyst_id: number;
  analyst_name: string;
  open_alerts: number;
  assigned_alerts: number;
  utilization: number;
  is_online: boolean;
  specializations: string[];
}

interface FeedEntry {
  event_type: string;
  analyst_name: string;
  description: string;
  timestamp: string | null;
  related_id: number | null;
}

interface QueueEntry {
  alert_id: number;
  risk_score: number;
  vessel_name: string | null;
  corridor_name: string | null;
  suggested_analyst_id: number | null;
  suggested_analyst_name: string | null;
}

export function TeamDashboardPage() {
  const [workload, setWorkload] = useState<WorkloadEntry[]>([]);
  const [feed, setFeed] = useState<FeedEntry[]>([]);
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<WorkloadEntry[]>("/analysts/workload/detailed")
      .then(setWorkload)
      .catch(() => setError("Failed to load workload data"));
    apiFetch<FeedEntry[]>("/analysts/activity-feed")
      .then(setFeed)
      .catch(() => {});
    apiFetch<QueueEntry[]>("/analysts/queue")
      .then(setQueue)
      .catch(() => {});
  }, []);

  if (error) {
    return (
      <div style={{ padding: "1rem" }}>
        <h1>Team Dashboard</h1>
        <p style={{ color: "#ef4444" }}>{error}</p>
      </div>
    );
  }

  return (
    <div style={{ padding: "1rem" }}>
      <h1>Team Dashboard</h1>

      {/* Capacity section */}
      <section>
        <h2>Analyst Capacity</h2>
        {workload.length > 0 ? (
          <ResponsiveContainer
            width="100%"
            height={Math.max(200, workload.length * 40)}
          >
            <BarChart data={workload} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" domain={[0, 1]} />
              <YAxis type="category" dataKey="analyst_name" width={120} />
              <Tooltip />
              <Bar dataKey="utilization" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p>No analyst data available.</p>
        )}

        {/* Online status indicators */}
        <div
          style={{
            display: "flex",
            gap: "1rem",
            flexWrap: "wrap",
            marginTop: "0.5rem",
          }}
        >
          {workload.map((a) => (
            <span
              key={a.analyst_id}
              data-testid="online-indicator"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.25rem",
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  backgroundColor: a.is_online ? "#22c55e" : "#9ca3af",
                  display: "inline-block",
                }}
              />
              {a.analyst_name}
            </span>
          ))}
        </div>
      </section>

      {/* Queue section */}
      <section style={{ marginTop: "2rem" }}>
        <h2>Assignment Queue</h2>
        {queue.length > 0 ? (
          <table
            data-testid="queue-table"
            style={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "0.5rem" }}>Alert</th>
                <th style={{ textAlign: "left", padding: "0.5rem" }}>Score</th>
                <th style={{ textAlign: "left", padding: "0.5rem" }}>
                  Suggested
                </th>
              </tr>
            </thead>
            <tbody>
              {queue.map((q) => (
                <tr key={q.alert_id}>
                  <td style={{ padding: "0.5rem" }}>#{q.alert_id}</td>
                  <td style={{ padding: "0.5rem" }}>{q.risk_score}</td>
                  <td style={{ padding: "0.5rem" }}>
                    {q.suggested_analyst_name || "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No unassigned high-risk alerts.</p>
        )}
      </section>

      {/* Activity feed */}
      <section style={{ marginTop: "2rem" }}>
        <h2>Recent Activity</h2>
        {feed.length > 0 ? (
          <ul data-testid="activity-feed">
            {feed.map((f, i) => (
              <li key={i}>
                {f.description}
                {f.timestamp
                  ? ` \u2014 ${new Date(f.timestamp).toLocaleString()}`
                  : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p>No recent activity.</p>
        )}
      </section>
    </div>
  );
}

export default TeamDashboardPage;
