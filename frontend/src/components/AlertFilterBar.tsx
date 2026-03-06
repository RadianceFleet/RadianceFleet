import { ExportButton } from './ExportButton'
import { inputStyle, btnStyle } from '../styles/tables'

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertFilterBarProps {
  minScore: string
  onMinScoreChange: (value: string) => void
  status: string
  onStatusChange: (value: string) => void
  vesselName: string
  onVesselNameChange: (value: string) => void
  dateFrom: string
  onDateFromChange: (value: string) => void
  dateTo: string
  onDateToChange: (value: string) => void
  patternsOnly: boolean
  onPatternsOnlyToggle: () => void
  exportFilters: Record<string, string | undefined>
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertFilterBar({
  minScore,
  onMinScoreChange,
  status,
  onStatusChange,
  vesselName,
  onVesselNameChange,
  dateFrom,
  onDateFromChange,
  dateTo,
  onDateToChange,
  patternsOnly,
  onPatternsOnlyToggle,
  exportFilters,
}: AlertFilterBarProps) {
  return (
    <div style={{ display: 'flex', gap: 10, marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
      <input
        placeholder="Min score"
        value={minScore}
        onChange={e => onMinScoreChange(e.target.value)}
        style={{ ...inputStyle, width: 90 }}
      />
      <select
        value={status}
        onChange={e => onStatusChange(e.target.value)}
        style={inputStyle}
      >
        <option value="">All statuses</option>
        <option value="new">New</option>
        <option value="under_review">Under review</option>
        <option value="needs_satellite_check">Needs satellite check</option>
        <option value="documented">Documented</option>
        <option value="dismissed">Dismissed</option>
      </select>
      <input
        placeholder="Vessel name"
        value={vesselName}
        onChange={e => onVesselNameChange(e.target.value)}
        style={{ ...inputStyle, width: 160 }}
      />
      <input
        type="date"
        value={dateFrom}
        onChange={e => onDateFromChange(e.target.value)}
        style={inputStyle}
        title="Date from"
      />
      <input
        type="date"
        value={dateTo}
        onChange={e => onDateToChange(e.target.value)}
        style={inputStyle}
        title="Date to"
      />
      <ExportButton filters={exportFilters} />
      <button
        onClick={onPatternsOnlyToggle}
        style={{
          ...btnStyle,
          background: patternsOnly ? '#9b59b6' : 'var(--bg-card)',
          color: patternsOnly ? 'white' : 'var(--text-muted)',
          borderColor: patternsOnly ? '#9b59b6' : 'var(--border)',
        }}
      >
        {patternsOnly ? 'Patterns only' : 'Patterns only'}
      </button>
    </div>
  )
}
