import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

interface BulkOrderItem {
  vessel_id: number;
  provider_preference?: string;
  aoi_wkt?: string;
  priority_rank?: number;
}

export default function SatelliteBulkOrderPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState(5);
  const [budgetCap, setBudgetCap] = useState<string>("");
  const [items, setItems] = useState<BulkOrderItem[]>([
    { vessel_id: 0, provider_preference: "" },
  ]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async () => {
      const validItems = items.filter((i) => i.vessel_id > 0);
      if (validItems.length === 0) throw new Error("Add at least one valid vessel");
      const params = new URLSearchParams({ name, priority: String(priority) });
      if (budgetCap) params.set("budget_cap", budgetCap);
      return apiFetch<{ bulk_order_id: number; status: string }>(
        `/satellite/bulk-orders?${params}`,
        { method: "POST", body: JSON.stringify(validItems) }
      );
    },
    onSuccess: (data) => {
      setSuccess(`Bulk order #${data.bulk_order_id} created (${data.status})`);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["bulk-orders"] });
    },
    onError: (err: Error) => {
      setError(err.message);
      setSuccess(null);
    },
  });

  const addItem = () => {
    setItems([...items, { vessel_id: 0, provider_preference: "" }]);
  };

  const removeItem = (index: number) => {
    setItems(items.filter((_, i) => i !== index));
  };

  const updateItem = (index: number, field: keyof BulkOrderItem, value: string | number) => {
    const updated = [...items];
    updated[index] = { ...updated[index], [field]: value };
    setItems(updated);
  };

  return (
    <div style={{ padding: "1.5rem", maxWidth: 800 }}>
      <h2>Create Bulk Satellite Order</h2>

      {error && (
        <div role="alert" style={{ color: "red", marginBottom: "1rem" }}>
          {error}
        </div>
      )}
      {success && (
        <div role="status" style={{ color: "green", marginBottom: "1rem" }}>
          {success}
        </div>
      )}

      <div style={{ marginBottom: "1rem" }}>
        <label htmlFor="bulk-name">Order Name</label>
        <input
          id="bulk-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., Baltic Shadow Fleet Sweep"
          style={{ display: "block", width: "100%", marginTop: 4 }}
        />
      </div>

      <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
        <div>
          <label htmlFor="bulk-priority">Priority (1-10)</label>
          <input
            id="bulk-priority"
            type="number"
            min={1}
            max={10}
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
            style={{ display: "block", width: 80, marginTop: 4 }}
          />
        </div>
        <div>
          <label htmlFor="bulk-budget">Budget Cap (USD)</label>
          <input
            id="bulk-budget"
            type="number"
            min={0}
            value={budgetCap}
            onChange={(e) => setBudgetCap(e.target.value)}
            placeholder="Optional"
            style={{ display: "block", width: 150, marginTop: 4 }}
          />
        </div>
      </div>

      <h3>Items</h3>
      {items.map((item, idx) => (
        <div
          key={idx}
          style={{
            display: "flex",
            gap: "0.5rem",
            alignItems: "center",
            marginBottom: "0.5rem",
          }}
        >
          <input
            type="number"
            placeholder="Vessel ID"
            aria-label={`Vessel ID for item ${idx + 1}`}
            value={item.vessel_id || ""}
            onChange={(e) => updateItem(idx, "vessel_id", Number(e.target.value))}
            style={{ width: 100 }}
          />
          <select
            aria-label={`Provider for item ${idx + 1}`}
            value={item.provider_preference || ""}
            onChange={(e) => updateItem(idx, "provider_preference", e.target.value)}
          >
            <option value="">Any Provider</option>
            <option value="planet">Planet</option>
            <option value="capella">Capella</option>
            <option value="maxar">Maxar</option>
            <option value="umbra">Umbra</option>
          </select>
          <button type="button" onClick={() => removeItem(idx)} aria-label="Remove item">
            Remove
          </button>
        </div>
      ))}

      <button type="button" onClick={addItem} style={{ marginBottom: "1rem" }}>
        + Add Item
      </button>

      <div>
        <button
          type="button"
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending || !name.trim()}
        >
          {createMutation.isPending ? "Creating..." : "Create Bulk Order"}
        </button>
      </div>
    </div>
  );
}
