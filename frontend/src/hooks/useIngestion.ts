import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export interface IngestionStatus {
  state: string
  accepted?: number
  rejected?: number
  errors?: string[]
  started_at?: string
  finished_at?: string
}

export function useIngestionStatus() {
  return useQuery({
    queryKey: ['ingestion-status'],
    queryFn: () => apiFetch<IngestionStatus>('/ingestion-status'),
    refetchInterval: 2000,
  })
}

export interface ImportResult {
  accepted: number
  rejected: number
  errors: string[]
}

export function useImportAIS() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/v1/ais/import', { method: 'POST', body: formData })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail ?? res.statusText)
      }
      return res.json() as Promise<ImportResult>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}
