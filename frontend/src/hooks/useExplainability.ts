import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface SignalExplanation {
  key: string;
  value: number;
  explanation: string;
  category: string;
  tier: number;
}

export interface WaterfallEntry {
  label: string;
  value: number;
  cumulative: number;
  is_multiplier: boolean;
}

export interface ExplainabilityResponse {
  alert_id: number;
  total_score: number;
  signals: SignalExplanation[];
  waterfall: WaterfallEntry[];
  categories: Record<string, SignalExplanation[]>;
  summary: string;
}

export function useExplainability(alertId: string | number | undefined) {
  return useQuery({
    queryKey: ["explainability", alertId],
    queryFn: () =>
      apiFetch<ExplainabilityResponse>(`/alerts/${alertId}/explain`),
    enabled: !!alertId,
    staleTime: 60_000,
  });
}
