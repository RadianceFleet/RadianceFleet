import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { AlertSummary, AlertDetail, AlertMapPoint } from '../types/api'

export interface AlertFilters {
  min_score?: string
  status?: string
  vessel_name?: string
  date_from?: string
  date_to?: string
  corridor_id?: string
  vessel_id?: string
  sort_by?: string
  sort_order?: string
  skip?: number
  limit?: number
}

export interface AlertListResponse {
  items: AlertSummary[]
  total: number
}

export function useAlerts(filters: AlertFilters) {
  const params = new URLSearchParams()
  if (filters.min_score) params.set('min_score', filters.min_score)
  if (filters.status) params.set('status', filters.status)
  if (filters.vessel_name) params.set('vessel_name', filters.vessel_name)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.corridor_id) params.set('corridor_id', filters.corridor_id)
  if (filters.vessel_id) params.set('vessel_id', filters.vessel_id)
  if (filters.sort_by) params.set('sort_by', filters.sort_by)
  if (filters.sort_order) params.set('sort_order', filters.sort_order)
  params.set('skip', String(filters.skip ?? 0))
  params.set('limit', String(filters.limit ?? 50))
  return useQuery({
    queryKey: ['alerts', filters],
    queryFn: () => apiFetch<AlertListResponse>(`/alerts?${params}`),
  })
}

export function useAlert(id: string | undefined) {
  return useQuery({
    queryKey: ['alert', id],
    queryFn: () => apiFetch<AlertDetail>(`/alerts/${id}`),
    enabled: !!id,
  })
}

export function useUpdateAlertStatus(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { status: string; reason?: string }) =>
      apiFetch(`/alerts/${id}/status`, { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert', id] })
      qc.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
}

export function useAlertMapPoints() {
  return useQuery({
    queryKey: ['alerts-map'],
    queryFn: () => apiFetch<{ points: AlertMapPoint[] }>('/alerts/map'),
  })
}

export function useUpdateAlertNotes(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (notes: string) =>
      apiFetch(`/alerts/${id}/notes`, { method: 'POST', body: JSON.stringify({ notes }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert', id] }),
  })
}

export function useBulkUpdateAlertStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { alert_ids: number[]; status: string }) =>
      apiFetch<{ updated: number }>('/alerts/bulk-status', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
}
