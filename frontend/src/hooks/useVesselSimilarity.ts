import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface SimilarVessel {
  source_vessel_id: number;
  target_vessel_id: number;
  fingerprint_distance: number;
  fingerprint_similarity: number;
  fingerprint_band: string;
  ownership_similarity_score: number;
  ownership_breakdown: Record<string, boolean>;
  composite_similarity_score: number;
  similarity_tier: string;
}

interface SimilarVesselsResponse {
  vessel_id: number;
  similar_vessels: SimilarVessel[];
  total: number;
}

export function useVesselSimilarity(vesselId: string | undefined, limit = 20) {
  return useQuery({
    queryKey: ["vesselSimilarity", vesselId, limit],
    queryFn: () =>
      apiFetch<SimilarVesselsResponse>(
        `/vessels/${vesselId}/similar?limit=${limit}`
      ),
    enabled: !!vesselId,
    staleTime: 120_000,
  });
}
