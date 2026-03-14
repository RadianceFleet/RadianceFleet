import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, Legend } from "recharts";
import { useCorridorActivity } from "../../hooks/useCorridorActivity";
import { Spinner } from "../ui/Spinner";
import { ErrorMessage } from "../ui/ErrorMessage";

interface Props {
  corridorId: string | undefined;
}

export function CorridorActivityChart({ corridorId }: Props) {
  const { data, isLoading, error, refetch } = useCorridorActivity(corridorId);

  if (isLoading) return <Spinner text="Loading activity…" />;
  if (error) return <ErrorMessage error={error} subject="activity data" onRetry={refetch} />;
  if (!data || data.length === 0)
    return (
      <p style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>No activity data available.</p>
    );

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
        <XAxis dataKey="period_start" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
        <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
        <Tooltip
          contentStyle={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            fontSize: "0.75rem",
          }}
          labelStyle={{ color: "var(--text-bright)" }}
        />
        <Legend wrapperStyle={{ fontSize: "0.75rem" }} />
        <Line
          type="monotone"
          dataKey="gap_count"
          stroke="var(--score-critical)"
          name="Gaps"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="distinct_vessels"
          stroke="var(--accent)"
          name="Vessels"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="avg_risk_score"
          stroke="var(--warning)"
          name="Avg Risk"
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
