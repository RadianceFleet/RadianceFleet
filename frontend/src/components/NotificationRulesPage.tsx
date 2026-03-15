import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { card, sectionHead, btnStyle } from "../styles/tables";

interface NotificationRule {
  rule_id: number;
  name: string;
  is_active: boolean;
  created_by: number | null;
  created_at: string | null;
  updated_at: string | null;
  min_score: number | null;
  max_score: number | null;
  corridor_ids_json: number[] | null;
  vessel_flags_json: string[] | null;
  alert_statuses_json: string[] | null;
  vessel_types_json: string[] | null;
  scoring_signals_json: string[] | null;
  time_window_start: string | null;
  time_window_end: string | null;
  channel: string;
  destination: string;
  message_template: string | null;
  throttle_minutes: number;
}

interface NotificationLog {
  log_id: number;
  rule_id: number;
  alert_id: number;
  channel: string;
  destination: string;
  status: string;
  error_message: string | null;
  sent_at: string | null;
}

interface RulesResponse {
  rules: NotificationRule[];
  total: number;
}

interface LogsResponse {
  logs: NotificationLog[];
  total: number;
}

const CHANNELS = ["slack", "email", "webhook"] as const;

const emptyForm = {
  name: "",
  is_active: true,
  min_score: "",
  max_score: "",
  corridor_ids_json: "",
  vessel_flags_json: "",
  alert_statuses_json: "",
  vessel_types_json: "",
  scoring_signals_json: "",
  time_window_start: "",
  time_window_end: "",
  channel: "slack" as string,
  destination: "",
  message_template: "",
  throttle_minutes: "30",
};

function parseCSV(val: string): string[] | null {
  const trimmed = val.trim();
  if (!trimmed) return null;
  return trimmed.split(",").map((s) => s.trim()).filter(Boolean);
}

function parseIntCSV(val: string): number[] | null {
  const parts = parseCSV(val);
  if (!parts) return null;
  return parts.map(Number).filter((n) => !isNaN(n));
}

export function NotificationRulesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<NotificationRule | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [viewLogs, setViewLogs] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const { data, isLoading } = useQuery<RulesResponse>({
    queryKey: ["notification-rules"],
    queryFn: () => apiFetch("/api/v1/admin/notification-rules"),
  });

  const { data: logsData } = useQuery<LogsResponse>({
    queryKey: ["notification-rule-logs", viewLogs],
    queryFn: () => apiFetch(`/api/v1/admin/notification-rules/${viewLogs}/logs`),
    enabled: viewLogs !== null,
  });

  const createMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch("/api/v1/admin/notification-rules", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notification-rules"] });
      setForm(emptyForm);
    },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      apiFetch(`/api/v1/admin/notification-rules/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notification-rules"] });
      setEditing(null);
      setForm(emptyForm);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/api/v1/admin/notification-rules/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-rules"] }),
  });

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const body: Record<string, unknown> = {
        name: form.name,
        is_active: form.is_active,
        channel: form.channel,
        destination: form.destination,
        message_template: form.message_template || null,
        throttle_minutes: parseInt(form.throttle_minutes, 10) || 30,
        min_score: form.min_score ? parseInt(form.min_score, 10) : null,
        max_score: form.max_score ? parseInt(form.max_score, 10) : null,
        corridor_ids_json: parseIntCSV(form.corridor_ids_json),
        vessel_flags_json: parseCSV(form.vessel_flags_json),
        alert_statuses_json: parseCSV(form.alert_statuses_json),
        vessel_types_json: parseCSV(form.vessel_types_json),
        scoring_signals_json: parseCSV(form.scoring_signals_json),
        time_window_start: form.time_window_start || null,
        time_window_end: form.time_window_end || null,
      };
      if (editing) {
        updateMut.mutate({ id: editing.rule_id, body });
      } else {
        createMut.mutate(body);
      }
    },
    [form, editing, createMut, updateMut],
  );

  const startEdit = (rule: NotificationRule) => {
    setEditing(rule);
    setForm({
      name: rule.name,
      is_active: rule.is_active,
      min_score: rule.min_score?.toString() ?? "",
      max_score: rule.max_score?.toString() ?? "",
      corridor_ids_json: rule.corridor_ids_json?.join(", ") ?? "",
      vessel_flags_json: rule.vessel_flags_json?.join(", ") ?? "",
      alert_statuses_json: rule.alert_statuses_json?.join(", ") ?? "",
      vessel_types_json: rule.vessel_types_json?.join(", ") ?? "",
      scoring_signals_json: rule.scoring_signals_json?.join(", ") ?? "",
      time_window_start: rule.time_window_start ?? "",
      time_window_end: rule.time_window_end ?? "",
      channel: rule.channel,
      destination: rule.destination,
      message_template: rule.message_template ?? "",
      throttle_minutes: rule.throttle_minutes.toString(),
    });
  };

  const testRule = async (id: number) => {
    try {
      const res = await apiFetch<{ test_result: { success: boolean; error?: string } }>(
        `/api/v1/admin/notification-rules/${id}/test`,
        { method: "POST" },
      );
      setTestResult(
        res.test_result.success
          ? `Test sent successfully (rule ${id})`
          : `Test failed: ${res.test_result.error || "unknown error"}`,
      );
    } catch {
      setTestResult("Test request failed");
    }
  };

  const set = (key: string, val: string | boolean) =>
    setForm((prev) => ({ ...prev, [key]: val }));

  if (isLoading) return <p>Loading rules...</p>;

  return (
    <div>
      <h2 style={sectionHead}>Notification Rules</h2>

      {testResult && (
        <div
          style={{ padding: 8, marginBottom: 12, background: "#2a2a3a", borderRadius: 4 }}
        >
          {testResult}
          <button style={{ ...btnStyle, marginLeft: 8 }} onClick={() => setTestResult(null)}>
            Dismiss
          </button>
        </div>
      )}

      {/* Form */}
      <section style={card}>
        <h3>{editing ? "Edit Rule" : "Create Rule"}</h3>
        <form onSubmit={handleSubmit}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <label>
              Name
              <input value={form.name} onChange={(e) => set("name", e.target.value)} required />
            </label>
            <label>
              Channel
              <select value={form.channel} onChange={(e) => set("channel", e.target.value)}>
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
            <label>
              Destination
              <input
                value={form.destination}
                onChange={(e) => set("destination", e.target.value)}
                placeholder="Slack channel, email, or URL"
                required
              />
            </label>
            <label>
              Throttle (min)
              <input
                type="number"
                value={form.throttle_minutes}
                onChange={(e) => set("throttle_minutes", e.target.value)}
              />
            </label>
            <label>
              Min Score
              <input
                type="number"
                value={form.min_score}
                onChange={(e) => set("min_score", e.target.value)}
              />
            </label>
            <label>
              Max Score
              <input
                type="number"
                value={form.max_score}
                onChange={(e) => set("max_score", e.target.value)}
              />
            </label>
            <label>
              Corridor IDs (comma-sep)
              <input
                value={form.corridor_ids_json}
                onChange={(e) => set("corridor_ids_json", e.target.value)}
              />
            </label>
            <label>
              Vessel Flags (comma-sep)
              <input
                value={form.vessel_flags_json}
                onChange={(e) => set("vessel_flags_json", e.target.value)}
              />
            </label>
            <label>
              Alert Statuses (comma-sep)
              <input
                value={form.alert_statuses_json}
                onChange={(e) => set("alert_statuses_json", e.target.value)}
              />
            </label>
            <label>
              Vessel Types (comma-sep)
              <input
                value={form.vessel_types_json}
                onChange={(e) => set("vessel_types_json", e.target.value)}
              />
            </label>
            <label>
              Scoring Signals (comma-sep)
              <input
                value={form.scoring_signals_json}
                onChange={(e) => set("scoring_signals_json", e.target.value)}
              />
            </label>
            <label>
              Time Window Start (HH:MM)
              <input
                value={form.time_window_start}
                onChange={(e) => set("time_window_start", e.target.value)}
                placeholder="08:00"
              />
            </label>
            <label>
              Time Window End (HH:MM)
              <input
                value={form.time_window_end}
                onChange={(e) => set("time_window_end", e.target.value)}
                placeholder="18:00"
              />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => set("is_active", e.target.checked)}
              />
              Active
            </label>
          </div>
          <label style={{ display: "block", marginTop: 8 }}>
            Message Template
            <textarea
              value={form.message_template}
              onChange={(e) => set("message_template", e.target.value)}
              rows={3}
              style={{ width: "100%" }}
            />
          </label>
          <div style={{ marginTop: 8 }}>
            <button type="submit" style={btnStyle}>
              {editing ? "Update" : "Create"}
            </button>
            {editing && (
              <button
                type="button"
                style={{ ...btnStyle, marginLeft: 8 }}
                onClick={() => {
                  setEditing(null);
                  setForm(emptyForm);
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </form>
      </section>

      {/* Rules list */}
      <section style={{ ...card, marginTop: 16 }}>
        <h3>Rules ({data?.total ?? 0})</h3>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Channel</th>
              <th>Destination</th>
              <th>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data?.rules.map((rule) => (
              <tr key={rule.rule_id}>
                <td>{rule.rule_id}</td>
                <td>{rule.name}</td>
                <td>{rule.channel}</td>
                <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {rule.destination}
                </td>
                <td>{rule.is_active ? "Yes" : "No"}</td>
                <td>
                  <button style={btnStyle} onClick={() => startEdit(rule)}>
                    Edit
                  </button>
                  <button
                    style={{ ...btnStyle, marginLeft: 4 }}
                    onClick={() => testRule(rule.rule_id)}
                  >
                    Test
                  </button>
                  <button
                    style={{ ...btnStyle, marginLeft: 4 }}
                    onClick={() =>
                      setViewLogs(viewLogs === rule.rule_id ? null : rule.rule_id)
                    }
                  >
                    Logs
                  </button>
                  <button
                    style={{ ...btnStyle, marginLeft: 4, color: "#e74c3c" }}
                    onClick={() => {
                      if (confirm("Delete this rule?")) deleteMut.mutate(rule.rule_id);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Log viewer */}
      {viewLogs !== null && logsData && (
        <section style={{ ...card, marginTop: 16 }}>
          <h3>
            Delivery Logs for Rule {viewLogs} ({logsData.total} total)
          </h3>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th>Log ID</th>
                <th>Alert ID</th>
                <th>Channel</th>
                <th>Status</th>
                <th>Error</th>
                <th>Sent At</th>
              </tr>
            </thead>
            <tbody>
              {logsData.logs.map((log) => (
                <tr key={log.log_id}>
                  <td>{log.log_id}</td>
                  <td>{log.alert_id}</td>
                  <td>{log.channel}</td>
                  <td
                    style={{
                      color:
                        log.status === "sent"
                          ? "#27ae60"
                          : log.status === "throttled"
                            ? "#f39c12"
                            : "#e74c3c",
                    }}
                  >
                    {log.status}
                  </td>
                  <td>{log.error_message || "-"}</td>
                  <td>{log.sent_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
