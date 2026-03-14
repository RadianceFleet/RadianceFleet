import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from "recharts";

interface ScoreBand {
  label: string;
  count: number;
  color: string;
}

interface Props {
  data: ScoreBand[];
}

export function ScoreDistributionChart({ data }: Props) {
  if (!data || data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 50 }}>
        <XAxis type="number" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fontSize: 11 }}
          stroke="var(--text-muted)"
          width={50}
        />
        <Tooltip
          contentStyle={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            fontSize: "0.75rem",
          }}
          labelStyle={{ color: "var(--text-bright)" }}
        />
        <Bar dataKey="count" radius={[0, 3, 3, 0]}>
          {data.map((entry, index) => (
            <Cell key={index} fill={entry.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
