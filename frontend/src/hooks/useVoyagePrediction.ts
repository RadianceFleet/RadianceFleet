import { useQuery } from "@tanstack/react-query";
import { apiFetch, ApiError } from "../lib/api";

export interface RoutePoint {
  lat: number;
  lon: number;
  timestamp_utc?: string | null;
}

export interface VoyagePrediction {
  vessel_id: number;
  predicted_route: RoutePoint[];
  actual_route: RoutePoint[];
  template_name: string | null;
  similarity_score: number | null;
  deviation_score: number | null;
  predicted_destination: string | null;
}

export function useVoyagePrediction(vesselId: string | undefined) {
  return useQuery({
    queryKey: ["voyage-prediction", vesselId],
    queryFn: async () => {
      try {
        return await apiFetch<VoyagePrediction>(`/vessels/${vesselId}/voyage-prediction`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled: !!vesselId,
    retry: false,
  });
}
