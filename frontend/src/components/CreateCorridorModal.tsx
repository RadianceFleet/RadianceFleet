import { useState } from 'react'
import { useCreateCorridor } from '../hooks/useCorridorMutation'
import type { CorridorCreatePayload } from '../types/api'

const overlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0, 0, 0, 0.6)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
}

const modalStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md, 8px)',
  padding: '1.5rem',
  width: '100%',
  maxWidth: 480,
  maxHeight: '90vh',
  overflowY: 'auto',
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

const labelStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  fontWeight: 600,
  color: 'var(--text-muted)',
  marginBottom: '0.25rem',
  display: 'block',
}

const btnBase: React.CSSProperties = {
  padding: '0.375rem 0.75rem',
  fontSize: '0.8125rem',
  fontWeight: 600,
  borderRadius: 'var(--radius)',
  cursor: 'pointer',
  border: 'none',
}

interface Props {
  onClose: () => void
}

export function CreateCorridorModal({ onClose }: Props) {
  const mutation = useCreateCorridor()

  const [name, setName] = useState('')
  const [corridorType, setCorridorType] = useState('transit')
  const [riskWeight, setRiskWeight] = useState('')
  const [description, setDescription] = useState('')
  const [isJammingZone, setIsJammingZone] = useState(false)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const payload: CorridorCreatePayload = {
      name: name.trim(),
      corridor_type: corridorType,
      is_jamming_zone: isJammingZone,
    }
    if (riskWeight) payload.risk_weight = parseFloat(riskWeight)
    if (description.trim()) payload.description = description.trim()

    mutation.mutate(payload, {
      onSuccess: () => onClose(),
    })
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={modalStyle} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-bright)' }}>
          Create Corridor
        </h3>

        <form onSubmit={handleSubmit}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {/* Name */}
            <div>
              <label style={labelStyle}>Name *</label>
              <input
                type="text"
                required
                value={name}
                onChange={e => setName(e.target.value)}
                style={inputStyle}
                placeholder="e.g. Strait of Hormuz"
              />
            </div>

            {/* Type */}
            <div>
              <label style={labelStyle}>Type</label>
              <select
                value={corridorType}
                onChange={e => setCorridorType(e.target.value)}
                style={inputStyle}
              >
                <option value="transit">Transit</option>
                <option value="sts_zone">STS Zone</option>
                <option value="jamming_zone">Jamming Zone</option>
              </select>
            </div>

            {/* Risk Weight */}
            <div>
              <label style={labelStyle}>Risk Weight</label>
              <input
                type="number"
                step="0.1"
                min="0"
                value={riskWeight}
                onChange={e => setRiskWeight(e.target.value)}
                style={{ ...inputStyle, fontFamily: 'monospace' }}
                placeholder="e.g. 1.5"
              />
            </div>

            {/* Description */}
            <div>
              <label style={labelStyle}>Description</label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={3}
                style={{ ...inputStyle, resize: 'vertical' }}
                placeholder="Optional description..."
              />
            </div>

            {/* Jamming Zone */}
            <div>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontSize: '0.8125rem',
                  color: 'var(--text-body)',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={isJammingZone}
                  onChange={e => setIsJammingZone(e.target.checked)}
                  style={{ accentColor: 'var(--accent-primary)' }}
                />
                Jamming Zone
              </label>
            </div>

            {mutation.isError && (
              <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', margin: 0 }}>
                Failed to create corridor. Please try again.
              </p>
            )}

            {/* Buttons */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button
                type="button"
                onClick={onClose}
                style={{
                  ...btnBase,
                  background: 'transparent',
                  color: 'var(--text-muted)',
                  border: '1px solid var(--border)',
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!name.trim() || mutation.isPending}
                style={{
                  ...btnBase,
                  background: 'var(--accent-primary)',
                  color: '#fff',
                  opacity: !name.trim() || mutation.isPending ? 0.6 : 1,
                }}
              >
                {mutation.isPending ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
