import type { WidgetTheme } from './widgetTheme'

interface TimelineItem {
  gap_event_id: number
  date: string | null
  duration_minutes: number
  risk_score: number
  risk_tier: string
  status: string
}

interface TimelineData {
  vessel_id: number
  items: TimelineItem[]
  count: number
}

interface Props {
  data: TimelineData
  theme: WidgetTheme
}

export default function AlertTimelineWidget({ data, theme }: Props) {
  const items = data.items || []

  if (items.length === 0) {
    return (
      <div style={{ color: theme.textSecondary, padding: 4 }}>
        No alerts in the last 30 days.
      </div>
    )
  }

  return (
    <div
      style={{
        maxHeight: 300,
        overflowY: items.length > 5 ? 'auto' : 'visible',
        fontFamily: 'system-ui, sans-serif',
      }}
    >
      {items.map((item) => {
        const tierColor = theme.tierColors[item.risk_tier] || theme.tierColors.unknown
        const dateStr = item.date
          ? new Date(item.date).toLocaleDateString(undefined, {
              month: 'short',
              day: 'numeric',
            })
          : 'Unknown'
        return (
          <div
            key={item.gap_event_id}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              padding: '6px 0',
              borderBottom: `1px solid ${theme.border}`,
            }}
          >
            {/* Timeline dot */}
            <div
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: tierColor,
                marginTop: 4,
                flexShrink: 0,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: theme.text, fontWeight: 600, fontSize: '0.95em' }}>
                  {dateStr}
                </span>
                <span
                  style={{
                    display: 'inline-block',
                    padding: '1px 8px',
                    borderRadius: 10,
                    background: tierColor,
                    color: '#fff',
                    fontSize: '0.8em',
                    fontWeight: 600,
                  }}
                >
                  {item.risk_tier.toUpperCase()}
                </span>
              </div>
              <div style={{ color: theme.textSecondary, fontSize: '0.85em', marginTop: 2 }}>
                AIS gap: {Math.round(item.duration_minutes / 60)}h {item.duration_minutes % 60}m
                {' | '}Score: {item.risk_score}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
