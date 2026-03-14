import { useState } from "react";
import type { NetworkFilters } from "../hooks/useOwnershipNetwork";

interface NetworkGraphFiltersProps {
  filters: NetworkFilters;
  onFiltersChange: (filters: NetworkFilters) => void;
  jurisdictions?: string[];
}

export function NetworkGraphFilters({
  filters,
  onFiltersChange,
  jurisdictions = [],
}: NetworkGraphFiltersProps) {
  const [localDepth, setLocalDepth] = useState(filters.depth ?? 3);

  const handleDepthChange = (value: number) => {
    setLocalDepth(value);
    onFiltersChange({ ...filters, depth: value });
  };

  return (
    <div
      data-testid="network-filters"
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 16,
        alignItems: "center",
        padding: "12px 0",
      }}
    >
      {/* Sanctioned toggle */}
      <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={filters.sanctioned_only ?? false}
          onChange={(e) =>
            onFiltersChange({ ...filters, sanctioned_only: e.target.checked })
          }
          data-testid="filter-sanctioned"
        />
        <span>Sanctioned only</span>
      </label>

      {/* SPV toggle */}
      <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={filters.spv_only ?? false}
          onChange={(e) =>
            onFiltersChange({ ...filters, spv_only: e.target.checked })
          }
          data-testid="filter-spv"
        />
        <span>SPV only</span>
      </label>

      {/* Jurisdiction dropdown */}
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>Jurisdiction:</span>
        <select
          value={filters.jurisdiction ?? ""}
          onChange={(e) =>
            onFiltersChange({
              ...filters,
              jurisdiction: e.target.value || null,
            })
          }
          data-testid="filter-jurisdiction"
          style={{
            background: "var(--bg-input, #0f172a)",
            color: "var(--text-body, #e2e8f0)",
            border: "1px solid var(--border-dim, #334155)",
            borderRadius: 4,
            padding: "4px 8px",
          }}
        >
          <option value="">All</option>
          {jurisdictions.map((j) => (
            <option key={j} value={j}>
              {j}
            </option>
          ))}
        </select>
      </label>

      {/* Depth slider */}
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>Depth: {localDepth}</span>
        <input
          type="range"
          min={1}
          max={10}
          value={localDepth}
          onChange={(e) => handleDepthChange(Number(e.target.value))}
          data-testid="filter-depth"
          style={{ width: 100 }}
        />
      </label>
    </div>
  );
}
