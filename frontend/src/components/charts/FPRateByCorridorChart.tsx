import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from "recharts";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface CorridorFPRate {
  corridor_id: number;
  corridor_name: string;
  total_alerts: number;
  false_positives: number;
  fp_rate: number;
  fp_rate_30d: number;
  fp_rate_90d: number;
  trend: string;
}

interface Props {
  data: CorridorFPRate[];
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function barColor(rate: number): string {
  if (rate > 0.3) return "#ef4444";
  if (rate > 0.15) return "#f59e0b";
  if (rate < 0.05) return "#22c55e";
  return "#6b7280";
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/* ------------------------------------------------------------------ */
/* Custom Tooltip                                                      */
/* ------------------------------------------------------------------ */

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: CorridorFPRate }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-white border rounded shadow-lg p-3 text-sm">
      <p className="font-semibold">{d.corridor_name}</p>
      <p>
        FP Rate: <strong>{formatPercent(d.fp_rate)}</strong>
      </p>
      <p>30-day: {formatPercent(d.fp_rate_30d)}</p>
      <p>90-day: {formatPercent(d.fp_rate_90d)}</p>
      <p>
        Alerts: {d.total_alerts} ({d.false_positives} FP)
      </p>
      <p>Trend: {d.trend}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Chart Component                                                     */
/* ------------------------------------------------------------------ */

export default function FPRateByCorridorChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <p className="text-gray-500 text-sm">
        No corridor FP rate data available.
      </p>
    );
  }

  // Sort by FP rate descending for chart
  const sorted = [...data].sort((a, b) => b.fp_rate - a.fp_rate);

  // Truncate corridor names for display
  const chartData = sorted.map((d) => ({
    ...d,
    display_name:
      d.corridor_name.length > 25
        ? d.corridor_name.slice(0, 22) + "..."
        : d.corridor_name,
  }));

  const chartHeight = Math.max(300, chartData.length * 40 + 60);

  return (
    <div style={{ width: "100%", height: chartHeight }}>
      <ResponsiveContainer>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 120, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            type="number"
            domain={[0, 1]}
            tickFormatter={formatPercent}
          />
          <YAxis
            type="category"
            dataKey="display_name"
            width={110}
            tick={{ fontSize: 12 }}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine
            x={0.3}
            stroke="#ef4444"
            strokeDasharray="5 5"
            label={{ value: "30%", position: "top", fill: "#ef4444", fontSize: 11 }}
          />
          <ReferenceLine
            x={0.15}
            stroke="#f59e0b"
            strokeDasharray="5 5"
            label={{ value: "15%", position: "top", fill: "#f59e0b", fontSize: 11 }}
          />
          <ReferenceLine
            x={0.05}
            stroke="#22c55e"
            strokeDasharray="5 5"
            label={{ value: "5%", position: "top", fill: "#22c55e", fontSize: 11 }}
          />
          <Bar dataKey="fp_rate" name="FP Rate" radius={[0, 4, 4, 0]}>
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={barColor(entry.fp_rate)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
