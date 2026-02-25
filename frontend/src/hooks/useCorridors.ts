import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { CorridorSummary, CorridorDetail } from '../types/api'

export function useCorridors() {
  return useQuery({
    queryKey: ['corridors'],
    queryFn: () => apiFetch<CorridorSummary[]>('/corridors'),
  })
}

export function useCorridorDetail(id: string | undefined) {
  return useQuery({
    queryKey: ['corridor', id],
    queryFn: () => apiFetch<CorridorDetail>(`/corridors/${id}`),
    enabled: !!id,
  })
}
