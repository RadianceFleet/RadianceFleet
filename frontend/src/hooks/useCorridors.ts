import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildQueryParams } from '../utils/queryParams'
import type { CorridorSummary, CorridorDetail } from '../types/api'

export function useCorridors(filters?: { skip?: number; limit?: number }) {
  const params = buildQueryParams({
    skip: filters?.skip,
    limit: filters?.limit,
  })
  return useQuery({
    queryKey: ['corridors', filters],
    queryFn: () => apiFetch<{ items: CorridorSummary[]; total: number }>(`/corridors?${params}`),
  })
}

export function useCorridorDetail(id: string | undefined) {
  return useQuery({
    queryKey: ['corridor', id],
    queryFn: () => apiFetch<CorridorDetail>(`/corridors/${id}`),
    enabled: !!id,
  })
}

export function useCorridorGeoJSON(enabled: boolean = true) {
  return useQuery({
    queryKey: ['corridors', 'geojson'],
    queryFn: () => apiFetch<GeoJSON.FeatureCollection>('/corridors/geojson'),
    enabled,
  })
}
