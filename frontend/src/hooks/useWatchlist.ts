import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export interface WatchlistEntry {
  id: number
  vessel_name: string | null
  mmsi: string | null
  imo: string | null
  source: string
  reason: string | null
  listed_date: string | null
  is_active: boolean
}

export function useWatchlist() {
  return useQuery({
    queryKey: ['watchlist'],
    queryFn: async () => {
      const resp = await apiFetch<{ items: WatchlistEntry[]; total: number }>('/watchlist')
      return resp.items
    },
  })
}

export function useImportWatchlist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ file, source }: { file: File; source: string }) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('source', source)
      const res = await fetch('/api/v1/watchlist/import', { method: 'POST', body: formData })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail ?? res.statusText)
      }
      return res.json() as Promise<{ imported: number }>
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })
}

export function useRemoveWatchlistEntry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiFetch(`/watchlist/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })
}
