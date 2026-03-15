import { useQuery } from "@tanstack/react-query";
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { apiFetch } from "../lib/api";

interface BudgetData {
  budget_usd: number;
  spent_usd: number;
  committed_usd: number;
  remaining_usd: number;
  provider_breakdown: { provider: string; spent_usd: number; order_count: number }[];
  daily_burn_rate_usd: number;
  projected_monthly_spend_usd: number;
  bulk_orders_by_status: Record<string, number>;
}

const COLORS = ["#0088FE", "#FF8042", "#00C49F", "#FFBB28"];

export default function SatelliteBudgetDashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["satellite-budget-dashboard"],
    queryFn: () => apiFetch<BudgetData>("/satellite/budget/dashboard"),
    staleTime: 30_000,
  });

  if (isLoading) return <div>Loading budget data...</div>;
  if (error) return <div role="alert">Failed to load budget dashboard</div>;
  if (!data) return null;

  const donutData = [
    { name: "Spent", value: data.spent_usd },
    { name: "Committed", value: data.committed_usd },
    { name: "Remaining", value: Math.max(0, data.remaining_usd) },
  ];

  return (
    <div style={{ padding: "1.5rem" }}>
      <h2>Satellite Budget Dashboard</h2>

      <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap", marginBottom: "2rem" }}>
        <div>
          <h4>Monthly Budget: ${data.budget_usd.toLocaleString()}</h4>
          <p>Spent: ${data.spent_usd.toLocaleString()}</p>
          <p>Committed: ${data.committed_usd.toLocaleString()}</p>
          <p>Remaining: ${data.remaining_usd.toLocaleString()}</p>
        </div>
        <div>
          <h4>Burn Rate</h4>
          <p>Daily: ${data.daily_burn_rate_usd.toLocaleString()}/day</p>
          <p>Projected monthly: ${data.projected_monthly_spend_usd.toLocaleString()}</p>
        </div>
      </div>

      <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
        <div>
          <h3>Budget Allocation</h3>
          <ResponsiveContainer width={300} height={300}>
            <PieChart>
              <Pie
                data={donutData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                label={({ name, value }) => `${name}: $${value}`}
              >
                {donutData.map((_, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {data.provider_breakdown.length > 0 && (
          <div>
            <h3>Spend by Provider</h3>
            <ResponsiveContainer width={400} height={300}>
              <BarChart data={data.provider_breakdown}>
                <XAxis dataKey="provider" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="spent_usd" fill="#0088FE" name="Spent (USD)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {Object.keys(data.bulk_orders_by_status).length > 0 && (
        <div style={{ marginTop: "1.5rem" }}>
          <h3>Bulk Orders by Status</h3>
          <table style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ padding: "4px 12px", borderBottom: "1px solid #ccc" }}>Status</th>
                <th style={{ padding: "4px 12px", borderBottom: "1px solid #ccc" }}>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.bulk_orders_by_status).map(([status, count]) => (
                <tr key={status}>
                  <td style={{ padding: "4px 12px" }}>{status}</td>
                  <td style={{ padding: "4px 12px" }}>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
