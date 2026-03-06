import { useState, useEffect } from 'react'
import type { AlertStatus } from '../types/api'
import type { UseMutationResult } from '@tanstack/react-query'
import { card, sectionHead, btnStyle } from '../styles/tables'

const STATUSES: AlertStatus[] = ['new', 'under_review', 'needs_satellite_check', 'documented', 'dismissed']

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertStatusPanelProps {
  currentStatus: string
  analystNotes: string | null
  statusMutation: UseMutationResult<unknown, Error, { status: string }, unknown>
  notesMutation: UseMutationResult<unknown, Error, string, unknown>
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertStatusPanel({ currentStatus, analystNotes, statusMutation, notesMutation }: AlertStatusPanelProps) {
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (analystNotes) setNotes(analystNotes)
  }, [analystNotes])

  // Auto-dismiss "Saved" after 3 seconds
  useEffect(() => {
    if (!saved) return
    const timer = setTimeout(() => setSaved(false), 3000)
    return () => clearTimeout(timer)
  }, [saved])

  return (
    <section style={card}>
      <h3 style={sectionHead}>Analyst Workflow</h3>

      {/* Error banners */}
      {statusMutation.error && (
        <div style={{ background: 'var(--score-critical)', color: 'white', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 13 }}>
          Status update failed: {statusMutation.error instanceof Error ? statusMutation.error.message : 'Unknown error'}
        </div>
      )}
      {notesMutation.error && (
        <div style={{ background: 'var(--score-critical)', color: 'white', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 13 }}>
          Notes save failed: {notesMutation.error instanceof Error ? notesMutation.error.message : 'Unknown error'}
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Status</label><br />
        <select
          value={currentStatus}
          onChange={e => statusMutation.mutate({ status: e.target.value })}
          disabled={statusMutation.isPending}
          style={{
            background: 'var(--bg-base)', color: 'var(--text-bright)', border: '1px solid var(--border)',
            padding: '6px 10px', borderRadius: 'var(--radius)', marginTop: 4, fontSize: 13,
            opacity: statusMutation.isPending ? 0.6 : 1,
          }}
        >
          {STATUSES.map(s => (
            <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
          ))}
        </select>
        {statusMutation.isPending && <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>Saving…</span>}
      </div>
      <div style={{ marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Analyst Notes</label><br />
        <textarea
          value={notes}
          onChange={e => { setNotes(e.target.value); setSaved(false) }}
          rows={4}
          style={{
            width: '100%', background: 'var(--bg-base)', color: 'var(--text-bright)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: 8, marginTop: 4,
            fontFamily: 'monospace', fontSize: 13, boxSizing: 'border-box', resize: 'vertical',
          }}
        />
        <button
          onClick={() => notesMutation.mutate(notes)}
          disabled={notesMutation.isPending}
          style={{
            ...btnStyle,
            background: 'var(--accent-primary)',
            color: '#fff',
            border: 'none',
            marginTop: 6,
            opacity: notesMutation.isPending ? 0.6 : 1,
          }}
        >
          {notesMutation.isPending ? 'Saving…' : saved ? '✓ Saved' : 'Save Notes'}
        </button>
      </div>
    </section>
  )
}
