import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";
import type { SourceDetection } from "./useViirsDetections";

export type { SourceDetection };

export function useSarDetections(filters?: {
  date_from?: string;
  date_to?: string;
  min_confidence?: number;
  limit?: number;
}) {
  const params = buildQueryParams({
    source: "sar",
    date_from: filters?.date_from,
    date_to: filters?.date_to,
    min_confidence: filters?.min_confidence,
    limit: filters?.limit ?? 200,
  });
  return useQuery({
    queryKey: ["dark-vessels-sar", filters],
    queryFn: () => apiFetch<SourceDetection[]>(`/dark-vessels/by-source?${params}`),
    staleTime: 60_000,
  });
}
