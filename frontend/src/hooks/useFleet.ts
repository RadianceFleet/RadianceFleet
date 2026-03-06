import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export interface FleetAlert {
  alert_id: number
  owner_cluster_id: number | null
  alert_type: string
  vessel_ids: number[] | null
  evidence: Record<string, unknown> | null
  risk_score_component: number | null
  created_utc: string | null
}

export interface OwnerCluster {
  cluster_id: number
  canonical_name: string
  country: string | null
  is_sanctioned: boolean
  vessel_count: number
}

export interface OwnerClusterDetail extends OwnerCluster {
  members: {
    member_id: number
    owner_id: number
    owner_name: string | null
    similarity_score: number | null
  }[]
}

export function useFleetAlerts(limit = 50) {
  return useQuery({
    queryKey: ['fleet-alerts', limit],
    queryFn: () => apiFetch<{ alerts: FleetAlert[]; total: number }>(`/fleet/alerts?limit=${limit}`),
    retry: false,
  })
}

export function useFleetClusters(limit = 50) {
  return useQuery({
    queryKey: ['fleet-clusters', limit],
    queryFn: () => apiFetch<{ items: OwnerCluster[]; total: number }>(`/fleet/clusters?limit=${limit}`),
    retry: false,
  })
}

export function useFleetClusterDetail(clusterId: number | undefined) {
  return useQuery({
    queryKey: ['fleet-cluster', clusterId],
    queryFn: () => apiFetch<OwnerClusterDetail>(`/fleet/clusters/${clusterId}`),
    enabled: clusterId != null,
    retry: false,
  })
}
