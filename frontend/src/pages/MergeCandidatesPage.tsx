import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMergeCandidates } from '../hooks/useVessels'
import { apiFetch } from '../lib/api'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { EmptyState } from '../components/ui/EmptyState'

const thStyle: React.CSSProperties = {
  padding: '8px 12px',
  textAlign: 'left',
  fontWeight: 600,
  color: 'var(--text-muted)',
  whiteSpace: 'nowrap',
  fontSize: 12,
}

const tdStyle: React.CSSProperties = { padding: '8px 12px', fontSize: 13 }

const statusColors: Record<string, string> = {
  pending: 'var(--warning)',
  auto_merged: 'var(--accent)',
  analyst_merged: 'var(--score-low)',
  rejected: 'var(--text-dim)',
}

export function MergeCandidatesPage() {
  const [statusFilter, setStatusFilter] = useState('pending')
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const { data, isLoading, error, refetch } = useMergeCandidates(statusFilter)
  const candidates = data?.items ?? []

  async function handleConfirm(candidateId: number) {
    setActionLoading(candidateId)
    setActionError(null)
    try {
      await apiFetch(`/merge-candidates/${candidateId}/confirm`, { method: 'POST' })
      refetch()
    } catch (err) {
      setActionError(`Failed to confirm #${candidateId}`)
    } finally {
      setActionLoading(null)
    }
  }

  async function handleReject(candidateId: number) {
    setActionLoading(candidateId)
    setActionError(null)
    try {
      await apiFetch(`/merge-candidates/${candidateId}/reject`, { method: 'POST' })
      refetch()
    } catch (err) {
      setActionError(`Failed to reject #${candidateId}`)
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Merge Candidates
      </h2>

      <Card style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Status:</span>
          {['pending', 'auto_merged', 'analyst_merged', 'rejected'].map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              style={{
                padding: '4px 10px',
                fontSize: 12,
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                background: statusFilter === s ? 'var(--accent)' : 'var(--bg-base)',
                color: statusFilter === s ? 'white' : 'var(--text-body)',
                cursor: 'pointer',
              }}
            >
              {s.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      </Card>

      <Card>
        {isLoading && <Spinner text="Loading merge candidates..." />}
        {error && <p style={{ color: 'var(--score-critical)' }}>Failed to load candidates</p>}
        {actionError && <p style={{ color: 'var(--score-critical)', fontSize: 13, padding: '0 12px' }}>{actionError}</p>}

        {!isLoading && candidates.length === 0 && (
          <EmptyState
            title="No merge candidates"
            description={`No candidates with status "${statusFilter}".`}
          />
        )}

        {candidates.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Vessel A (went dark)</th>
                  <th style={thStyle}>Vessel B (appeared)</th>
                  <th style={thStyle}>Distance</th>
                  <th style={thStyle}>Gap</th>
                  <th style={thStyle}>Confidence</th>
                  <th style={thStyle}>SAR</th>
                  <th style={thStyle}>Status</th>
                  {statusFilter === 'pending' && <th style={thStyle}>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {candidates.map(c => (
                  <tr key={c.candidate_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={tdStyle}>#{c.candidate_id}</td>
                    <td style={tdStyle}>
                      <Link to={`/vessels/${c.vessel_a.vessel_id}`} style={{ color: 'var(--accent)' }}>
                        {c.vessel_a.mmsi ?? '?'}
                      </Link>
                      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                        {c.vessel_a.name ?? '--'}
                      </div>
                    </td>
                    <td style={tdStyle}>
                      <Link to={`/vessels/${c.vessel_b.vessel_id}`} style={{ color: 'var(--accent)' }}>
                        {c.vessel_b.mmsi ?? '?'}
                      </Link>
                      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                        {c.vessel_b.name ?? '--'}
                      </div>
                    </td>
                    <td style={{ ...tdStyle, fontFamily: 'monospace' }}>
                      {c.distance_nm != null ? `${c.distance_nm.toFixed(1)} nm` : '--'}
                    </td>
                    <td style={{ ...tdStyle, fontFamily: 'monospace' }}>
                      {c.time_delta_hours != null ? `${c.time_delta_hours.toFixed(1)}h` : '--'}
                    </td>
                    <td style={tdStyle}>
                      <ScoreBadge score={c.confidence_score} size="sm" />
                    </td>
                    <td style={tdStyle}>
                      {c.satellite_corroboration
                        ? <span style={{ color: 'var(--score-low)' }}>Yes</span>
                        : <span style={{ color: 'var(--text-dim)' }}>--</span>}
                    </td>
                    <td style={tdStyle}>
                      <span style={{ color: statusColors[c.status] ?? 'var(--text-body)', fontWeight: 600, fontSize: 11, textTransform: 'uppercase' }}>
                        {c.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    {statusFilter === 'pending' && (
                      <td style={tdStyle}>
                        <button
                          onClick={() => handleConfirm(c.candidate_id)}
                          disabled={actionLoading === c.candidate_id}
                          style={{
                            padding: '3px 8px', fontSize: 11, background: 'var(--score-low)',
                            color: 'white', border: 'none', borderRadius: 'var(--radius)',
                            cursor: actionLoading === c.candidate_id ? 'wait' : 'pointer',
                            marginRight: 4,
                            opacity: actionLoading === c.candidate_id ? 0.6 : 1,
                          }}
                        >
                          {actionLoading === c.candidate_id ? 'Working...' : 'Confirm'}
                        </button>
                        <button
                          onClick={() => handleReject(c.candidate_id)}
                          disabled={actionLoading === c.candidate_id}
                          style={{
                            padding: '3px 8px', fontSize: 11, background: 'var(--bg-base)',
                            color: 'var(--text-body)', border: '1px solid var(--border)',
                            borderRadius: 'var(--radius)',
                            cursor: actionLoading === c.candidate_id ? 'wait' : 'pointer',
                            opacity: actionLoading === c.candidate_id ? 0.6 : 1,
                          }}
                        >
                          Reject
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data?.total != null && data.total > 20 && (
          <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text-muted)' }}>
            Showing 20 of {data.total} candidates.
          </div>
        )}
      </Card>
    </div>
  )
}
