import type { VesselHistoryEntry } from '../types/api'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { sectionHead } from '../styles/tables'

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '--'
  return ts.slice(0, 19).replace('T', ' ') + ' UTC'
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface VesselIdentityTimelineProps {
  historyEntries: VesselHistoryEntry[]
  isLoading: boolean
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function VesselIdentityTimeline({ historyEntries, isLoading }: VesselIdentityTimelineProps) {
  return (
    <Card style={{ marginBottom: 16 }}>
      <h3 style={sectionHead}>Identity History</h3>
      {isLoading && <Spinner text="Loading history..." />}
      {!isLoading && historyEntries.length === 0 && (
        <EmptyState title="No identity changes recorded" />
      )}
      {!isLoading && historyEntries.length > 0 && (
        <div style={{ position: 'relative', paddingLeft: 20 }}>
          {/* Vertical timeline line */}
          <div style={{
            position: 'absolute',
            left: 6,
            top: 4,
            bottom: 4,
            width: 2,
            background: 'var(--border)',
          }} />
          {historyEntries.map((h, i) => (
            <div key={h.vessel_history_id ?? i} style={{ position: 'relative', marginBottom: 16 }}>
              {/* Timeline dot */}
              <div style={{
                position: 'absolute',
                left: -17,
                top: 4,
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: 'var(--accent)',
                border: '2px solid var(--bg-card)',
              }} />
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 2 }}>
                {formatTimestamp(h.observed_at)}
              </div>
              <div style={{ fontSize: 13 }}>
                <span style={{ color: 'var(--text-muted)' }}>{h.field_changed}:</span>{' '}
                <span style={{ color: 'var(--score-medium)', textDecoration: 'line-through' }}>
                  {h.old_value || '(none)'}
                </span>
                {' '}&rarr;{' '}
                <span style={{ color: 'var(--accent)', fontWeight: 600 }}>
                  {h.new_value || '(none)'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
