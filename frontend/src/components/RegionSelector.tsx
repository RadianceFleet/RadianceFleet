import { useState, useEffect } from "react";
import { apiFetch } from "../lib/api";

interface Region {
  corridor_id: number;
  name: string;
}

interface Props {
  selectedId: number | null;
  onSelect: (corridorId: number | null) => void;
}

export function RegionSelector({ selectedId, onSelect }: Props) {
  const [regions, setRegions] = useState<Region[]>([]);

  useEffect(() => {
    apiFetch<Region[]>("/corridors/regions")
      .then(setRegions)
      .catch(() => setRegions([]));
  }, []);

  return (
    <div data-testid="region-selector" style={{ marginBottom: "1rem" }}>
      <label
        htmlFor="region-select"
        style={{ fontWeight: 600, marginRight: "0.5rem" }}
      >
        Region:
      </label>
      <select
        id="region-select"
        value={selectedId ?? ""}
        onChange={(e) => {
          const val = e.target.value;
          onSelect(val ? parseInt(val, 10) : null);
        }}
        style={{ padding: "0.25rem 0.5rem", borderRadius: 4, border: "1px solid #d1d5db" }}
      >
        <option value="">All regions</option>
        {regions.map((r) => (
          <option key={r.corridor_id} value={r.corridor_id}>
            {r.name}
          </option>
        ))}
      </select>
    </div>
  );
}
