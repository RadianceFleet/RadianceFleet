import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { useAuth } from './useAuth'

export interface Tip {
  id: number
  mmsi: string
  imo: string | null
  behavior_type: string
  detail_text: string
  source_url: string | null
  submitter_email: string | null
  status: string
  created_at: string
  analyst_note: string | null
}

export function useTips(filters?: { status?: string; limit?: number; offset?: number }) {
  const { getAuthHeaders } = useAuth()
  const params = new URLSearchParams()
  if (filters?.status) params.set('status', filters.status)
  if (filters?.limit) params.set('limit', String(filters.limit))
  if (filters?.offset) params.set('offset', String(filters.offset))

  return useQuery({
    queryKey: ['admin-tips', filters],
    queryFn: () => apiFetch<Tip[]>(`/admin/tips?${params}`, { headers: getAuthHeaders() }),
  })
}

export function useUpdateTip() {
  const { getAuthHeaders } = useAuth()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ tipId, status, analyst_note }: { tipId: number; status?: string; analyst_note?: string }) =>
      apiFetch(`/admin/tips/${tipId}`, {
        method: 'PATCH',
        body: JSON.stringify({ status, analyst_note }),
        headers: getAuthHeaders(),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tips'] }),
  })
}
