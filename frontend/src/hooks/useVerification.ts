import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

// Types defined inline — NOT in types/api.ts (owned by Agent 4)
interface VerificationBudget {
  provider: string
  spent_usd: number
  budget_usd: number
  remaining_usd: number
  calls_this_month: number
}

interface VerificationResult {
  provider: string
  verified: boolean
  data: Record<string, unknown>
  cost_usd: number
}

export function useVerificationBudget() {
  return useQuery({
    queryKey: ['verification-budget'],
    queryFn: () => apiFetch<VerificationBudget>('/verification/budget'),
  })
}

export function useVerifyVessel(vesselId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (provider: string) =>
      apiFetch<VerificationResult>(`/vessels/${vesselId}/verify`, {
        method: 'POST',
        body: JSON.stringify({ provider }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vessel', vesselId] })
      qc.invalidateQueries({ queryKey: ['verification-budget'] })
    },
  })
}

export function useUpdateOwner(vesselId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { owner_name?: string; verified_by?: string; source_url?: string; verification_notes?: string }) =>
      apiFetch(`/vessels/${vesselId}/owner`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vessel', vesselId] })
    },
  })
}
