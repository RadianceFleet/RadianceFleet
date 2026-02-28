import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { DarkVesselDetection } from '../types/api'

export function useDarkVessels(filters?: {
  ais_match_result?: string
  corridor_id?: number
  skip?: number
  limit?: number
}) {
  const params = new URLSearchParams()
  if (filters?.ais_match_result) params.set('ais_match_result', filters.ais_match_result)
  if (filters?.corridor_id != null) params.set('corridor_id', String(filters.corridor_id))
  if (filters?.skip != null) params.set('skip', String(filters.skip))
  if (filters?.limit != null) params.set('limit', String(filters.limit))
  return useQuery({
    queryKey: ['dark-vessels', filters],
    queryFn: async () => {
      const resp = await apiFetch<{ items: DarkVesselDetection[]; total: number }>(`/dark-vessels?${params}`)
      return resp
    },
  })
}
