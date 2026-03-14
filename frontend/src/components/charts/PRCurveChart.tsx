import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import type { SweepPoint } from "../../hooks/useValidation";

interface Props {
  data: SweepPoint[];
}

export function PRCurveChart({ data }: Props) {
  const points = data
    .filter((d) => d.recall != null && d.precision != null)
    .map((d) => ({ recall: d.recall!, precision: d.precision!, threshold: d.threshold }));

  if (points.length === 0) {
    return (
      <p style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No sweep data available.</p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis
          dataKey="recall"
          type="number"
          domain={[0, 1]}
          name="Recall"
          tick={{ fontSize: 11 }}
          stroke="var(--text-muted)"
          label={{ value: "Recall", position: "insideBottom", offset: -4, fontSize: 11 }}
        />
        <YAxis
          dataKey="precision"
          type="number"
          domain={[0, 1]}
          name="Precision"
          tick={{ fontSize: 11 }}
          stroke="var(--text-muted)"
          label={{ value: "Precision", angle: -90, position: "insideLeft", fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            fontSize: "0.75rem",
          }}
          formatter={(value) => (typeof value === "number" ? value.toFixed(3) : String(value))}
          labelFormatter={() => ""}
        />
        <Scatter
          data={points}
          fill="var(--accent)"
          line={{ stroke: "var(--accent)", strokeWidth: 1.5 }}
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
