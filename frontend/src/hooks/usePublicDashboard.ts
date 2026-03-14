import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PublicDashboardData {
  vessel_count: number;
  alert_counts: { high: number; medium: number; low: number };
  detection_coverage: { monitored_zones: number; active_corridors: number };
  recent_alerts: RecentAlert[];
  trend_buckets: TrendBucket[];
  detections_by_type: { gap: number; spoofing: number; sts: number };
}

export interface RecentAlert {
  mmsi_suffix: string;
  flag: string;
  tier: "high" | "medium" | "low";
  created_at: string | null;
}

export interface TrendBucket {
  date: string;
  count: number;
}

export interface TrendDay {
  date: string;
  count: number;
}

export interface PublicTrendsData {
  days: TrendDay[];
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function usePublicDashboard() {
  return useQuery({
    queryKey: ["public-dashboard"],
    queryFn: () => apiFetch<PublicDashboardData>("/public/dashboard"),
    refetchInterval: 300_000, // 5 minutes
    staleTime: 300_000,
  });
}

export function usePublicTrends() {
  return useQuery({
    queryKey: ["public-trends"],
    queryFn: () => apiFetch<PublicTrendsData>("/public/trends"),
    refetchInterval: 900_000, // 15 minutes
    staleTime: 900_000,
  });
}
