import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

export interface ChecklistItem {
  item_id: number;
  item_key: string;
  label: string;
  is_checked: boolean;
  checked_by: number | null;
  checked_at: string | null;
  notes: string | null;
  sort_order: number;
}

export interface Checklist {
  checklist_id: number;
  alert_id: number;
  checklist_template: string;
  created_by: number;
  created_at: string | null;
  completed_at: string | null;
  completed_by: number | null;
  items: ChecklistItem[];
}

export function useChecklist(alertId: string | undefined) {
  return useQuery({
    queryKey: ["checklist", alertId],
    queryFn: () => apiFetch<Checklist>(`/alerts/${alertId}/checklist`),
    enabled: !!alertId,
    retry: false,
  });
}

export function useCreateChecklist(alertId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (template?: string) =>
      apiFetch<Checklist>(`/alerts/${alertId}/checklist`, {
        method: "POST",
        body: JSON.stringify(template ? { template } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["checklist", alertId] });
    },
  });
}

export function useToggleChecklistItem(alertId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      isChecked,
      notes,
    }: {
      itemId: number;
      isChecked: boolean;
      notes?: string;
    }) =>
      apiFetch<ChecklistItem>(
        `/alerts/${alertId}/checklist/items/${itemId}`,
        {
          method: "PATCH",
          body: JSON.stringify({ is_checked: isChecked, notes }),
        }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["checklist", alertId] });
    },
  });
}
