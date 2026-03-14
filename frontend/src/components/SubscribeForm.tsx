import { useState } from "react";

const API_BASE = (import.meta.env.VITE_API_URL ?? "") + "/api/v1";

export function SubscribeForm({
  mmsi,
  corridorId,
  label,
}: {
  mmsi?: string;
  corridorId?: number;
  label?: string;
}) {
  const [email, setEmail] = useState("");
  const [submittedEmail, setSubmittedEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(false);
  const [resendMsg, setResendMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, mmsi: mmsi || null, corridor_id: corridorId || null }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Subscription failed. Please try again.");
      } else {
        setSubmittedEmail(email);
        setSubmitted(true);
      }
    } catch {
      setError("Network error. Please try again.");
    }
    setLoading(false);
  };

  const handleResend = async () => {
    if (resendCooldown) return;
    setResendLoading(true);
    setResendMsg(null);
    try {
      await fetch(`${API_BASE}/subscribe/resend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: submittedEmail }),
      });
      setResendMsg("Confirmation email resent.");
      setResendCooldown(true);
      setTimeout(() => setResendCooldown(false), 60_000);
    } catch {
      setResendMsg("Failed to resend. Please try again later.");
    }
    setResendLoading(false);
  };

  const inputStyle: React.CSSProperties = {
    padding: "7px",
    background: "var(--bg)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "inherit",
    fontSize: 13,
  };

  if (submitted) {
    return (
      <div
        style={{
          padding: 12,
          fontSize: 13,
          background: "var(--bg-card)",
          borderRadius: "var(--radius)",
          border: "1px solid var(--border)",
        }}
      >
        <p style={{ margin: "0 0 8px" }}>
          Check your inbox to confirm. Check spam if you don't see it.
        </p>
        <p style={{ margin: "0 0 4px", color: "var(--text-dim)" }}>
          Didn't receive it?{" "}
          <button
            onClick={handleResend}
            disabled={resendCooldown || resendLoading}
            style={{
              background: "none",
              border: "none",
              color: "var(--accent)",
              cursor: resendCooldown ? "default" : "pointer",
              padding: 0,
              fontSize: 13,
              textDecoration: "underline",
            }}
          >
            {resendCooldown ? "Email sent" : resendLoading ? "Sending..." : "Resend confirmation"}
          </button>
        </p>
        {resendMsg && (
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-dim)" }}>{resendMsg}</p>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        padding: 12,
        background: "var(--bg-card)",
        borderRadius: "var(--radius)",
        border: "1px solid var(--border)",
      }}
    >
      <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--text-muted)" }}>
        {label || "Get email alerts for this vessel"}
      </p>
      <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8 }}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          placeholder="your@email.com"
          style={{ ...inputStyle, flex: 1 }}
        />
        <button
          type="submit"
          disabled={loading}
          style={{
            padding: "6px 14px",
            background: "var(--accent)",
            color: "#fff",
            border: "none",
            borderRadius: "var(--radius)",
            cursor: loading ? "not-allowed" : "pointer",
            fontSize: 13,
          }}
        >
          {loading ? "..." : "Subscribe"}
        </button>
      </form>
      {error && (
        <p style={{ fontSize: 12, color: "var(--score-critical)", margin: "6px 0 0" }}>{error}</p>
      )}
      <p style={{ fontSize: 11, color: "var(--text-dim)", margin: "6px 0 0" }}>
        One-click unsubscribe in every email. 12-month data retention.
      </p>
    </div>
  );
}
