import React, { useState } from "react";
import {
  useExportSubscriptions,
  useExportRuns,
  useCreateExportSubscription,
  useDeleteExportSubscription,
  useTriggerExportRun,
  type ExportSubscription,
  type ExportRun,
} from "../hooks/useExportSubscriptions";

const SCHEDULES = ["daily", "weekly", "monthly"] as const;
const EXPORT_TYPES = ["alerts", "vessels", "ais_positions", "evidence_cards"] as const;
const FORMATS = ["csv", "json", "parquet"] as const;
const DELIVERY_METHODS = ["email", "s3", "webhook"] as const;

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="badge badge-secondary">--</span>;
  const color =
    status === "completed" ? "badge-success" :
    status === "failed" ? "badge-danger" :
    status === "running" ? "badge-warning" :
    "badge-secondary";
  return <span className={`badge ${color}`}>{status}</span>;
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface CreateFormProps {
  onClose: () => void;
}

function CreateForm({ onClose }: CreateFormProps) {
  const createMutation = useCreateExportSubscription();
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState<string>("daily");
  const [scheduleDay, setScheduleDay] = useState<number | undefined>();
  const [scheduleHour, setScheduleHour] = useState(6);
  const [exportType, setExportType] = useState<string>("alerts");
  const [format, setFormat] = useState<string>("csv");
  const [deliveryMethod, setDeliveryMethod] = useState<string>("email");
  const [deliveryEmail, setDeliveryEmail] = useState("");
  const [s3Bucket, setS3Bucket] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [dateMode, setDateMode] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const deliveryConfig: Record<string, string> = {};
    if (deliveryMethod === "email") deliveryConfig.email = deliveryEmail;
    if (deliveryMethod === "s3") deliveryConfig.bucket = s3Bucket;
    if (deliveryMethod === "webhook") deliveryConfig.url = webhookUrl;

    const filterJson: Record<string, unknown> = {};
    if (dateMode) filterJson.date_mode = dateMode;

    createMutation.mutate(
      {
        name,
        schedule,
        schedule_day: scheduleDay ?? null,
        schedule_hour_utc: scheduleHour,
        export_type: exportType,
        format,
        delivery_method: deliveryMethod,
        delivery_config_json: deliveryConfig,
        filter_json: Object.keys(filterJson).length > 0 ? filterJson : null,
      },
      { onSuccess: () => onClose() }
    );
  };

  return (
    <form onSubmit={handleSubmit} className="export-create-form" data-testid="create-form">
      <h3>New Export Subscription</h3>
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label>
        Schedule
        <select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
          {SCHEDULES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      {schedule === "weekly" && (
        <label>
          Day of week (0=Mon)
          <input type="number" min={0} max={6} value={scheduleDay ?? 0} onChange={(e) => setScheduleDay(Number(e.target.value))} />
        </label>
      )}
      {schedule === "monthly" && (
        <label>
          Day of month
          <input type="number" min={1} max={28} value={scheduleDay ?? 1} onChange={(e) => setScheduleDay(Number(e.target.value))} />
        </label>
      )}
      <label>
        Hour (UTC)
        <input type="number" min={0} max={23} value={scheduleHour} onChange={(e) => setScheduleHour(Number(e.target.value))} />
      </label>
      <label>
        Export Type
        <select value={exportType} onChange={(e) => setExportType(e.target.value)}>
          {EXPORT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </label>
      <label>
        Date Filter
        <select value={dateMode} onChange={(e) => setDateMode(e.target.value)}>
          <option value="">No filter</option>
          <option value="last_day">Last day</option>
          <option value="last_week">Last week</option>
          <option value="last_month">Last month</option>
        </select>
      </label>
      <label>
        Format
        <select value={format} onChange={(e) => setFormat(e.target.value)}>
          {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </label>
      <label>
        Delivery Method
        <select value={deliveryMethod} onChange={(e) => setDeliveryMethod(e.target.value)}>
          {DELIVERY_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      {deliveryMethod === "email" && (
        <label>
          Email address
          <input type="email" value={deliveryEmail} onChange={(e) => setDeliveryEmail(e.target.value)} required />
        </label>
      )}
      {deliveryMethod === "s3" && (
        <label>
          S3 Bucket
          <input value={s3Bucket} onChange={(e) => setS3Bucket(e.target.value)} required />
        </label>
      )}
      {deliveryMethod === "webhook" && (
        <label>
          Webhook URL
          <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} required />
        </label>
      )}
      <div className="form-actions">
        <button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? "Creating..." : "Create"}
        </button>
        <button type="button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

function RunHistory({ subscriptionId }: { subscriptionId: number }) {
  const { data, isLoading } = useExportRuns(subscriptionId);

  if (isLoading) return <p>Loading runs...</p>;
  if (!data?.runs?.length) return <p>No runs yet.</p>;

  return (
    <table className="runs-table" data-testid="runs-table">
      <thead>
        <tr>
          <th>Run ID</th>
          <th>Started</th>
          <th>Status</th>
          <th>Rows</th>
          <th>Size</th>
          <th>Delivery</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {data.runs.map((run: ExportRun) => (
          <tr key={run.run_id}>
            <td>{run.run_id}</td>
            <td>{run.started_at ? new Date(run.started_at).toLocaleString() : "--"}</td>
            <td><StatusBadge status={run.status} /></td>
            <td>{run.row_count ?? "--"}</td>
            <td>{formatBytes(run.file_size_bytes)}</td>
            <td>{run.delivery_status ?? "--"}</td>
            <td title={run.error_message ?? ""}>{run.error_message ? run.error_message.slice(0, 50) : "--"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ExportSubscriptionsPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { data, isLoading, error } = useExportSubscriptions();
  const deleteMutation = useDeleteExportSubscription();
  const triggerMutation = useTriggerExportRun();

  if (isLoading) return <p>Loading export subscriptions...</p>;
  if (error) return <p className="error">Error loading subscriptions: {String(error)}</p>;

  return (
    <div className="export-subscriptions-page" data-testid="export-subscriptions-page">
      <div className="page-header">
        <h2>Export Subscriptions</h2>
        <button onClick={() => setShowCreate(true)}>+ New Subscription</button>
      </div>

      {showCreate && <CreateForm onClose={() => setShowCreate(false)} />}

      {!data?.subscriptions?.length ? (
        <p>No export subscriptions configured.</p>
      ) : (
        <table className="subscriptions-table" data-testid="subscriptions-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Schedule</th>
              <th>Type</th>
              <th>Format</th>
              <th>Delivery</th>
              <th>Status</th>
              <th>Last Run</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.subscriptions.map((sub: ExportSubscription) => (
              <React.Fragment key={sub.subscription_id}>
                <tr>
                  <td>{sub.name}</td>
                  <td>{sub.schedule}</td>
                  <td>{sub.export_type}</td>
                  <td>{sub.format}</td>
                  <td>{sub.delivery_method}</td>
                  <td>
                    <span className={sub.is_active ? "text-success" : "text-muted"}>
                      {sub.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    {sub.last_run_at
                      ? `${new Date(sub.last_run_at).toLocaleDateString()} (${sub.last_run_status})`
                      : "Never"}
                  </td>
                  <td>
                    <button
                      onClick={() => triggerMutation.mutate(sub.subscription_id)}
                      disabled={triggerMutation.isPending}
                      title="Run now"
                    >
                      Run
                    </button>
                    <button
                      onClick={() =>
                        setExpandedId(expandedId === sub.subscription_id ? null : sub.subscription_id)
                      }
                    >
                      History
                    </button>
                    <button
                      onClick={() => deleteMutation.mutate(sub.subscription_id)}
                      disabled={deleteMutation.isPending}
                      title="Delete"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
                {expandedId === sub.subscription_id && (
                  <tr>
                    <td colSpan={8}>
                      <RunHistory subscriptionId={sub.subscription_id} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
