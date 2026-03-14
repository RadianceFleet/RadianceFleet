import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface RouteLaunderingItem {
  anomaly_id: number;
  vessel_id: number;
  gap_event_id: number | null;
  anomaly_type: string;
  start_time_utc: string;
  end_time_utc: string | null;
  evidence_json: Record<string, unknown> | null;
  implied_speed_kn: number | null;
  plausibility_score: number | null;
  risk_score_component: number;
}

interface RouteLaunderingResponse {
  items: RouteLaunderingItem[];
  total: number;
}

export function useRouteLaundering(vesselId: string | number | undefined) {
  return useQuery({
    queryKey: ["route-laundering", vesselId],
    queryFn: () => apiFetch<RouteLaunderingResponse>(`/route-laundering/${vesselId}`),
    enabled: !!vesselId,
  });
}
