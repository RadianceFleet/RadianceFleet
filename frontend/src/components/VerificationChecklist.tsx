import { useState } from "react";
import {
  useChecklist,
  useCreateChecklist,
  useToggleChecklistItem,
} from "../hooks/useVerificationChecklist";
import { card, sectionHead, btnStyle } from "../styles/tables";

interface VerificationChecklistProps {
  alertId: string;
  readOnly?: boolean;
}

const templateLabels: Record<string, string> = {
  standard: "Standard Review",
  high_risk: "High Risk Review",
  sts_zone: "STS Zone Review",
};

export function VerificationChecklist({
  alertId,
  readOnly = false,
}: VerificationChecklistProps) {
  const { data: checklist, isLoading, error } = useChecklist(alertId);
  const createMutation = useCreateChecklist(alertId);
  const toggleMutation = useToggleChecklistItem(alertId);
  const [notesInput, setNotesInput] = useState<Record<number, string>>({});

  const hasChecklist = !!checklist && !error;
  const items = checklist?.items ?? [];
  const checkedCount = items.filter((i) => i.is_checked).length;
  const totalCount = items.length;
  const progressPct = totalCount > 0 ? (checkedCount / totalCount) * 100 : 0;

  if (isLoading) {
    return (
      <section style={card}>
        <h3 style={sectionHead}>Evidence Verification Checklist</h3>
        <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
          Loading checklist...
        </p>
      </section>
    );
  }

  if (!hasChecklist) {
    return (
      <section style={card}>
        <h3 style={sectionHead}>Evidence Verification Checklist</h3>
        {!readOnly && (
          <div>
            <p
              style={{
                fontSize: 13,
                color: "var(--text-dim)",
                marginBottom: 12,
              }}
            >
              No checklist has been created for this alert yet.
            </p>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              style={btnStyle}
            >
              {createMutation.isPending
                ? "Creating..."
                : "Create Checklist"}
            </button>
            {createMutation.isError && (
              <p
                style={{
                  fontSize: 12,
                  color: "var(--score-critical)",
                  marginTop: 8,
                }}
              >
                Failed to create checklist.
              </p>
            )}
          </div>
        )}
        {readOnly && (
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
            No checklist available.
          </p>
        )}
      </section>
    );
  }

  return (
    <section style={card}>
      <h3 style={sectionHead}>Evidence Verification Checklist</h3>

      {/* Template name */}
      <div
        style={{
          fontSize: 12,
          color: "var(--text-dim)",
          marginBottom: 8,
        }}
      >
        Template:{" "}
        <strong>
          {templateLabels[checklist.checklist_template] ??
            checklist.checklist_template}
        </strong>
      </div>

      {/* Progress bar */}
      <div style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 12,
            color: "var(--text-dim)",
            marginBottom: 4,
          }}
        >
          <span>
            {checkedCount} / {totalCount} completed
          </span>
          <span>{progressPct.toFixed(0)}%</span>
        </div>
        <div
          style={{
            height: 8,
            background: "var(--bg-base)",
            borderRadius: 4,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${progressPct}%`,
              background:
                progressPct === 100
                  ? "#27ae60"
                  : progressPct >= 50
                    ? "#f39c12"
                    : "#e74c3c",
              borderRadius: 4,
              transition: "width 0.3s ease",
            }}
          />
        </div>
      </div>

      {/* Completion status */}
      {checklist.completed_at && (
        <div
          style={{
            background: "rgba(39, 174, 96, 0.1)",
            border: "1px solid rgba(39, 174, 96, 0.3)",
            borderRadius: "var(--radius)",
            padding: "8px 12px",
            fontSize: 12,
            color: "#27ae60",
            marginBottom: 12,
          }}
        >
          Completed at{" "}
          {checklist.completed_at.slice(0, 19).replace("T", " ")}
        </div>
      )}

      {/* Checklist items */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {items.map((item) => (
          <div
            key={item.item_id}
            style={{
              display: "flex",
              flexDirection: "column",
              padding: "8px 12px",
              background: item.is_checked
                ? "rgba(39, 174, 96, 0.05)"
                : "var(--bg-base)",
              borderRadius: "var(--radius)",
              border: `1px solid ${item.is_checked ? "rgba(39, 174, 96, 0.2)" : "var(--border)"}`,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <input
                type="checkbox"
                checked={item.is_checked}
                disabled={readOnly || toggleMutation.isPending}
                onChange={(e) => {
                  toggleMutation.mutate({
                    itemId: item.item_id,
                    isChecked: e.target.checked,
                    notes: notesInput[item.item_id],
                  });
                }}
                style={{ cursor: readOnly ? "default" : "pointer" }}
              />
              <span
                style={{
                  fontSize: 13,
                  color: item.is_checked
                    ? "var(--text-muted)"
                    : "var(--text-primary, #e0e0e0)",
                  textDecoration: item.is_checked
                    ? "line-through"
                    : "none",
                  flex: 1,
                }}
              >
                {item.label}
              </span>
            </div>

            {/* Who-checked metadata */}
            {item.is_checked && item.checked_at && (
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-dim)",
                  marginLeft: 24,
                  marginTop: 4,
                }}
              >
                Checked by analyst #{item.checked_by} at{" "}
                {item.checked_at.slice(0, 19).replace("T", " ")}
              </div>
            )}

            {/* Existing notes */}
            {item.notes && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginLeft: 24,
                  marginTop: 4,
                  fontStyle: "italic",
                }}
              >
                {item.notes}
              </div>
            )}

            {/* Notes input */}
            {!readOnly && !item.is_checked && (
              <div style={{ marginLeft: 24, marginTop: 4 }}>
                <input
                  type="text"
                  placeholder="Add notes (optional)"
                  value={notesInput[item.item_id] ?? ""}
                  onChange={(e) =>
                    setNotesInput((prev) => ({
                      ...prev,
                      [item.item_id]: e.target.value,
                    }))
                  }
                  style={{
                    width: "100%",
                    padding: "4px 8px",
                    fontSize: 12,
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    background: "var(--bg-card)",
                    color: "var(--text-muted)",
                  }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
