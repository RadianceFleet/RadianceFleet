import type { UseMutationResult } from '@tanstack/react-query'
import { inputStyle, btnStyle } from '../styles/tables'

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertBulkActionsProps {
  selected: Set<number>
  onClearSelection: () => void
  bulkStatus: string
  onBulkStatusChange: (value: string) => void
  bulkUpdate: UseMutationResult<{ updated: number }, Error, { alert_ids: number[]; status: string }, unknown>
  addToast: (message: string, type: 'success' | 'error') => void
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertBulkActions({
  selected,
  onClearSelection,
  bulkStatus,
  onBulkStatusChange,
  bulkUpdate,
  addToast,
}: AlertBulkActionsProps) {
  if (selected.size === 0) return null

  return (
    <div style={{
      position: 'sticky', bottom: 12, zIndex: 10,
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '10px 16px', marginBottom: 8,
      background: 'var(--bg-card)', border: '1px solid var(--accent-primary)',
      borderRadius: 'var(--radius-md)', fontSize: 13,
      boxShadow: '0 -2px 12px rgba(0,0,0,0.25)',
    }}>
      <span style={{ color: 'var(--text-body)', fontWeight: 600 }}>{selected.size} selected</span>
      <div style={{ width: 1, height: 20, background: 'var(--border)' }} />
      <button
        onClick={() => {
          bulkUpdate.mutate(
            { alert_ids: [...selected], status: 'documented' },
            {
              onSuccess: (data) => {
                addToast(`Marked ${data.updated} alert(s) as reviewed`, 'success')
                onClearSelection()
              },
              onError: () => addToast('Failed to update alerts', 'error'),
            }
          )
        }}
        disabled={bulkUpdate.isPending}
        style={{ ...btnStyle, background: '#27ae60', color: '#fff', borderColor: '#27ae60' }}
      >
        Mark Reviewed
      </button>
      <select value={bulkStatus} onChange={e => onBulkStatusChange(e.target.value)} style={inputStyle}>
        <option value="under_review">Under review</option>
        <option value="needs_satellite_check">Needs satellite check</option>
        <option value="documented">Documented</option>
        <option value="dismissed">Dismissed</option>
      </select>
      <button
        onClick={() => {
          bulkUpdate.mutate(
            { alert_ids: [...selected], status: bulkStatus },
            {
              onSuccess: (data) => {
                addToast(`Updated ${data.updated} alert(s) to "${bulkStatus}"`, 'success')
                onClearSelection()
              },
              onError: () => addToast('Failed to update alerts', 'error'),
            }
          )
        }}
        disabled={bulkUpdate.isPending}
        style={{ ...btnStyle, background: 'var(--accent-primary)', color: '#fff', borderColor: 'var(--accent-primary)' }}
      >
        {bulkUpdate.isPending ? 'Updating...' : 'Apply'}
      </button>
      <div style={{ width: 1, height: 20, background: 'var(--border)' }} />
      <a
        href={`/api/v1/alerts/export?ids=${[...selected].join(',')}`}
        download
        style={{
          ...btnStyle,
          textDecoration: 'none',
          display: 'inline-block',
        }}
      >
        Export CSV
      </a>
      <button onClick={onClearSelection} style={btnStyle}>Clear</button>
    </div>
  )
}
