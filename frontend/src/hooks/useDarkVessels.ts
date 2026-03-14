import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";
import type { DarkVesselDetection } from "../types/api";

export function useDarkVessels(filters?: {
  ais_match_result?: string;
  corridor_id?: number;
  skip?: number;
  limit?: number;
}) {
  const params = buildQueryParams({
    ais_match_result: filters?.ais_match_result,
    corridor_id: filters?.corridor_id,
    skip: filters?.skip,
    limit: filters?.limit,
  });
  return useQuery({
    queryKey: ["dark-vessels", filters],
    queryFn: async () => {
      const resp = await apiFetch<{ items: DarkVesselDetection[]; total: number }>(
        `/dark-vessels?${params}`
      );
      return resp;
    },
  });
}
