import { useState } from "react";
import { Link } from "react-router-dom";
import { useMergeCandidates, useMergeChains } from "../hooks/useVessels";
import { apiFetch } from "../lib/api";
import { Card } from "../components/ui/Card";
import { Spinner } from "../components/ui/Spinner";
import { ScoreBadge } from "../components/ui/ScoreBadge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { MergeChainGraph } from "../components/MergeChainGraph";

const thStyle: React.CSSProperties = {
  padding: "8px 12px",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-muted)",
  whiteSpace: "nowrap",
  fontSize: 12,
};

const tdStyle: React.CSSProperties = { padding: "8px 12px", fontSize: 13 };

const statusColors: Record<string, string> = {
  pending: "var(--warning)",
  auto_merged: "var(--accent)",
  analyst_merged: "var(--score-low)",
  rejected: "var(--text-dim)",
};

type ViewMode = "table" | "graph";

export function MergeCandidatesPage() {
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [statusFilter, setStatusFilter] = useState("pending");
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, isLoading, error, refetch } = useMergeCandidates(statusFilter);
  const candidates = data?.items ?? [];
  const chainsQuery = useMergeChains(viewMode === "graph" ? {} : undefined);

  async function handleConfirm(candidateId: number) {
    setActionLoading(candidateId);
    setActionError(null);
    try {
      await apiFetch(`/merge-candidates/${candidateId}/confirm`, { method: "POST" });
      refetch();
    } catch (_err) {
      setActionError(`Failed to confirm #${candidateId}`);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleReject(candidateId: number) {
    setActionLoading(candidateId);
    setActionError(null);
    try {
      await apiFetch(`/merge-candidates/${candidateId}/reject`, { method: "POST" });
      refetch();
    } catch (_err) {
      setActionError(`Failed to reject #${candidateId}`);
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: "0 0 1rem", fontSize: "1rem", color: "var(--text-muted)" }}>
        Merge Candidates
      </h2>

      <Card style={{ marginBottom: "1rem" }}>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, color: "var(--text-muted)" }}>Status:</span>
          {["pending", "auto_merged", "analyst_merged", "rejected"].map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                background:
                  statusFilter === s && viewMode === "table" ? "var(--accent)" : "var(--bg-base)",
                color: statusFilter === s && viewMode === "table" ? "white" : "var(--text-body)",
                cursor: "pointer",
              }}
            >
              {s.replace(/_/g, " ")}
            </button>
          ))}

          <span style={{ marginLeft: "auto", fontSize: 13, color: "var(--text-muted)" }}>
            View:
          </span>
          {(["table", "graph"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                background: viewMode === mode ? "var(--accent)" : "var(--bg-base)",
                color: viewMode === mode ? "white" : "var(--text-body)",
                cursor: "pointer",
                textTransform: "capitalize",
              }}
            >
              {mode}
            </button>
          ))}
        </div>
      </Card>

      {viewMode === "table" && (
        <Card>
          {isLoading && <Spinner text="Loading merge candidates..." />}
          {error && <ErrorMessage error={error} subject="candidates" onRetry={refetch} />}
          {actionError && (
            <p style={{ color: "var(--score-critical)", fontSize: 13, padding: "0 12px" }}>
              {actionError}
            </p>
          )}

          {!isLoading && candidates.length === 0 && (
            <EmptyState
              title="No merge candidates"
              description={`No candidates with status "${statusFilter}".`}
            />
          )}

          {candidates.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "var(--bg-base)" }}>
                    <th style={thStyle}>ID</th>
                    <th style={thStyle}>Vessel A (went dark)</th>
                    <th style={thStyle}>Vessel B (appeared)</th>
                    <th style={thStyle}>Distance</th>
                    <th style={thStyle}>Gap</th>
                    <th style={thStyle}>Confidence</th>
                    <th style={thStyle}>SAR</th>
                    <th style={thStyle}>Status</th>
                    {statusFilter === "pending" && <th style={thStyle}>Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={c.candidate_id} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={tdStyle}>#{c.candidate_id}</td>
                      <td style={tdStyle}>
                        <Link
                          to={`/vessels/${c.vessel_a.vessel_id}`}
                          style={{ color: "var(--accent)" }}
                        >
                          {c.vessel_a.mmsi ?? "?"}
                        </Link>
                        <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                          {c.vessel_a.name ?? "--"}
                        </div>
                      </td>
                      <td style={tdStyle}>
                        <Link
                          to={`/vessels/${c.vessel_b.vessel_id}`}
                          style={{ color: "var(--accent)" }}
                        >
                          {c.vessel_b.mmsi ?? "?"}
                        </Link>
                        <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                          {c.vessel_b.name ?? "--"}
                        </div>
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                        {c.distance_nm != null ? `${c.distance_nm.toFixed(1)} nm` : "--"}
                      </td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                        {c.time_delta_hours != null ? `${c.time_delta_hours.toFixed(1)}h` : "--"}
                      </td>
                      <td style={tdStyle}>
                        <ScoreBadge score={c.confidence_score} size="sm" />
                      </td>
                      <td style={tdStyle}>
                        {c.satellite_corroboration ? (
                          <span style={{ color: "var(--score-low)" }}>Yes</span>
                        ) : (
                          <span style={{ color: "var(--text-dim)" }}>--</span>
                        )}
                      </td>
                      <td style={tdStyle}>
                        <span
                          style={{
                            color: statusColors[c.status] ?? "var(--text-body)",
                            fontWeight: 600,
                            fontSize: 11,
                            textTransform: "uppercase",
                          }}
                        >
                          {c.status.replace(/_/g, " ")}
                        </span>
                      </td>
                      {statusFilter === "pending" && (
                        <td style={tdStyle}>
                          <button
                            onClick={() => handleConfirm(c.candidate_id)}
                            disabled={actionLoading === c.candidate_id}
                            style={{
                              padding: "3px 8px",
                              fontSize: 11,
                              background: "var(--score-low)",
                              color: "white",
                              border: "none",
                              borderRadius: "var(--radius)",
                              cursor: actionLoading === c.candidate_id ? "wait" : "pointer",
                              marginRight: 4,
                              opacity: actionLoading === c.candidate_id ? 0.6 : 1,
                            }}
                          >
                            {actionLoading === c.candidate_id ? "Working..." : "Confirm"}
                          </button>
                          <button
                            onClick={() => handleReject(c.candidate_id)}
                            disabled={actionLoading === c.candidate_id}
                            style={{
                              padding: "3px 8px",
                              fontSize: 11,
                              background: "var(--bg-base)",
                              color: "var(--text-body)",
                              border: "1px solid var(--border)",
                              borderRadius: "var(--radius)",
                              cursor: actionLoading === c.candidate_id ? "wait" : "pointer",
                              opacity: actionLoading === c.candidate_id ? 0.6 : 1,
                            }}
                          >
                            Reject
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data?.total != null && data.total > 20 && (
            <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-muted)" }}>
              Showing 20 of {data.total} candidates.
            </div>
          )}
        </Card>
      )}

      {viewMode === "graph" && (
        <Card>
          {chainsQuery.isLoading && <Spinner text="Loading merge chains..." />}
          {chainsQuery.error && (
            <ErrorMessage
              error={chainsQuery.error}
              subject="merge chains"
              onRetry={chainsQuery.refetch}
            />
          )}

          {!chainsQuery.isLoading && (chainsQuery.data?.items ?? []).length === 0 && (
            <EmptyState
              title="No merge chains"
              description="No merge chains found. Chains are created when 3+ vessels are linked by confirmed merges."
            />
          )}

          {(chainsQuery.data?.items ?? []).map((chain) => (
            <div
              key={chain.chain_id}
              style={{
                borderBottom: "1px solid var(--border)",
                padding: "16px 12px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>Chain #{chain.chain_id}</span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {chain.chain_length} vessels
                </span>
                <ScoreBadge score={Math.round(chain.confidence * 100)} size="sm" />
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    textTransform: "uppercase",
                    color:
                      chain.confidence_band === "HIGH"
                        ? "#22c55e"
                        : chain.confidence_band === "MEDIUM"
                          ? "#f59e0b"
                          : "#ef4444",
                  }}
                >
                  {chain.confidence_band}
                </span>
              </div>
              <div style={{ overflowX: "auto" }}>
                <MergeChainGraph
                  nodes={chain.nodes}
                  edges={chain.edges}
                  confidenceBand={chain.confidence_band}
                />
              </div>
            </div>
          ))}

          {chainsQuery.data?.total != null && chainsQuery.data.total > 50 && (
            <div
              style={{
                marginTop: 8,
                fontSize: 13,
                color: "var(--text-muted)",
                padding: "0 12px 12px",
              }}
            >
              Showing 50 of {chainsQuery.data.total} chains.
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
