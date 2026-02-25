import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { VesselSummary, VesselDetail, VesselHistoryEntry } from '../types/api'

export interface VesselSearchFilters {
  search?: string
  flag?: string
  vessel_type?: string
  limit?: number
}

export function useVesselSearch(filters: VesselSearchFilters) {
  const params = new URLSearchParams()
  if (filters.search) params.set('search', filters.search)
  if (filters.flag) params.set('flag', filters.flag)
  if (filters.vessel_type) params.set('vessel_type', filters.vessel_type)
  params.set('limit', String(filters.limit ?? 20))
  return useQuery({
    queryKey: ['vessels', filters],
    queryFn: () => apiFetch<VesselSummary[]>(`/vessels?${params}`),
    enabled: !!(filters.search || filters.flag || filters.vessel_type),
  })
}

export function useVesselDetail(id: string | undefined) {
  return useQuery({
    queryKey: ['vessel', id],
    queryFn: () => apiFetch<VesselDetail>(`/vessels/${id}`),
    enabled: !!id,
  })
}

export function useVesselHistory(id: string | undefined) {
  return useQuery({
    queryKey: ['vessel-history', id],
    queryFn: () => apiFetch<VesselHistoryEntry[]>(`/vessels/${id}/history`),
    enabled: !!id,
  })
}
