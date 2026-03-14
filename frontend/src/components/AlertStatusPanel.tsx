import { useState, useEffect } from "react";
import type { AlertStatus } from "../types/api";
import type { UseMutationResult } from "@tanstack/react-query";
import { card, sectionHead, btnStyle } from "../styles/tables";

const STATUSES: AlertStatus[] = [
  "new",
  "under_review",
  "needs_satellite_check",
  "documented",
  "dismissed",
  "confirmed_fp",
  "confirmed_tp",
];

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertStatusPanelProps {
  currentStatus: string;
  analystNotes: string | null;
  statusMutation: UseMutationResult<unknown, Error, { status: string }, unknown>;
  notesMutation: UseMutationResult<unknown, Error, string, unknown>;
  is_false_positive?: boolean | null;
  reviewed_by?: string | null;
  review_date?: string | null;
  verdictMutation: UseMutationResult<
    unknown,
    Error,
    { verdict: string; reason?: string; reviewed_by?: string },
    unknown
  >;
  readOnly?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertStatusPanel({
  currentStatus,
  analystNotes,
  statusMutation,
  notesMutation,
  is_false_positive,
  reviewed_by,
  review_date,
  verdictMutation,
  readOnly,
}: AlertStatusPanelProps) {
  const [notes, setNotes] = useState("");
  const [saved, setSaved] = useState(false);
  const [verdictReason, setVerdictReason] = useState("");

  useEffect(() => {
    if (analystNotes) setNotes(analystNotes); // eslint-disable-line react-hooks/set-state-in-effect -- sync prop to local state for controlled input
  }, [analystNotes]);

  // Auto-dismiss "Saved" after 3 seconds
  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 3000);
    return () => clearTimeout(timer);
  }, [saved]);

  return (
    <section style={card}>
      <h3 style={sectionHead}>Analyst Workflow</h3>

      {/* Error banners */}
      {statusMutation.error && (
        <div
          style={{
            background: "var(--score-critical)",
            color: "white",
            padding: "8px 12px",
            borderRadius: "var(--radius)",
            marginBottom: 12,
            fontSize: 13,
          }}
        >
          Status update failed:{" "}
          {statusMutation.error instanceof Error ? statusMutation.error.message : "Unknown error"}
        </div>
      )}
      {notesMutation.error && (
        <div
          style={{
            background: "var(--score-critical)",
            color: "white",
            padding: "8px 12px",
            borderRadius: "var(--radius)",
            marginBottom: 12,
            fontSize: 13,
          }}
        >
          Notes save failed:{" "}
          {notesMutation.error instanceof Error ? notesMutation.error.message : "Unknown error"}
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: "var(--text-dim)" }}>Status</label>
        <br />
        <select
          value={currentStatus}
          onChange={(e) => statusMutation.mutate({ status: e.target.value })}
          disabled={readOnly || statusMutation.isPending}
          style={{
            background: "var(--bg-base)",
            color: "var(--text-bright)",
            border: "1px solid var(--border)",
            padding: "6px 10px",
            borderRadius: "var(--radius)",
            marginTop: 4,
            fontSize: 13,
            opacity: readOnly || statusMutation.isPending ? 0.6 : 1,
          }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        {statusMutation.isPending && (
          <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 8 }}>Saving…</span>
        )}
      </div>
      <div style={{ marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: "var(--text-dim)" }}>Analyst Notes</label>
        <br />
        <textarea
          value={notes}
          onChange={(e) => {
            setNotes(e.target.value);
            setSaved(false);
          }}
          disabled={readOnly}
          rows={4}
          style={{
            width: "100%",
            background: "var(--bg-base)",
            color: "var(--text-bright)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: 8,
            marginTop: 4,
            fontFamily: "monospace",
            fontSize: 13,
            boxSizing: "border-box",
            resize: "vertical",
          }}
        />
        <button
          onClick={() => notesMutation.mutate(notes)}
          disabled={readOnly || notesMutation.isPending}
          style={{
            ...btnStyle,
            background: "var(--accent-primary)",
            color: "#fff",
            border: "none",
            marginTop: 6,
            opacity: notesMutation.isPending ? 0.6 : 1,
          }}
        >
          {notesMutation.isPending ? "Saving…" : saved ? "✓ Saved" : "Save Notes"}
        </button>
      </div>

      {/* Analyst Verdict */}
      <div style={{ marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: "var(--text-dim)" }}>Analyst Verdict</label>
        {is_false_positive != null && (
          <div
            style={{
              marginTop: 4,
              marginBottom: 8,
              padding: "8px 12px",
              background: "var(--bg-base)",
              borderRadius: "var(--radius)",
              fontSize: 13,
            }}
          >
            <div>
              Verdict: <strong>{is_false_positive ? "False Positive" : "True Positive"}</strong>
            </div>
            {reviewed_by && <div>Reviewed by: {reviewed_by}</div>}
            {review_date && <div>Date: {new Date(review_date).toLocaleString()}</div>}
          </div>
        )}
        {verdictMutation.error && (
          <div
            style={{
              background: "var(--score-critical)",
              color: "white",
              padding: "8px 12px",
              borderRadius: "var(--radius)",
              marginBottom: 8,
              fontSize: 13,
            }}
          >
            Verdict failed:{" "}
            {verdictMutation.error instanceof Error
              ? verdictMutation.error.message
              : "Unknown error"}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
          <input
            type="text"
            placeholder="Reason (optional)"
            value={verdictReason}
            onChange={(e) => setVerdictReason(e.target.value)}
            disabled={readOnly}
            style={{
              flex: 1,
              background: "var(--bg-base)",
              color: "var(--text-bright)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "6px 10px",
              fontSize: 13,
            }}
          />
          <button
            onClick={() =>
              verdictMutation.mutate({
                verdict: "confirmed_tp",
                reason: verdictReason || undefined,
              })
            }
            disabled={readOnly || verdictMutation.isPending}
            style={{
              ...btnStyle,
              background: "#2e7d32",
              color: "#fff",
              border: "none",
              opacity: verdictMutation.isPending ? 0.6 : 1,
            }}
          >
            Confirm TP
          </button>
          <button
            onClick={() =>
              verdictMutation.mutate({
                verdict: "confirmed_fp",
                reason: verdictReason || undefined,
              })
            }
            disabled={readOnly || verdictMutation.isPending}
            style={{
              ...btnStyle,
              background: "#c62828",
              color: "#fff",
              border: "none",
              opacity: verdictMutation.isPending ? 0.6 : 1,
            }}
          >
            Mark FP
          </button>
        </div>
      </div>
    </section>
  );
}
