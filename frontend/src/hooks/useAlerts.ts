import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { buildQueryParams } from "../utils/queryParams";
import type { AlertSummary, AlertDetail, AlertMapPoint } from "../types/api";

export interface AlertFilters {
  min_score?: string;
  status?: string;
  vessel_name?: string;
  date_from?: string;
  date_to?: string;
  corridor_id?: string;
  vessel_id?: string;
  sort_by?: string;
  sort_order?: string;
  skip?: number;
  limit?: number;
}

export interface AlertListResponse {
  items: AlertSummary[];
  total: number;
}

export function useAlerts(filters: AlertFilters) {
  const params = buildQueryParams({
    min_score: filters.min_score,
    status: filters.status,
    vessel_name: filters.vessel_name,
    date_from: filters.date_from,
    date_to: filters.date_to,
    corridor_id: filters.corridor_id,
    vessel_id: filters.vessel_id,
    sort_by: filters.sort_by,
    sort_order: filters.sort_order,
    skip: filters.skip ?? 0,
    limit: filters.limit ?? 50,
  });
  return useQuery({
    queryKey: ["alerts", filters],
    queryFn: () => apiFetch<AlertListResponse>(`/alerts?${params}`),
  });
}

export function useAlert(id: string | undefined) {
  return useQuery({
    queryKey: ["alert", id],
    queryFn: () => apiFetch<AlertDetail>(`/alerts/${id}`),
    enabled: !!id,
  });
}

export function useUpdateAlertStatus(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { status: string; reason?: string }) =>
      apiFetch(`/alerts/${id}/status`, { method: "POST", body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alert", id] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useAlertMapPoints() {
  return useQuery({
    queryKey: ["alerts-map"],
    queryFn: () => apiFetch<{ points: AlertMapPoint[] }>("/alerts/map"),
  });
}

export function useUpdateAlertNotes(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (notes: string) =>
      apiFetch(`/alerts/${id}/notes`, { method: "POST", body: JSON.stringify({ notes }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert", id] }),
  });
}

export function useSubmitAlertVerdict(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { verdict: string; reason?: string; reviewed_by?: string }) =>
      apiFetch(`/alerts/${id}/verdict`, { method: "POST", body: JSON.stringify(data) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alert", id] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useBulkUpdateAlertStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { alert_ids: number[]; status: string }) =>
      apiFetch<{ updated: number }>("/alerts/bulk-status", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}
