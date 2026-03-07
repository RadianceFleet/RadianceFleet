import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildQueryParams } from '../utils/queryParams'

export interface CorridorActivityPoint {
  period_start: string
  gap_count: number
  distinct_vessels: number
  avg_risk_score: number
}

export function useCorridorActivity(
  corridorId: string | undefined,
  filters?: { date_from?: string; date_to?: string; granularity?: string }
) {
  const params = buildQueryParams({
    date_from: filters?.date_from,
    date_to: filters?.date_to,
    granularity: filters?.granularity ?? 'week',
  })
  return useQuery({
    queryKey: ['corridor-activity', corridorId, filters],
    queryFn: () => apiFetch<CorridorActivityPoint[]>(`/corridors/${corridorId}/activity?${params}`),
    enabled: !!corridorId,
  })
}
