import { Link } from 'react-router-dom'
import { useVesselTimeline } from '../hooks/useVessels'
import { Card } from './ui/Card'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import type { TimelineEvent } from '../types/api'

const sectionHead: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 14,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 1,
}

const EVENT_COLORS: Record<string, string> = {
  ais_gap: 'var(--score-critical)',
  sts_transfer: 'var(--warning)',
  loitering: '#d4a017',
  spoofing: '#9b59b6',
  identity_change: 'var(--accent)',
  port_call: 'var(--score-low)',
}

function getEventColor(eventType: string): string {
  return EVENT_COLORS[eventType] ?? 'var(--text-muted)'
}

function getEventLink(event: TimelineEvent): string {
  switch (event.event_type) {
    case 'ais_gap':
      return `/alerts/${event.related_entity_id}`
    case 'sts_transfer':
      return '/sts-events'
    case 'spoofing':
      return `/alerts/${event.related_entity_id}`
    case 'loitering':
      return `/alerts/${event.related_entity_id}`
    case 'identity_change':
      return '#'
    case 'port_call':
      return '#'
    default:
      return '#'
  }
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return '--'
  return ts.slice(0, 19).replace('T', ' ') + ' UTC'
}

function exportTimelineCSV(events: TimelineEvent[], vesselId: string) {
  const header = 'event_type,timestamp,summary\n'
  const rows = events
    .map(
      (e) =>
        `"${e.event_type}","${e.timestamp ?? ''}","${(e.summary ?? '').replace(/"/g, '""')}"`
    )
    .join('\n')
  const blob = new Blob([header + rows], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `vessel_${vesselId}_timeline.csv`
  a.click()
  URL.revokeObjectURL(url)
}

interface VesselTimelineProps {
  vesselId: string
}

export function VesselTimeline({ vesselId }: VesselTimelineProps) {
  const { data, isLoading } = useVesselTimeline(vesselId)
  const events = data?.events ?? []

  return (
    <Card style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ ...sectionHead, margin: 0 }}>Activity Timeline</h3>
        {events.length > 0 && (
          <button
            onClick={() => exportTimelineCSV(events, vesselId)}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: '4px 10px',
              cursor: 'pointer',
              fontSize: 12,
              background: 'var(--bg-base)',
              color: 'var(--accent)',
            }}
          >
            Export CSV
          </button>
        )}
      </div>

      {isLoading && <Spinner text="Loading timeline..." />}
      {!isLoading && events.length === 0 && (
        <EmptyState title="No timeline events" description="No activity events recorded for this vessel." />
      )}

      {!isLoading && events.length > 0 && (
        <div style={{ position: 'relative', paddingLeft: 20 }}>
          {/* Vertical timeline line */}
          <div
            style={{
              position: 'absolute',
              left: 6,
              top: 4,
              bottom: 4,
              width: 2,
              background: 'var(--border)',
            }}
          />
          {events.map((event, i) => {
            const color = getEventColor(event.event_type)
            const link = getEventLink(event)
            const isClickable = link !== '#'

            return (
              <div key={`${event.event_type}-${event.related_entity_id}-${i}`} style={{ position: 'relative', marginBottom: 16 }}>
                {/* Timeline dot */}
                <div
                  style={{
                    position: 'absolute',
                    left: -17,
                    top: 4,
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: color,
                    border: '2px solid var(--bg-card)',
                  }}
                />

                {/* Timestamp */}
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 2 }}>
                  {formatTimestamp(event.timestamp)}
                </div>

                {/* Event card content */}
                <div
                  style={{
                    background: 'var(--bg-base)',
                    borderRadius: 'var(--radius)',
                    padding: '8px 12px',
                    borderLeft: `3px solid ${color}`,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    {/* Event type badge */}
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '1px 6px',
                        borderRadius: 'var(--radius)',
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                        background: color,
                        color: 'white',
                      }}
                    >
                      {event.event_type.replace(/_/g, ' ')}
                    </span>
                  </div>

                  {/* Summary text */}
                  <div style={{ fontSize: 13, color: 'var(--text-bright)' }}>
                    {isClickable ? (
                      <Link to={link} style={{ color: 'var(--text-bright)', textDecoration: 'none' }}>
                        {event.summary}
                      </Link>
                    ) : (
                      event.summary
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
