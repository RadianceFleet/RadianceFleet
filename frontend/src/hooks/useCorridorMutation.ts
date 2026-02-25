import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { CorridorUpdatePayload } from '../types/api'

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
