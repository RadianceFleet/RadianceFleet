import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { SpoofingAnomaly, AlertSummary, StsEventSummary, VesselHistoryEntry, PortCallItem } from '../types/api'

export function useVesselDetectors(vesselId: string | undefined) {
  const enabled = !!vesselId

  const spoofing = useQuery({
    queryKey: ['vessel-spoofing', vesselId],
    queryFn: () => apiFetch<{ items: SpoofingAnomaly[]; total: number }>(`/spoofing/${vesselId}`).catch(() => null),
    enabled,
    retry: false,
  })

  const gaps = useQuery({
    queryKey: ['vessel-gaps-detector', vesselId],
    queryFn: () => apiFetch<{ items: AlertSummary[]; total: number }>(`/alerts?vessel_id=${vesselId}&limit=20&sort_by=risk_score&sort_order=desc`),
    enabled,
    retry: false,
  })

  const stsEvents = useQuery({
    queryKey: ['vessel-sts-detector', vesselId],
    queryFn: () => apiFetch<{ items: StsEventSummary[]; total: number }>(`/sts-events?vessel_id=${vesselId}&limit=20`),
    enabled,
    retry: false,
  })

  const flagHistory = useQuery({
    queryKey: ['vessel-flag-history', vesselId],
    queryFn: () => apiFetch<VesselHistoryEntry[]>(`/vessels/${vesselId}/history`),
    enabled,
    retry: false,
  })

  const portCalls = useQuery({
    queryKey: ['vessel-port-calls', vesselId],
    queryFn: () => apiFetch<{ items: PortCallItem[]; total: number }>(`/port-calls/${vesselId}`).catch(() => null),
    enabled,
    retry: false,
  })

  return { spoofing, gaps, stsEvents, flagHistory, portCalls }
}
