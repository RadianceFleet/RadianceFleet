import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export function useCorridors() {
  return useQuery({
    queryKey: ['corridors'],
    queryFn: () => apiFetch<Record<string, unknown>[]>('/corridors'),
  })
}

export function useCorridorDetail(id: string | undefined) {
  return useQuery({
    queryKey: ['corridor', id],
    queryFn: () => apiFetch<Record<string, unknown>>(`/corridors/${id}`),
    enabled: !!id,
  })
}
