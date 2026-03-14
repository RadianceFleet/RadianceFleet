/**
 * AssignmentPanel — UI for alert handoff workflow.
 *
 * Shows:
 * - Dropdown to select a target analyst (sorted by workload, least loaded first)
 * - Notes textarea for handoff context
 * - Submit button to execute the handoff
 * - Chronological handoff history list
 */

import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

interface WorkloadEntry {
  analyst_id: number;
  analyst_name: string;
  open_alerts: number;
  assigned_alerts: number;
  avg_resolution_hours: number | null;
}

interface HandoffHistoryEntry {
  handoff_id: number;
  from_analyst: string;
  to_analyst: string;
  notes: string;
  created_at: string;
}

export interface AssignmentPanelProps {
  alertId: number;
  /** Current analyst ID (to exclude from target list) */
  currentAnalystId?: number;
}

export default function AssignmentPanel({
  alertId,
  currentAnalystId,
}: AssignmentPanelProps) {
  const queryClient = useQueryClient();
  const [selectedAnalystId, setSelectedAnalystId] = useState<number | "">("");
  const [notes, setNotes] = useState("");

  // Fetch workload data for analyst dropdown
  const { data: workload = [], isLoading: loadingWorkload } =
    useQuery<WorkloadEntry[]>({
      queryKey: ["analyst-workload"],
      queryFn: () => apiFetch<WorkloadEntry[]>("/analysts/workload"),
      staleTime: 30_000,
    });

  // Fetch handoff history
  const { data: history = [], isLoading: loadingHistory } = useQuery<
    HandoffHistoryEntry[]
  >({
    queryKey: ["handoff-history", alertId],
    queryFn: () =>
      apiFetch<HandoffHistoryEntry[]>(
        `/alerts/${alertId}/handoff-history`
      ),
    staleTime: 10_000,
  });

  // Handoff mutation
  const handoffMutation = useMutation({
    mutationFn: (body: { to_analyst_id: number; notes: string }) =>
      apiFetch(`/alerts/${alertId}/handoff`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setSelectedAnalystId("");
      setNotes("");
      queryClient.invalidateQueries({ queryKey: ["handoff-history", alertId] });
      queryClient.invalidateQueries({ queryKey: ["analyst-workload"] });
    },
  });

  const availableAnalysts = workload.filter(
    (a) => a.analyst_id !== currentAnalystId
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedAnalystId === "") return;
    handoffMutation.mutate({
      to_analyst_id: Number(selectedAnalystId),
      notes,
    });
  };

  return (
    <div
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: 16,
        marginTop: 12,
      }}
      data-testid="assignment-panel"
    >
      <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Alert Handoff</h3>

      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: 8 }}>
          <label
            htmlFor="target-analyst"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Hand off to:
          </label>
          <select
            id="target-analyst"
            value={selectedAnalystId}
            onChange={(e) =>
              setSelectedAnalystId(
                e.target.value === "" ? "" : Number(e.target.value)
              )
            }
            style={{
              width: "100%",
              padding: "6px 8px",
              borderRadius: 4,
              border: "1px solid #cbd5e1",
            }}
            disabled={loadingWorkload}
          >
            <option value="">Select analyst...</option>
            {availableAnalysts.map((a) => (
              <option key={a.analyst_id} value={a.analyst_id}>
                {a.analyst_name} ({a.open_alerts} open)
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: 8 }}>
          <label
            htmlFor="handoff-notes"
            style={{ display: "block", fontSize: 13, marginBottom: 4 }}
          >
            Notes:
          </label>
          <textarea
            id="handoff-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Context for the receiving analyst..."
            rows={3}
            style={{
              width: "100%",
              padding: "6px 8px",
              borderRadius: 4,
              border: "1px solid #cbd5e1",
              resize: "vertical",
            }}
          />
        </div>

        <button
          type="submit"
          disabled={
            selectedAnalystId === "" || handoffMutation.isPending
          }
          style={{
            padding: "6px 16px",
            borderRadius: 4,
            border: "none",
            backgroundColor:
              selectedAnalystId === "" ? "#94a3b8" : "#3b82f6",
            color: "#fff",
            cursor:
              selectedAnalystId === "" ? "not-allowed" : "pointer",
            fontSize: 14,
          }}
        >
          {handoffMutation.isPending ? "Handing off..." : "Hand Off"}
        </button>

        {handoffMutation.isError && (
          <p style={{ color: "#ef4444", fontSize: 13, marginTop: 4 }}>
            Handoff failed. Please try again.
          </p>
        )}
      </form>

      {/* Handoff History */}
      <div style={{ marginTop: 16 }}>
        <h4 style={{ margin: "0 0 8px", fontSize: 14 }}>Handoff History</h4>
        {loadingHistory ? (
          <p style={{ fontSize: 13, color: "#64748b" }}>Loading...</p>
        ) : history.length === 0 ? (
          <p style={{ fontSize: 13, color: "#64748b" }}>
            No handoffs yet for this alert.
          </p>
        ) : (
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
              fontSize: 13,
            }}
          >
            {history.map((h) => (
              <li
                key={h.handoff_id}
                style={{
                  padding: "6px 0",
                  borderBottom: "1px solid #f1f5f9",
                }}
              >
                <strong>{h.from_analyst}</strong> handed off to{" "}
                <strong>{h.to_analyst}</strong>
                <br />
                <span style={{ color: "#64748b" }}>
                  {new Date(h.created_at).toLocaleString()}
                </span>
                {h.notes && (
                  <p style={{ margin: "4px 0 0", color: "#475569" }}>
                    {h.notes}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
