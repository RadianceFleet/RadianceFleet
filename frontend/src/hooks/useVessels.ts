import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { VesselSummary, VesselDetail, VesselHistoryEntry, VesselAlias, TimelineEvent, MergeCandidateSummary } from '../types/api'

export interface VesselSearchFilters {
  search?: string
  flag?: string
  vessel_type?: string
  min_dwt?: string
  max_dwt?: string
  min_year_built?: string
  watchlist_only?: boolean
  limit?: number
}

export function useVesselSearch(filters: VesselSearchFilters) {
  const params = new URLSearchParams()
  if (filters.search) params.set('search', filters.search)
  if (filters.flag) params.set('flag', filters.flag)
  if (filters.vessel_type) params.set('vessel_type', filters.vessel_type)
  if (filters.min_dwt) params.set('min_dwt', filters.min_dwt)
  if (filters.max_dwt) params.set('max_dwt', filters.max_dwt)
  if (filters.min_year_built) params.set('min_year_built', filters.min_year_built)
  if (filters.watchlist_only) params.set('watchlist_only', 'true')
  params.set('limit', String(filters.limit ?? 20))
  return useQuery({
    queryKey: ['vessels', filters],
    queryFn: () => apiFetch<{ items: VesselSummary[]; total: number }>(`/vessels?${params}`),
    enabled: !!(filters.search || filters.flag || filters.vessel_type || filters.min_dwt || filters.max_dwt || filters.min_year_built || filters.watchlist_only),
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

export function useVesselAliases(id: string | undefined) {
  return useQuery({
    queryKey: ['vessel-aliases', id],
    queryFn: () => apiFetch<{ vessel_id: number; aliases: VesselAlias[] }>(`/vessels/${id}/aliases`),
    enabled: !!id,
  })
}

export function useVesselTimeline(id: string | undefined) {
  return useQuery({
    queryKey: ['vessel-timeline', id],
    queryFn: () => apiFetch<{ vessel_id: number; events: TimelineEvent[]; count: number }>(`/vessels/${id}/timeline`),
    enabled: !!id,
  })
}

export function useMergeCandidates(status?: string) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  return useQuery({
    queryKey: ['merge-candidates', status],
    queryFn: () => apiFetch<{ items: MergeCandidateSummary[]; total: number }>(`/merge-candidates?${params}`),
  })
}
