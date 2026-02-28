import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useCorridorDetail } from '../hooks/useCorridors'
import { useUpdateCorridor, useDeleteCorridor } from '../hooks/useCorridorMutation'
import type { CorridorUpdatePayload } from '../types/api'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'

const inputStyle: React.CSSProperties = {
  padding: '0.375rem 0.5rem',
  background: 'var(--bg-base)',
  color: 'var(--text-body)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  fontSize: '0.8125rem',
  width: '100%',
}

const labelStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  fontWeight: 600,
  color: 'var(--text-muted)',
  marginBottom: '0.25rem',
}

const valueStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  color: 'var(--text-body)',
}

const rowStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.25rem',
}

const btnBase: React.CSSProperties = {
  padding: '0.375rem 0.75rem',
  fontSize: '0.8125rem',
  fontWeight: 600,
  borderRadius: 'var(--radius)',
  cursor: 'pointer',
  border: 'none',
}

function formatType(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function CorridorDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: corridor, isLoading, error } = useCorridorDetail(id)
  const mutation = useUpdateCorridor(id ?? '')
  const deleteMutation = useDeleteCorridor()

  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editRiskWeight, setEditRiskWeight] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editIsJammingZone, setEditIsJammingZone] = useState(false)

  useEffect(() => {
    if (corridor) {
      setEditName(String(corridor.name ?? ''))
      setEditRiskWeight(String(corridor.risk_weight ?? ''))
      setEditDescription(String(corridor.description ?? ''))
      setEditIsJammingZone(Boolean(corridor.is_jamming_zone))
    }
  }, [corridor])

  function handleEdit() {
    if (corridor) {
      setEditName(String(corridor.name ?? ''))
      setEditRiskWeight(String(corridor.risk_weight ?? ''))
      setEditDescription(String(corridor.description ?? ''))
      setEditIsJammingZone(Boolean(corridor.is_jamming_zone))
    }
    setEditing(true)
  }

  function handleCancel() {
    setEditing(false)
  }

  function handleSave() {
    const payload: CorridorUpdatePayload = {
      name: editName,
      risk_weight: parseFloat(editRiskWeight),
      description: editDescription,
      is_jamming_zone: editIsJammingZone,
    }
    mutation.mutate(payload, {
      onSuccess: () => setEditing(false),
    })
  }

  function handleDelete() {
    if (!corridor) return
    const confirmed = window.confirm(
      `Are you sure you want to delete corridor "${corridor.name}"? This action cannot be undone.`
    )
    if (!confirmed) return
    deleteMutation.mutate(corridor.corridor_id, {
      onSuccess: () => navigate('/corridors'),
    })
  }

  return (
    <div style={{ maxWidth: 700 }}>
      <Link
        to="/corridors"
        style={{
          color: 'var(--accent-primary)',
          textDecoration: 'none',
          fontSize: '0.8125rem',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.25rem',
          marginBottom: '0.75rem',
        }}
      >
        &larr; Back to Corridors
      </Link>

      {isLoading && <Spinner text="Loading corridor..." />}

      {error && (
        <Card>
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem', margin: 0 }}>
            Failed to load corridor details.
          </p>
        </Card>
      )}

      {corridor && (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '1rem',
            }}
          >
            <h2 style={{ margin: 0, fontSize: '1rem', color: 'var(--text-bright)' }}>
              {String(corridor.name)}
            </h2>

            {!editing ? (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  onClick={handleEdit}
                  style={{
                    ...btnBase,
                    background: 'var(--accent-primary)',
                    color: '#fff',
                  }}
                >
                  Edit
                </button>
                <button
                  onClick={handleDelete}
                  disabled={deleteMutation.isPending}
                  style={{
                    ...btnBase,
                    background: 'transparent',
                    color: 'var(--score-critical)',
                    border: '1px solid var(--score-critical)',
                    opacity: deleteMutation.isPending ? 0.6 : 1,
                  }}
                >
                  {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  onClick={handleCancel}
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
                  onClick={handleSave}
                  disabled={mutation.isPending}
                  style={{
                    ...btnBase,
                    background: 'var(--accent-primary)',
                    color: '#fff',
                    opacity: mutation.isPending ? 0.6 : 1,
                  }}
                >
                  {mutation.isPending ? 'Saving...' : 'Save'}
                </button>
              </div>
            )}
          </div>

          {mutation.isError && (
            <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', margin: '0 0 0.75rem' }}>
              Failed to update corridor. Please try again.
            </p>
          )}

          {deleteMutation.isError && (
            <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', margin: '0 0 0.75rem' }}>
              Failed to delete corridor. Please try again.
            </p>
          )}

          <Card>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
              {/* Name */}
              <div style={rowStyle}>
                <div style={labelStyle}>Name</div>
                {editing ? (
                  <input
                    type="text"
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    style={inputStyle}
                  />
                ) : (
                  <div style={valueStyle}>{String(corridor.name)}</div>
                )}
              </div>

              {/* Type (read-only) */}
              <div style={rowStyle}>
                <div style={labelStyle}>Type</div>
                <div style={valueStyle}>
                  {formatType(String(corridor.corridor_type ?? ''))}
                </div>
              </div>

              {/* Risk Weight */}
              <div style={rowStyle}>
                <div style={labelStyle}>Risk Weight</div>
                {editing ? (
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={editRiskWeight}
                    onChange={e => setEditRiskWeight(e.target.value)}
                    style={{ ...inputStyle, fontFamily: 'monospace' }}
                  />
                ) : (
                  <div style={{ ...valueStyle, fontFamily: 'monospace' }}>
                    {corridor.risk_weight != null
                      ? Number(corridor.risk_weight).toFixed(1)
                      : '-'}
                  </div>
                )}
              </div>

              {/* Jamming Zone */}
              <div style={rowStyle}>
                <div style={labelStyle}>Jamming Zone</div>
                {editing ? (
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
                      checked={editIsJammingZone}
                      onChange={e => setEditIsJammingZone(e.target.checked)}
                      style={{ accentColor: 'var(--accent-primary)' }}
                    />
                    {editIsJammingZone ? 'Yes' : 'No'}
                  </label>
                ) : (
                  <span
                    style={{
                      display: 'inline-block',
                      padding: '0.125rem 0.5rem',
                      borderRadius: 'var(--radius)',
                      fontSize: '0.75rem',
                      fontWeight: 600,
                      background: corridor.is_jamming_zone
                        ? 'rgba(239, 68, 68, 0.15)'
                        : 'rgba(255, 255, 255, 0.06)',
                      color: corridor.is_jamming_zone
                        ? 'var(--score-critical)'
                        : 'var(--text-dim)',
                      border: corridor.is_jamming_zone
                        ? '1px solid rgba(239, 68, 68, 0.3)'
                        : '1px solid var(--border)',
                      width: 'fit-content',
                    }}
                  >
                    {corridor.is_jamming_zone ? 'Yes' : 'No'}
                  </span>
                )}
              </div>

              {/* Description (full width) */}
              <div style={{ ...rowStyle, gridColumn: '1 / -1' }}>
                <div style={labelStyle}>Description</div>
                {editing ? (
                  <textarea
                    value={editDescription}
                    onChange={e => setEditDescription(e.target.value)}
                    rows={3}
                    style={{ ...inputStyle, resize: 'vertical' }}
                  />
                ) : (
                  <div style={{ ...valueStyle, color: 'var(--text-dim)' }}>
                    {String(corridor.description || 'No description')}
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* Alert stats */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginTop: '0.75rem' }}>
            <Card>
              <div style={labelStyle}>Alerts (7 days)</div>
              <div
                style={{
                  fontSize: '1.25rem',
                  fontWeight: 700,
                  fontFamily: 'monospace',
                  color: 'var(--text-bright)',
                  marginTop: '0.25rem',
                }}
              >
                {Number(corridor.alert_count_7d ?? 0)}
              </div>
            </Card>
            <Card>
              <div style={labelStyle}>Alerts (30 days)</div>
              <div
                style={{
                  fontSize: '1.25rem',
                  fontWeight: 700,
                  fontFamily: 'monospace',
                  color: 'var(--text-bright)',
                  marginTop: '0.25rem',
                }}
              >
                {Number(corridor.alert_count_30d ?? 0)}
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
