import { useState } from "react";
import type { ExportResponse } from "../types/api";
import { apiFetch } from "../lib/api";
import { btnStyle } from "../styles/tables";
import { useAuth } from "../hooks/useAuth";

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertExportPanelProps {
  alertId: string;
  approvalStatus?: string | null;
  onApprovalChange?: (newStatus: string) => void;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const approvalBadgeColors: Record<string, string> = {
  approved: "#22c55e",
  rejected: "#ef4444",
  pending: "#f59e0b",
};

const CSV_COLUMNS = [
  "alert_id",
  "vessel_mmsi",
  "vessel_name",
  "flag",
  "dwt",
  "gap_start_utc",
  "gap_end_utc",
  "duration_hours",
  "corridor_name",
  "risk_score",
  "status",
  "analyst_notes",
] as const;

type CsvColumn = (typeof CSV_COLUMNS)[number];

const API_BASE = "/api/v1";

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertExportPanel({
  alertId,
  approvalStatus,
  onApprovalChange,
}: AlertExportPanelProps) {
  const [exportError, setExportError] = useState<string | null>(null);
  const [satLoading, setSatLoading] = useState(false);
  const [satResult, setSatResult] = useState<string | null>(null);
  const [approvalLoading, setApprovalLoading] = useState(false);
  const [csvColumnsOpen, setCsvColumnsOpen] = useState(false);
  const [selectedColumns, setSelectedColumns] = useState<Set<CsvColumn>>(new Set(CSV_COLUMNS));

  const { isAuthenticated, isSeniorOrAdmin: canApprove } = useAuth();

  const handleExport = async (fmt: "md" | "json") => {
    setExportError(null);
    try {
      const data = await apiFetch<ExportResponse>(`/alerts/${alertId}/export?format=${fmt}`, {
        method: "POST",
      });
      const content = data.content ?? JSON.stringify(data, null, 2);
      const blob = new Blob([content], {
        type: fmt === "json" ? "application/json" : "text/markdown",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `alert_${alertId}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed");
    }
  };

  const handleCsvExport = async () => {
    setExportError(null);
    try {
      const params = new URLSearchParams({ ids: alertId });
      if (selectedColumns.size < CSV_COLUMNS.length && selectedColumns.size > 0) {
        params.set("columns", Array.from(selectedColumns).join(","));
      }
      const token = localStorage.getItem("rf_token");
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`${API_BASE}/alerts/export?${params.toString()}`, { headers });
      if (!res.ok) throw new Error(`CSV export failed: ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `alert_${alertId}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "CSV export failed");
    }
  };

  const toggleColumn = (col: CsvColumn) => {
    setSelectedColumns((prev) => {
      const next = new Set(prev);
      if (next.has(col)) {
        next.delete(col);
      } else {
        next.add(col);
      }
      return next;
    });
  };

  const toggleAllColumns = () => {
    if (selectedColumns.size === CSV_COLUMNS.length) {
      setSelectedColumns(new Set());
    } else {
      setSelectedColumns(new Set(CSV_COLUMNS));
    }
  };

  const handleSatelliteCheck = async () => {
    setSatLoading(true);
    setSatResult(null);
    try {
      await apiFetch(`/alerts/${alertId}/satellite-check`, { method: "POST" });
      setSatResult("Satellite check prepared");
    } catch (err) {
      setSatResult(err instanceof Error ? err.message : "Failed to prepare satellite check");
    } finally {
      setSatLoading(false);
    }
  };

  const handleApproval = async (action: "approve" | "reject") => {
    setApprovalLoading(true);
    try {
      await apiFetch(`/alerts/${alertId}/evidence/${action}`, { method: "POST" });
      onApprovalChange?.(action === "approve" ? "approved" : "rejected");
    } catch (err) {
      setExportError(err instanceof Error ? err.message : `Failed to ${action}`);
    } finally {
      setApprovalLoading(false);
    }
  };

  const statusLabel = approvalStatus ?? "pending";
  const badgeColor = approvalBadgeColors[statusLabel] ?? "var(--text-dim)";

  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {isAuthenticated && (
          <>
            <button
              onClick={() => handleExport("md")}
              style={{ ...btnStyle, background: "var(--bg-base)", color: "var(--accent)" }}
            >
              Export Markdown
            </button>
            <button
              onClick={() => handleExport("json")}
              style={{ ...btnStyle, background: "var(--bg-base)", color: "var(--accent)" }}
            >
              Export JSON
            </button>
          </>
        )}
        <button
          onClick={() => setCsvColumnsOpen((prev) => !prev)}
          style={{ ...btnStyle, background: "var(--bg-base)", color: "var(--accent)" }}
        >
          Export CSV {csvColumnsOpen ? "\u25B2" : "\u25BC"}
        </button>
        <button
          onClick={handleSatelliteCheck}
          disabled={satLoading}
          style={{
            ...btnStyle,
            background: "var(--bg-base)",
            color: "var(--warning)",
            opacity: satLoading ? 0.6 : 1,
          }}
        >
          {satLoading ? "Preparing..." : "Prepare satellite check"}
        </button>
      </div>

      {/* CSV column picker */}
      {csvColumnsOpen && (
        <div
          style={{
            marginTop: 8,
            padding: 10,
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            background: "var(--bg-base)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 6,
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)" }}>
              Select columns ({selectedColumns.size}/{CSV_COLUMNS.length})
            </span>
            <button
              onClick={toggleAllColumns}
              style={{ ...btnStyle, fontSize: 11, padding: "2px 8px" }}
            >
              {selectedColumns.size === CSV_COLUMNS.length ? "Deselect all" : "Select all"}
            </button>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px" }}>
            {CSV_COLUMNS.map((col) => (
              <label
                key={col}
                style={{
                  fontSize: 12,
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedColumns.has(col)}
                  onChange={() => toggleColumn(col)}
                />
                {col}
              </label>
            ))}
          </div>
          <button
            onClick={handleCsvExport}
            disabled={selectedColumns.size === 0}
            style={{
              ...btnStyle,
              marginTop: 8,
              background: "var(--accent)",
              color: "#fff",
              opacity: selectedColumns.size === 0 ? 0.5 : 1,
            }}
          >
            Download CSV
          </button>
        </div>
      )}

      {/* Evidence approval status */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Evidence:</span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            padding: "2px 8px",
            borderRadius: "var(--radius)",
            background: badgeColor,
            color: "#fff",
            textTransform: "uppercase",
            letterSpacing: 0.5,
          }}
        >
          {statusLabel}
        </span>
        {canApprove && statusLabel !== "approved" && (
          <button
            onClick={() => handleApproval("approve")}
            disabled={approvalLoading}
            style={{
              ...btnStyle,
              fontSize: 11,
              padding: "2px 10px",
              color: "#22c55e",
              borderColor: "#22c55e",
            }}
          >
            Approve
          </button>
        )}
        {canApprove && statusLabel !== "rejected" && (
          <button
            onClick={() => handleApproval("reject")}
            disabled={approvalLoading}
            style={{
              ...btnStyle,
              fontSize: 11,
              padding: "2px 10px",
              color: "#ef4444",
              borderColor: "#ef4444",
            }}
          >
            Reject
          </button>
        )}
      </div>

      {exportError && (
        <p style={{ fontSize: 12, color: "var(--score-critical)", marginTop: 8 }}>{exportError}</p>
      )}
      {satResult && (
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>{satResult}</p>
      )}
      <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 12 }}>
        Note: export requires status &ne; &quot;new&quot; (analyst review gate -- NFR7)
      </p>
    </>
  );
}
