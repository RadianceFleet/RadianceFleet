import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface ExportSubscription {
  subscription_id: number;
  name: string;
  created_by: number;
  schedule: string;
  schedule_day: number | null;
  schedule_hour_utc: number;
  export_type: string;
  filter_json: Record<string, unknown> | null;
  columns_json: string[] | null;
  format: string;
  delivery_method: string;
  delivery_config_json: Record<string, unknown> | null;
  is_active: boolean;
  last_run_at: string | null;
  last_run_status: string | null;
  last_run_rows: number | null;
  created_at: string | null;
}

export interface ExportRun {
  run_id: number;
  subscription_id: number;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  row_count: number | null;
  file_size_bytes: number | null;
  delivery_status: string | null;
  error_message: string | null;
  created_at: string | null;
}

export interface ExportSubscriptionCreate {
  name: string;
  schedule: string;
  schedule_day?: number | null;
  schedule_hour_utc?: number;
  export_type: string;
  filter_json?: Record<string, unknown> | null;
  columns_json?: string[] | null;
  format: string;
  delivery_method: string;
  delivery_config_json?: Record<string, unknown> | null;
}

export function useExportSubscriptions(skip = 0, limit = 50) {
  return useQuery({
    queryKey: ["export-subscriptions", skip, limit],
    queryFn: () =>
      apiFetch<{ total: number; subscriptions: ExportSubscription[] }>(
        `/admin/export-subscriptions?skip=${skip}&limit=${limit}`
      ),
    staleTime: 30_000,
  });
}

export function useExportSubscription(id: number) {
  return useQuery({
    queryKey: ["export-subscription", id],
    queryFn: () =>
      apiFetch<ExportSubscription>(`/admin/export-subscriptions/${id}`),
    enabled: id > 0,
  });
}

export function useExportRuns(subscriptionId: number, skip = 0, limit = 20) {
  return useQuery({
    queryKey: ["export-runs", subscriptionId, skip, limit],
    queryFn: () =>
      apiFetch<{ total: number; runs: ExportRun[] }>(
        `/admin/export-subscriptions/${subscriptionId}/runs?skip=${skip}&limit=${limit}`
      ),
    enabled: subscriptionId > 0,
  });
}

export function useCreateExportSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ExportSubscriptionCreate) =>
      apiFetch<ExportSubscription>("/admin/export-subscriptions", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["export-subscriptions"] }),
  });
}

export function useUpdateExportSubscription(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ExportSubscriptionCreate> & { is_active?: boolean }) =>
      apiFetch<ExportSubscription>(`/admin/export-subscriptions/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["export-subscriptions"] });
      qc.invalidateQueries({ queryKey: ["export-subscription", id] });
    },
  });
}

export function useDeleteExportSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiFetch<{ status: string }>(`/admin/export-subscriptions/${id}`, {
        method: "DELETE",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["export-subscriptions"] }),
  });
}

export function useTriggerExportRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (subscriptionId: number) =>
      apiFetch<ExportRun>(`/admin/export-subscriptions/${subscriptionId}/run`, {
        method: "POST",
      }),
    onSuccess: (_data, subscriptionId) => {
      qc.invalidateQueries({ queryKey: ["export-subscriptions"] });
      qc.invalidateQueries({ queryKey: ["export-runs", subscriptionId] });
    },
  });
}
