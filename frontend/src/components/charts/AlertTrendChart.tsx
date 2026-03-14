import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../lib/api";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { Spinner } from "../ui/Spinner";
import { ErrorMessage } from "../ui/ErrorMessage";

interface TrendBucket {
  date: string;
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  reviewed: number;
}

interface TrendResponse {
  period: string;
  buckets: TrendBucket[];
  summary: { total_new: number; total_reviewed: number; review_ratio: number };
}

export function AlertTrendChart({ period = "7d" }: { period?: string }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["alert-trends", period],
    queryFn: () => apiFetch<TrendResponse>(`/alerts/trends?period=${period}`),
  });

  if (isLoading) return <Spinner text="Loading trends..." />;
  if (error) return <ErrorMessage error={error} subject="trends" onRetry={refetch} />;
  if (!data) return null;

  return (
    <div>
      <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
        {data.summary && (
          <>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              New: <b style={{ color: "var(--text-bright)" }}>{data.summary.total_new}</b>
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Reviewed: <b style={{ color: "var(--accent)" }}>{data.summary.total_reviewed}</b>
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Review rate: <b>{(data.summary.review_ratio * 100).toFixed(0)}%</b>
            </span>
          </>
        )}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data.buckets}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
          <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
          <Tooltip
            contentStyle={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--text-bright)" }}
          />
          <Line type="monotone" dataKey="critical" stroke="#dc2626" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="high" stroke="#ea580c" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="medium" stroke="#d97706" strokeWidth={1} dot={false} />
          <Line type="monotone" dataKey="total" stroke="#60a5fa" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
