import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { useAuth } from "../hooks/useAuth";

interface SavedFilter {
  filter_id: number;
  name: string;
  filter_json: Record<string, string>;
  is_default: boolean;
}

interface Props {
  currentFilters: Record<string, string>;
  onApplyFilter: (filters: Record<string, string>) => void;
}

export function SavedFilterBar({ currentFilters, onApplyFilter }: Props) {
  const queryClient = useQueryClient();
  const { isAuthenticated } = useAuth();
  const [saveName, setSaveName] = useState("");
  const [showSave, setShowSave] = useState(false);

  const { data } = useQuery({
    queryKey: ["saved-filters"],
    queryFn: () => apiFetch<{ items: SavedFilter[] }>("/alerts/saved-filters"),
    enabled: isAuthenticated,
  });

  const saveMutation = useMutation({
    mutationFn: (body: { name: string; filter_json: Record<string, string> }) =>
      apiFetch("/alerts/saved-filters", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saved-filters"] });
      setSaveName("");
      setShowSave(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/alerts/saved-filters/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-filters"] }),
  });

  const filters = data?.items ?? [];

  // Apply default filter on mount
  useEffect(() => {
    const defaultFilter = filters.find((f) => f.is_default);
    if (defaultFilter) onApplyFilter(defaultFilter.filter_json);
  }, [filters.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!isAuthenticated) return null;

  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        alignItems: "center",
        marginBottom: 12,
        flexWrap: "wrap",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--text-muted)", fontSize: 12 }}>Saved:</span>
      {filters.map((f) => (
        <div key={f.filter_id} style={{ display: "flex", gap: 2, alignItems: "center" }}>
          <button
            onClick={() => onApplyFilter(f.filter_json)}
            style={{
              background: "var(--bg-base)",
              color: "var(--accent)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "3px 10px",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            {f.name}
            {f.is_default ? " *" : ""}
          </button>
          <button
            onClick={() => deleteMutation.mutate(f.filter_id)}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-dim)",
              cursor: "pointer",
              fontSize: 11,
            }}
          >
            &times;
          </button>
        </div>
      ))}
      {showSave ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            saveMutation.mutate({ name: saveName, filter_json: currentFilters });
          }}
          style={{ display: "flex", gap: 4 }}
        >
          <input
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Filter name"
            style={{
              fontSize: 12,
              padding: "3px 6px",
              background: "var(--bg-base)",
              color: "var(--text-bright)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
            }}
          />
          <button
            type="submit"
            style={{
              fontSize: 12,
              padding: "3px 8px",
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--radius)",
              cursor: "pointer",
            }}
          >
            Save
          </button>
        </form>
      ) : (
        <button
          onClick={() => setShowSave(true)}
          style={{
            fontSize: 12,
            color: "var(--text-muted)",
            background: "none",
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius)",
            padding: "3px 10px",
            cursor: "pointer",
          }}
        >
          + Save current
        </button>
      )}
    </div>
  );
}
