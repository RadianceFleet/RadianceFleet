import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";

export interface StsChain {
  alert_id: number;
  chain_vessel_ids: number[];
  vessel_names: Record<number, string | null>;
  intermediary_vessel_ids: number[];
  hops: unknown[];
  chain_length: number;
  risk_score_component: number;
  created_utc: string | null;
}

export function useStsChains(filters?: {
  date_from?: string;
  date_to?: string;
  skip?: number;
  limit?: number;
}) {
  const params = buildQueryParams({
    date_from: filters?.date_from,
    date_to: filters?.date_to,
    skip: filters?.skip,
    limit: filters?.limit,
  });
  return useQuery({
    queryKey: ["sts-chains", filters],
    queryFn: async () => {
      return apiFetch<{ items: StsChain[]; total: number }>(`/sts-chains?${params}`);
    },
  });
}
