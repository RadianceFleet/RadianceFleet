import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ReferenceLine,
} from "recharts";

export interface WaterfallEntry {
  label: string;
  value: number;
  cumulative: number;
  is_multiplier: boolean;
}

interface Props {
  data: WaterfallEntry[];
}

/**
 * Waterfall chart showing cumulative signal contributions to a risk score.
 *
 * Each bar starts at the previous cumulative total and extends by the
 * signal's individual contribution. Multiplier effects are shown in a
 * distinct colour.
 */
export function WaterfallChart({ data }: Props) {
  if (!data || data.length === 0) return null;

  // Transform data for stacked bar rendering:
  // Each bar needs an invisible "base" (previous cumulative) and a visible "value"
  const chartData = data.map((entry, i) => {
    const prev = i > 0 ? data[i - 1].cumulative : 0;
    const base = entry.value >= 0 ? prev : prev + entry.value;
    const visibleValue = Math.abs(entry.value);
    return {
      label: entry.label,
      base: Math.round(base * 100) / 100,
      value: Math.round(visibleValue * 100) / 100,
      rawValue: entry.value,
      cumulative: entry.cumulative,
      is_multiplier: entry.is_multiplier,
    };
  });

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, data.length * 32)}>
      <BarChart
        data={chartData}
        layout="vertical"
        margin={{ top: 4, right: 40, bottom: 4, left: 120 }}
      >
        <XAxis
          type="number"
          tick={{ fontSize: 11 }}
          stroke="var(--text-muted)"
        />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fontSize: 11 }}
          stroke="var(--text-muted)"
          width={115}
        />
        <Tooltip
          contentStyle={{
            background: "var(--bg-surface, #1e1e2e)",
            border: "1px solid var(--border, #333)",
            fontSize: "0.75rem",
          }}
          labelStyle={{ color: "var(--text-bright, #fff)" }}
          formatter={(_val: number, _name: string, props: { payload: { rawValue: number; cumulative: number; is_multiplier: boolean } }) => {
            const { rawValue, cumulative, is_multiplier } = props.payload;
            return [
              `${rawValue >= 0 ? "+" : ""}${rawValue} pts (total: ${cumulative})${is_multiplier ? " [multiplier]" : ""}`,
              "Contribution",
            ];
          }}
        />
        <ReferenceLine x={0} stroke="var(--text-dim, #666)" />
        {/* Invisible base bar */}
        <Bar dataKey="base" stackId="waterfall" fill="transparent" />
        {/* Visible contribution bar */}
        <Bar dataKey="value" stackId="waterfall" radius={[0, 3, 3, 0]}>
          {chartData.map((entry, index) => {
            let fill: string;
            if (entry.is_multiplier) {
              fill = "#a855f7"; // purple for multipliers
            } else if (entry.rawValue < 0) {
              fill = "#22c55e"; // green for deductions
            } else {
              fill = "#ef4444"; // red for risk contributions
            }
            return <Cell key={index} fill={fill} />;
          })}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
