import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";

export interface SourceDetection {
  detection_id: number;
  scene_id: string | null;
  latitude: number | null;
  longitude: number | null;
  detection_timestamp_utc: string | null;
  estimated_length_m: number | null;
  vessel_type_estimate: string | null;
  confidence: number | null;
  matched_vessel_id: number | null;
}

export function useViirsDetections(filters?: {
  date_from?: string;
  date_to?: string;
  min_confidence?: number;
  limit?: number;
}) {
  const params = buildQueryParams({
    source: "viirs",
    date_from: filters?.date_from,
    date_to: filters?.date_to,
    min_confidence: filters?.min_confidence,
    limit: filters?.limit ?? 200,
  });
  return useQuery({
    queryKey: ["dark-vessels-viirs", filters],
    queryFn: () => apiFetch<SourceDetection[]>(`/dark-vessels/by-source?${params}`),
    staleTime: 60_000,
  });
}
