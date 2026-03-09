import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildQueryParams } from '../utils/queryParams'
import type { VesselSummary, VesselDetail, VesselHistoryEntry, VesselAlias, TimelineEvent, MergeCandidateSummary, MergeChainItem } from '../types/api'

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
  const params = buildQueryParams({
    search: filters.search,
    flag: filters.flag,
    vessel_type: filters.vessel_type,
    min_dwt: filters.min_dwt,
    max_dwt: filters.max_dwt,
    min_year_built: filters.min_year_built,
    watchlist_only: filters.watchlist_only || undefined,
    limit: filters.limit ?? 20,
  })
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
  const params = buildQueryParams({ status })
  return useQuery({
    queryKey: ['merge-candidates', status],
    queryFn: () => apiFetch<{ items: MergeCandidateSummary[]; total: number }>(`/merge-candidates?${params}`),
  })
}

export function useMergeChains(params?: { min_confidence?: number; confidence_band?: string }) {
  const qp = buildQueryParams(params ?? {})
  return useQuery({
    queryKey: ['merge-chains', params],
    queryFn: () => apiFetch<{ items: MergeChainItem[]; total: number }>(`/merge-chains?${qp}`),
  })
}

export function useVesselTrack(vesselId: string | number | undefined) {
  return useQuery({
    queryKey: ['vessel-track', vesselId],
    queryFn: () => apiFetch<any>(`/vessels/${vesselId}/track.geojson`),
    enabled: !!vesselId,
    staleTime: 60_000,
  })
}
