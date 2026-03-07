import { useState } from 'react'
import { useTips, useUpdateTip } from '../hooks/useTips'
import type { Tip } from '../hooks/useTips'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { Pagination } from '../components/ui/Pagination'

const PAGE_SIZE = 20

const STATUSES = ['ALL', 'PENDING', 'REVIEWED', 'ACTIONED', 'DISMISSED'] as const

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}

const btnStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  padding: '2px 8px',
  cursor: 'pointer',
  fontSize: '0.6875rem',
  background: 'var(--bg-base)',
  color: 'var(--accent)',
}

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '-'
  return ts.slice(0, 16).replace('T', ' ')
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    PENDING: 'var(--warning, #d4a017)',
    REVIEWED: 'var(--accent)',
    ACTIONED: 'var(--score-low, #22c55e)',
    DISMISSED: 'var(--text-dim)',
  }
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '1px 6px',
        borderRadius: 'var(--radius)',
        fontSize: '0.6875rem',
        fontWeight: 700,
        background: colors[status] ?? 'var(--text-dim)',
        color: 'white',
      }}
    >
      {status}
    </span>
  )
}

function NoteCell({ tip, onSave }: { tip: Tip; onSave: (note: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(tip.analyst_note ?? '')

  if (!editing) {
    return (
      <span
        onClick={() => setEditing(true)}
        style={{ cursor: 'pointer', color: tip.analyst_note ? 'var(--text-bright)' : 'var(--text-dim)' }}
        title="Click to edit"
      >
        {tip.analyst_note || '(click to add)'}
      </span>
    )
  }

  return (
    <div style={{ display: 'flex', gap: 4 }}>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={2}
        style={{
          fontSize: '0.75rem',
          padding: 4,
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          background: 'var(--bg-base)',
          color: 'var(--text-bright)',
          flex: 1,
          resize: 'vertical',
        }}
        autoFocus
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <button
          style={{ ...btnStyle, color: 'var(--score-low, #22c55e)' }}
          onClick={() => { onSave(draft); setEditing(false) }}
        >
          Save
        </button>
        <button
          style={btnStyle}
          onClick={() => { setDraft(tip.analyst_note ?? ''); setEditing(false) }}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export function TipsAdminPage() {
  const [statusFilter, setStatusFilter] = useState<string>('ALL')
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useTips({
    status: statusFilter === 'ALL' ? undefined : statusFilter,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  })
  const updateTip = useUpdateTip()
  const tips = data ?? []
  const totalPages = Math.max(1, Math.ceil(tips.length < PAGE_SIZE ? page + 1 : page + 2))

  return (
    <div style={{ maxWidth: 1200 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Tips Administration
      </h2>

      <div style={{ display: 'flex', gap: 4, marginBottom: '1rem' }}>
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => { setStatusFilter(s); setPage(0) }}
            style={{
              ...btnStyle,
              background: statusFilter === s ? 'var(--accent)' : 'var(--bg-base)',
              color: statusFilter === s ? 'white' : 'var(--text-muted)',
              fontWeight: statusFilter === s ? 700 : 400,
            }}
          >
            {s}
          </button>
        ))}
      </div>

      <Card>
        {isLoading && <Spinner text="Loading tips..." />}
        {error && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>
            Failed to load tips
          </p>
        )}

        {!isLoading && tips.length === 0 && (
          <EmptyState title="No tips found" description="No tip submissions match the current filter." />
        )}

        {!isLoading && tips.length > 0 && (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--bg-base)' }}>
                    <th style={headStyle}>ID</th>
                    <th style={headStyle}>MMSI</th>
                    <th style={headStyle}>IMO</th>
                    <th style={headStyle}>Type</th>
                    <th style={headStyle}>Detail</th>
                    <th style={headStyle}>Status</th>
                    <th style={headStyle}>Created</th>
                    <th style={headStyle}>Analyst Note</th>
                    <th style={headStyle}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tips.map((tip) => (
                    <tr key={tip.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{tip.id}</td>
                      <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{tip.mmsi}</td>
                      <td style={cellStyle}>{tip.imo ?? '-'}</td>
                      <td style={cellStyle}>{tip.behavior_type}</td>
                      <td style={{ ...cellStyle, maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={tip.detail_text}>
                        {tip.detail_text}
                      </td>
                      <td style={cellStyle}><StatusBadge status={tip.status} /></td>
                      <td style={{ ...cellStyle, whiteSpace: 'nowrap' }}>{formatTimestamp(tip.created_at)}</td>
                      <td style={{ ...cellStyle, minWidth: 150 }}>
                        <NoteCell
                          tip={tip}
                          onSave={(note) => updateTip.mutate({ tipId: tip.id, analyst_note: note })}
                        />
                      </td>
                      <td style={cellStyle}>
                        <div style={{ display: 'flex', gap: 4 }}>
                          {tip.status !== 'REVIEWED' && (
                            <button style={btnStyle} onClick={() => updateTip.mutate({ tipId: tip.id, status: 'REVIEWED' })}>
                              Review
                            </button>
                          )}
                          {tip.status !== 'ACTIONED' && (
                            <button style={{ ...btnStyle, color: 'var(--score-low, #22c55e)' }} onClick={() => updateTip.mutate({ tipId: tip.id, status: 'ACTIONED' })}>
                              Action
                            </button>
                          )}
                          {tip.status !== 'DISMISSED' && (
                            <button style={{ ...btnStyle, color: 'var(--score-critical, #ef4444)' }} onClick={() => updateTip.mutate({ tipId: tip.id, status: 'DISMISSED' })}>
                              Dismiss
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Pagination
              page={page}
              totalPages={totalPages}
              total={tips.length}
              onPageChange={setPage}
              label="tips"
            />
          </>
        )}
      </Card>
    </div>
  )
}
