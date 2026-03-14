import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { NetworkGraph } from "../components/NetworkGraph";
import { NetworkGraphFilters } from "../components/NetworkGraphFilters";
import {
  useFleetOwnershipNetwork,
  useVesselOwnershipNetwork,
  type NetworkFilters,
} from "../hooks/useOwnershipNetwork";

export default function OwnershipNetworkPage() {
  const [searchParams] = useSearchParams();
  const vesselIdParam = searchParams.get("vessel_id");
  const vesselId = vesselIdParam ? Number(vesselIdParam) : null;

  const [filters, setFilters] = useState<NetworkFilters>({
    sanctioned_only: false,
    spv_only: false,
    jurisdiction: null,
    depth: 3,
    limit: 100,
  });

  // Use vessel-specific or fleet-wide query based on URL param
  const vesselQuery = useVesselOwnershipNetwork(
    vesselId,
    filters.depth ?? 3,
    filters.limit ?? 100,
  );
  const fleetQuery = useFleetOwnershipNetwork(
    vesselId ? { depth: filters.depth, limit: filters.limit } : filters,
  );

  const query = vesselId ? vesselQuery : fleetQuery;
  const data = query.data;

  // Extract unique jurisdictions for filter dropdown
  const jurisdictions = data
    ? [
        ...new Set(
          data.nodes
            .map((n) => n.jurisdiction)
            .filter((j): j is string => j != null),
        ),
      ].sort()
    : [];

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>
        Ownership Network
        {vesselId && <span style={{ fontWeight: 400, fontSize: 16 }}> (Vessel #{vesselId})</span>}
      </h1>

      <NetworkGraphFilters
        filters={filters}
        onFiltersChange={setFilters}
        jurisdictions={jurisdictions}
      />

      {query.isLoading && (
        <div style={{ padding: 24, color: "var(--text-muted, #94a3b8)" }}>
          Loading network...
        </div>
      )}

      {query.error && (
        <div style={{ padding: 24, color: "#ef4444" }}>
          Error loading network: {String(query.error)}
        </div>
      )}

      {data && (
        <>
          {/* Stats bar */}
          <div
            style={{
              display: "flex",
              gap: 24,
              padding: "8px 0",
              fontSize: 13,
              color: "var(--text-muted, #94a3b8)",
            }}
          >
            <span>Nodes: {data.stats.total_nodes}</span>
            <span>Edges: {data.stats.total_edges}</span>
            <span>Depth: {data.stats.max_depth}</span>
            {data.stats.sanctioned_count > 0 && (
              <span style={{ color: "#ef4444" }}>
                Sanctioned: {data.stats.sanctioned_count}
              </span>
            )}
            {data.stats.spv_count > 0 && (
              <span style={{ color: "#f59e0b" }}>
                SPV: {data.stats.spv_count}
              </span>
            )}
          </div>

          <NetworkGraph
            nodes={data.nodes}
            edges={data.edges}
            sanctionsPaths={data.sanctions_paths}
          />
        </>
      )}
    </div>
  );
}
