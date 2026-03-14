import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import type { NarrativeResponse } from "../types/api";

export function useNarrative(alertId: string | undefined, format: string = "md") {
  return useQuery({
    queryKey: ["narrative", alertId, format],
    queryFn: () =>
      apiFetch<NarrativeResponse>(`/alerts/${alertId}/narrative?format=${format}`),
    enabled: !!alertId,
    staleTime: 60_000,
  });
}
