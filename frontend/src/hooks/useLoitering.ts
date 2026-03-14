import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";

export interface GlobalLoiteringEvent {
  loiter_id: number;
  vessel_id: number;
  mean_lat: number | null;
  mean_lon: number | null;
  duration_hours: number;
  corridor_id: number | null;
  start_time_utc: string | null;
  median_sog_kn: number | null;
}

export function useGlobalLoitering(filters?: {
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
    queryKey: ["global-loitering", filters],
    queryFn: async () => {
      return apiFetch<{ items: GlobalLoiteringEvent[]; total: number }>(`/loitering?${params}`);
    },
  });
}
