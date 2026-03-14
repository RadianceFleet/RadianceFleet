import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";

export interface GlobalSpoofingEvent {
  anomaly_id: number;
  vessel_id: number;
  anomaly_type: string;
  start_time_utc: string | null;
  risk_score_component: number;
}

export function useGlobalSpoofing(filters?: {
  date_from?: string;
  date_to?: string;
  anomaly_type?: string;
  skip?: number;
  limit?: number;
}) {
  const params = buildQueryParams({
    date_from: filters?.date_from,
    date_to: filters?.date_to,
    anomaly_type: filters?.anomaly_type,
    skip: filters?.skip ?? 0,
    limit: filters?.limit ?? 50,
  });
  return useQuery({
    queryKey: ["global-spoofing", filters],
    queryFn: () => apiFetch<{ items: GlobalSpoofingEvent[]; total: number }>(`/spoofing?${params}`),
  });
}
