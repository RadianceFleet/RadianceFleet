import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { CorridorUpdatePayload, CorridorCreatePayload } from '../types/api'

export function useUpdateCorridor(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CorridorUpdatePayload) =>
      apiFetch(`/corridors/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['corridor', id] })
      qc.invalidateQueries({ queryKey: ['corridors'] })
    },
  })
}

export function useCreateCorridor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CorridorCreatePayload) =>
      apiFetch<{ corridor_id: number }>('/corridors', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['corridors'] })
    },
  })
}

export function useDeleteCorridor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/corridors/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['corridors'] })
    },
  })
}
