import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";

interface BulkOrderItem {
  item_id: number;
  priority_rank: number;
  vessel_id: number;
  alert_id: number | null;
  provider_preference: string | null;
  status: string;
  skip_reason: string | null;
  satellite_order_id: number | null;
}

interface BulkOrder {
  bulk_order_id: number;
  name: string;
  status: string;
  priority: number;
  total_orders: number;
  submitted_orders: number;
  delivered_orders: number;
  failed_orders: number;
  estimated_total_cost_usd: number | null;
  actual_total_cost_usd: number | null;
  budget_cap_usd: number | null;
  created_at: string | null;
}

interface BulkOrderDetail extends BulkOrder {
  items: BulkOrderItem[];
  requested_by: number | null;
  updated_at: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  draft: "#6b7280",
  queued: "#3b82f6",
  processing: "#f59e0b",
  completed: "#10b981",
  cancelled: "#ef4444",
  pending: "#6b7280",
  submitted: "#3b82f6",
  delivered: "#10b981",
  failed: "#ef4444",
  skipped: "#9ca3af",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 12,
        color: "#fff",
        backgroundColor: STATUS_COLORS[status] || "#6b7280",
      }}
    >
      {status}
    </span>
  );
}

function ProgressBar({ submitted, total }: { submitted: number; total: number }) {
  const pct = total > 0 ? Math.round((submitted / total) * 100) : 0;
  return (
    <div
      style={{ width: 120, height: 16, backgroundColor: "#e5e7eb", borderRadius: 8, overflow: "hidden" }}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        style={{ width: `${pct}%`, height: "100%", backgroundColor: "#3b82f6", transition: "width 0.3s" }}
      />
    </div>
  );
}

export default function BulkOrderStatusTable() {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");

  const { data, isLoading } = useQuery({
    queryKey: ["bulk-orders", statusFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      return apiFetch<{ total: number; orders: BulkOrder[] }>(
        `/satellite/bulk-orders?${params}`
      );
    },
    staleTime: 15_000,
  });

  const { data: detail } = useQuery({
    queryKey: ["bulk-order-detail", expandedId],
    queryFn: () =>
      apiFetch<BulkOrderDetail>(`/satellite/bulk-orders/${expandedId}`),
    enabled: expandedId !== null,
  });

  const queueMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/satellite/bulk-orders/${id}/queue`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["bulk-orders"] }),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/satellite/bulk-orders/${id}/cancel`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["bulk-orders"] }),
  });

  if (isLoading) return <div>Loading bulk orders...</div>;

  const orders = data?.orders ?? [];

  return (
    <div style={{ padding: "1.5rem" }}>
      <h2>Bulk Satellite Orders</h2>

      <div style={{ marginBottom: "1rem" }}>
        <label htmlFor="status-filter">Filter by status: </label>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All</option>
          <option value="draft">Draft</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
          <option value="completed">Completed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {orders.length === 0 ? (
        <p>No bulk orders found.</p>
      ) : (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>ID</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Name</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Status</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Priority</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Progress</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Cost</th>
              <th style={{ textAlign: "left", padding: "8px", borderBottom: "2px solid #ddd" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <>
                <tr
                  key={order.bulk_order_id}
                  style={{ cursor: "pointer" }}
                  onClick={() =>
                    setExpandedId(expandedId === order.bulk_order_id ? null : order.bulk_order_id)
                  }
                >
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>
                    {order.bulk_order_id}
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>{order.name}</td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>
                    <StatusBadge status={order.status} />
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>{order.priority}</td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>
                    <ProgressBar submitted={order.submitted_orders} total={order.total_orders} />
                    <span style={{ fontSize: 12, marginLeft: 4 }}>
                      {order.submitted_orders}/{order.total_orders}
                    </span>
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>
                    {order.actual_total_cost_usd != null
                      ? `$${order.actual_total_cost_usd.toLocaleString()}`
                      : order.estimated_total_cost_usd != null
                        ? `~$${order.estimated_total_cost_usd.toLocaleString()}`
                        : "-"}
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #eee" }}>
                    {order.status === "draft" && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          queueMutation.mutate(order.bulk_order_id);
                        }}
                      >
                        Queue
                      </button>
                    )}
                    {["draft", "queued", "processing"].includes(order.status) && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          cancelMutation.mutate(order.bulk_order_id);
                        }}
                        style={{ marginLeft: 4 }}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
                {expandedId === order.bulk_order_id && detail && (
                  <tr key={`${order.bulk_order_id}-detail`}>
                    <td colSpan={7} style={{ padding: "8px 8px 16px 24px", background: "#f9fafb" }}>
                      <h4 style={{ margin: "0 0 8px" }}>Items ({detail.items.length})</h4>
                      <table style={{ borderCollapse: "collapse", width: "100%" }}>
                        <thead>
                          <tr>
                            <th style={{ textAlign: "left", padding: 4 }}>Rank</th>
                            <th style={{ textAlign: "left", padding: 4 }}>Vessel</th>
                            <th style={{ textAlign: "left", padding: 4 }}>Provider</th>
                            <th style={{ textAlign: "left", padding: 4 }}>Status</th>
                            <th style={{ textAlign: "left", padding: 4 }}>Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.items.map((item) => (
                            <tr key={item.item_id}>
                              <td style={{ padding: 4 }}>{item.priority_rank}</td>
                              <td style={{ padding: 4 }}>{item.vessel_id}</td>
                              <td style={{ padding: 4 }}>{item.provider_preference || "any"}</td>
                              <td style={{ padding: 4 }}>
                                <StatusBadge status={item.status} />
                              </td>
                              <td style={{ padding: 4 }}>{item.skip_reason || "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
