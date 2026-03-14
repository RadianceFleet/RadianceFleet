import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface NetworkNode {
  id: string;
  type: "company" | "vessel";
  label: string;
  layer: number;
  is_sanctioned: boolean;
  is_spv: boolean;
  jurisdiction: string | null;
  vessel_id?: number;
  owner_id?: number;
}

export interface NetworkEdge {
  source: string;
  target: string;
  relationship: string;
}

export interface NetworkStats {
  total_nodes: number;
  total_edges: number;
  max_depth: number;
  sanctioned_count: number;
  spv_count: number;
}

export interface OwnershipNetworkData {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  sanctions_paths: string[][];
  stats: NetworkStats;
}

export interface NetworkFilters {
  sanctioned_only?: boolean;
  spv_only?: boolean;
  jurisdiction?: string | null;
  depth?: number;
  limit?: number;
}

export function useVesselOwnershipNetwork(
  vesselId: number | null,
  depth: number = 3,
  limit: number = 100,
) {
  return useQuery({
    queryKey: ["ownership-network", "vessel", vesselId, depth, limit],
    queryFn: () =>
      apiFetch<OwnershipNetworkData>(
        `/detect/ownership-network/${vesselId}?depth=${depth}&limit=${limit}`,
      ),
    enabled: vesselId !== null,
    staleTime: 5 * 60_000,
  });
}

export function useFleetOwnershipNetwork(filters: NetworkFilters = {}) {
  const params = new URLSearchParams();
  if (filters.sanctioned_only) params.set("sanctioned_only", "true");
  if (filters.spv_only) params.set("spv_only", "true");
  if (filters.jurisdiction) params.set("jurisdiction", filters.jurisdiction);
  if (filters.depth) params.set("depth", String(filters.depth));
  if (filters.limit) params.set("limit", String(filters.limit));

  const qs = params.toString();
  return useQuery({
    queryKey: ["ownership-network", "fleet", filters],
    queryFn: () =>
      apiFetch<OwnershipNetworkData>(
        `/detect/ownership-network${qs ? `?${qs}` : ""}`,
      ),
    staleTime: 5 * 60_000,
  });
}
