import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import type { StreamAlert } from '../hooks/useAlertStream'

interface Props {
  alert: StreamAlert | null
}

function scoreBand(score: number): { label: string; color: string } {
  if (score >= 76) return { label: 'CRITICAL', color: '#dc2626' }
  if (score >= 51) return { label: 'HIGH', color: '#ea580c' }
  if (score >= 21) return { label: 'MEDIUM', color: '#d97706' }
  return { label: 'LOW', color: '#16a34a' }
}

export function AlertToast({ alert }: Props) {
  const [visible, setVisible] = useState(false)
  const [current, setCurrent] = useState<StreamAlert | null>(null)

  useEffect(() => {
    if (!alert) return
    setCurrent(alert)
    setVisible(true)
    const timer = setTimeout(() => setVisible(false), 8000)
    return () => clearTimeout(timer)
  }, [alert])

  if (!visible || !current) return null

  const band = scoreBand(current.risk_score)

  return (
    <div style={{
      position: 'fixed',
      top: 16,
      right: 16,
      zIndex: 9999,
      background: 'var(--bg-card, #1e293b)',
      border: `2px solid ${band.color}`,
      borderRadius: 8,
      padding: '12px 16px',
      maxWidth: 340,
      boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
      animation: 'slideIn 0.3s ease-out',
      fontSize: 13,
      fontFamily: 'monospace',
      color: 'var(--text-bright, #e2e8f0)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ color: band.color, fontWeight: 700, fontSize: 11, textTransform: 'uppercase' }}>
          {band.label} Alert
        </span>
        <button
          onClick={() => setVisible(false)}
          style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 16 }}
        >
          &times;
        </button>
      </div>
      <div>Score: <b style={{ color: band.color }}>{current.risk_score}</b></div>
      {current.gap_start_utc && (
        <div>{current.gap_start_utc.slice(0, 16).replace('T', ' ')} UTC</div>
      )}
      {current.duration_minutes && (
        <div>Duration: {(current.duration_minutes / 60).toFixed(1)}h</div>
      )}
      <Link
        to={`/alerts/${current.gap_event_id}`}
        style={{ color: 'var(--accent, #60a5fa)', textDecoration: 'underline', fontSize: 12, marginTop: 4, display: 'inline-block' }}
      >
        View details
      </Link>
    </div>
  )
}
