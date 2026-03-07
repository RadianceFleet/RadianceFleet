import { useState } from 'react'
import { useCreateHuntMission, useHuntTargets } from '../../hooks/useHunt'
import { Card } from '../ui/Card'

const overlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.5)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 9999,
}

const labelStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  fontWeight: 600,
  color: 'var(--text-muted)',
  marginBottom: '0.25rem',
}

const inputStyle: React.CSSProperties = {
  padding: '0.375rem 0.5rem',
  background: 'var(--bg-base)',
  color: 'var(--text-body)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  fontSize: '0.8125rem',
  width: '100%',
  boxSizing: 'border-box',
}

const btnBase: React.CSSProperties = {
  padding: '0.375rem 0.75rem',
  fontSize: '0.8125rem',
  fontWeight: 600,
  borderRadius: 'var(--radius)',
  cursor: 'pointer',
  border: 'none',
}

export function CreateMissionModal({ onClose }: { onClose: () => void }) {
  const [targetProfileId, setTargetProfileId] = useState('')
  const [searchStart, setSearchStart] = useState('')
  const [searchEnd, setSearchEnd] = useState('')
  const mutation = useCreateHuntMission()
  const { data: targets } = useHuntTargets({ limit: 200 })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const pid = parseInt(targetProfileId, 10)
    if (isNaN(pid) || !searchStart || !searchEnd) return
    mutation.mutate(
      {
        target_profile_id: pid,
        search_start_utc: new Date(searchStart).toISOString(),
        search_end_utc: new Date(searchEnd).toISOString(),
      },
      { onSuccess: () => onClose() },
    )
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <Card style={{ width: 420, maxHeight: '80vh', overflow: 'auto' }}>
        <form onSubmit={handleSubmit} onClick={e => e.stopPropagation()}>
          <h3 style={{ margin: '0 0 1rem', fontSize: '0.9375rem', color: 'var(--text-bright)' }}>
            New Search Mission
          </h3>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div>
              <div style={labelStyle}>Target Profile *</div>
              {targets && targets.length > 0 ? (
                <select
                  required
                  value={targetProfileId}
                  onChange={e => setTargetProfileId(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">Select a target...</option>
                  {targets.map(t => (
                    <option key={t.profile_id} value={t.profile_id}>
                      #{t.profile_id} — Vessel {t.vessel_id}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  required
                  value={targetProfileId}
                  onChange={e => setTargetProfileId(e.target.value)}
                  style={inputStyle}
                  placeholder="Target profile ID"
                />
              )}
            </div>
            <div>
              <div style={labelStyle}>Search Start *</div>
              <input
                type="datetime-local"
                required
                value={searchStart}
                onChange={e => setSearchStart(e.target.value)}
                style={inputStyle}
              />
            </div>
            <div>
              <div style={labelStyle}>Search End *</div>
              <input
                type="datetime-local"
                required
                value={searchEnd}
                onChange={e => setSearchEnd(e.target.value)}
                style={inputStyle}
              />
            </div>
          </div>

          {mutation.isError && (
            <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', margin: '0.75rem 0 0' }}>
              Failed to create mission. Please try again.
            </p>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1rem' }}>
            <button
              type="button"
              onClick={onClose}
              style={{ ...btnBase, background: 'transparent', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              style={{ ...btnBase, background: 'var(--accent-primary)', color: '#fff', opacity: mutation.isPending ? 0.6 : 1 }}
            >
              {mutation.isPending ? 'Creating...' : 'Create Mission'}
            </button>
          </div>
        </form>
      </Card>
    </div>
  )
}
