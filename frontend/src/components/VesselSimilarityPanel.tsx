import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useVesselSimilarity } from "../hooks/useVesselSimilarity";
import type { SimilarVessel } from "../hooks/useVesselSimilarity";
import {
  card,
  sectionHead,
  tableStyle,
  thStyle,
  theadRow,
  tbodyRow,
  tdStyle,
} from "../styles/tables";

/* ------------------------------------------------------------------ */
/*  Tier badge                                                         */
/* ------------------------------------------------------------------ */

const tierColors: Record<string, string> = {
  HIGH: "#27ae60",
  MEDIUM: "#f39c12",
  LOW: "#95a5a6",
};

function TierBadge({ tier }: { tier: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "var(--radius)",
        fontSize: 11,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        background: tierColors[tier] ?? "var(--text-dim)",
        color: "white",
      }}
    >
      {tier}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Score bar                                                           */
/* ------------------------------------------------------------------ */

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.7 ? "#27ae60" : score >= 0.4 ? "#f39c12" : "#e74c3c";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <div
        style={{
          flex: 1,
          height: 8,
          background: "var(--bg-base)",
          borderRadius: 4,
          overflow: "hidden",
          minWidth: 60,
          maxWidth: 120,
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: color,
            borderRadius: 4,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span style={{ fontSize: 12, color: "var(--text-muted)", minWidth: 32 }}>
        {pct}%
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Ownership indicators                                               */
/* ------------------------------------------------------------------ */

const ownershipLabels: Record<string, string> = {
  shared_cluster: "Cluster",
  shared_ism_manager: "ISM",
  shared_pi_club: "P&I",
  same_owner_name: "Owner",
  same_country: "Country",
};

function OwnershipIndicators({
  breakdown,
}: {
  breakdown: Record<string, boolean>;
}) {
  const matched = Object.entries(breakdown).filter(([, v]) => v);
  if (matched.length === 0) {
    return (
      <span style={{ fontSize: 11, color: "var(--text-dim)" }}>None</span>
    );
  }
  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {matched.map(([key]) => (
        <span
          key={key}
          style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: "var(--radius)",
            fontSize: 10,
            fontWeight: 600,
            background: "rgba(39, 174, 96, 0.15)",
            color: "#27ae60",
            border: "1px solid rgba(39, 174, 96, 0.3)",
          }}
        >
          {ownershipLabels[key] ?? key}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail row                                                         */
/* ------------------------------------------------------------------ */

function DetailRow({ vessel }: { vessel: SimilarVessel }) {
  return (
    <tr>
      <td colSpan={5} style={{ ...tdStyle, background: "var(--bg-base)" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 16,
            fontSize: 12,
            padding: "4px 0",
          }}
        >
          <div>
            <strong style={{ color: "var(--text-muted)" }}>
              Fingerprint Distance
            </strong>
            <br />
            {vessel.fingerprint_distance.toFixed(2)} ({vessel.fingerprint_band})
          </div>
          <div>
            <strong style={{ color: "var(--text-muted)" }}>
              Fingerprint Similarity
            </strong>
            <br />
            {(vessel.fingerprint_similarity * 100).toFixed(1)}%
          </div>
          <div>
            <strong style={{ color: "var(--text-muted)" }}>
              Ownership Score
            </strong>
            <br />
            {(vessel.ownership_similarity_score * 100).toFixed(1)}%
          </div>
        </div>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Main panel                                                         */
/* ------------------------------------------------------------------ */

interface VesselSimilarityPanelProps {
  vesselId: string;
}

export function VesselSimilarityPanel({
  vesselId,
}: VesselSimilarityPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const { data, isLoading, error } = useVesselSimilarity(
    expanded ? vesselId : undefined
  );

  const vessels = data?.similar_vessels ?? [];

  return (
    <section style={card}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
        }}
        onClick={() => setExpanded(!expanded)}
        data-testid="similarity-toggle"
      >
        <h3 style={{ ...sectionHead, margin: 0 }}>
          Similar Vessels {expanded ? "\u25B2" : "\u25BC"}
        </h3>
        {data && (
          <span
            style={{ fontSize: 12, color: "var(--text-dim)" }}
            data-testid="similarity-count"
          >
            {data.total} found
          </span>
        )}
      </div>

      {expanded && (
        <div style={{ marginTop: 12 }}>
          {isLoading && (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              Finding similar vessels...
            </p>
          )}
          {error && (
            <p style={{ fontSize: 13, color: "var(--score-critical)" }}>
              Failed to load similar vessels.
            </p>
          )}
          {!isLoading && !error && vessels.length === 0 && (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              No similar vessels found.
            </p>
          )}
          {vessels.length > 0 && (
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
                  <th style={thStyle}>Vessel</th>
                  <th style={thStyle}>Composite Score</th>
                  <th style={thStyle}>Ownership</th>
                  <th style={thStyle}>Tier</th>
                  <th style={thStyle}></th>
                </tr>
              </thead>
              <tbody>
                {vessels.map((v) => (
                  <Fragment key={v.target_vessel_id}>
                    <tr
                      style={tbodyRow}
                      data-testid={`similarity-row-${v.target_vessel_id}`}
                    >
                      <td style={tdStyle}>
                        <Link to={`/vessels/${v.target_vessel_id}`}>
                          Vessel #{v.target_vessel_id}
                        </Link>
                      </td>
                      <td style={tdStyle}>
                        <ScoreBar score={v.composite_similarity_score} />
                      </td>
                      <td style={tdStyle}>
                        <OwnershipIndicators
                          breakdown={v.ownership_breakdown ?? {}}
                        />
                      </td>
                      <td style={tdStyle}>
                        <TierBadge tier={v.similarity_tier} />
                      </td>
                      <td style={tdStyle}>
                        <button
                          onClick={() =>
                            setExpandedRow(
                              expandedRow === v.target_vessel_id
                                ? null
                                : v.target_vessel_id
                            )
                          }
                          style={{
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                            fontSize: 12,
                            color: "var(--text-muted)",
                          }}
                          data-testid={`expand-details-${v.target_vessel_id}`}
                        >
                          {expandedRow === v.target_vessel_id
                            ? "Hide"
                            : "Details"}
                        </button>
                      </td>
                    </tr>
                    {expandedRow === v.target_vessel_id && (
                      <DetailRow vessel={v} />
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
