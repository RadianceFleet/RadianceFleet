import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface JammingZoneFeature {
  type: "Feature";
  geometry: object | null;
  properties: {
    zone_id: number;
    status: string;
    confidence: number;
    vessel_count: number;
    gap_count: number;
    radius_nm: number;
    first_detected_at: string | null;
    last_gap_at: string | null;
  };
}

export interface JammingZonesGeoJSON {
  type: "FeatureCollection";
  features: JammingZoneFeature[];
}

export function useJammingZonesGeoJSON(status?: string) {
  const params = status ? `?status=${status}` : "";
  return useQuery({
    queryKey: ["jamming-zones-geojson", status],
    queryFn: () => apiFetch<JammingZonesGeoJSON>(`/detect/jamming-zones/geojson${params}`),
    staleTime: 5 * 60_000,
  });
}
