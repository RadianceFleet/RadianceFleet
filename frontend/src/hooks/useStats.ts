import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export interface DashboardStats {
  alert_counts: {
    total: number
    critical: number
    high: number
    medium: number
    low: number
  }
  by_status: Record<string, number>
  by_corridor: Record<string, number>
  vessels_with_multiple_gaps_7d: number
  distinct_vessels: number
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<DashboardStats>('/stats'),
  })
}
