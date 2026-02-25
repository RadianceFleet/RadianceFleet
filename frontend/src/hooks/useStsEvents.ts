import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export function useStsEvents(filters?: { vessel_id?: string }) {
  const params = new URLSearchParams()
  if (filters?.vessel_id) params.set('vessel_id', filters.vessel_id)
  return useQuery({
    queryKey: ['sts-events', filters],
    queryFn: () => apiFetch<Record<string, unknown>[]>(`/sts-events?${params}`),
  })
}
