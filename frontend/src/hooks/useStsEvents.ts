import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";
import type { StsEventSummary } from "../types/api";

export function useStsEvents(filters?: { vessel_id?: string; skip?: number; limit?: number }) {
  const params = buildQueryParams({
    vessel_id: filters?.vessel_id,
    skip: filters?.skip,
    limit: filters?.limit,
  });
  return useQuery({
    queryKey: ["sts-events", filters],
    queryFn: async () => {
      const resp = await apiFetch<{ items: StsEventSummary[]; total: number }>(
        `/sts-events?${params}`
      );
      return resp;
    },
  });
}
