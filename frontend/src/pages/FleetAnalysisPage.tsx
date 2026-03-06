import { useState } from 'react'
import { useFleetAlerts, useFleetClusters, useFleetClusterDetail } from '../hooks/useFleet'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}

const sectionHead: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 14,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 1,
}

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '-'
  return ts.slice(0, 16).replace('T', ' ')
}

export function FleetAnalysisPage() {
  const { data: alertsData, isLoading: alertsLoading, error: alertsError } = useFleetAlerts()
  const { data: clustersData, isLoading: clustersLoading, error: clustersError } = useFleetClusters()
  const [selectedClusterId, setSelectedClusterId] = useState<number | undefined>(undefined)
  const { data: clusterDetail, isLoading: detailLoading } = useFleetClusterDetail(selectedClusterId)

  const alerts = alertsData?.alerts ?? []
  const clusters = clustersData?.items ?? []
  const alertsUnavailable = alertsError && !alertsLoading
  const clustersUnavailable = clustersError && !clustersLoading

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Fleet Analysis
      </h2>

      {/* Fleet Alerts */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Fleet Alerts</h3>
        {alertsLoading && <Spinner text="Loading fleet alerts..." />}
        {alertsUnavailable && (
          <EmptyState title="Data not available" description="Fleet alerts endpoint is not yet available. Run fleet pattern detection first." />
        )}
        {!alertsLoading && !alertsUnavailable && alerts.length === 0 && (
          <EmptyState title="No fleet alerts" description="No fleet-level alerts have been generated yet." />
        )}
        {alerts.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>ID</th>
                  <th style={headStyle}>Type</th>
                  <th style={headStyle}>Cluster</th>
                  <th style={headStyle}>Vessels</th>
                  <th style={headStyle}>Risk Score</th>
                  <th style={headStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map(a => (
                  <tr key={a.alert_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{a.alert_id}</td>
                    <td style={cellStyle}>
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius)',
                        fontSize: 11,
                        background: 'var(--bg-base)',
                        border: '1px solid var(--border)',
                        color: 'var(--warning)',
                      }}>
                        {a.alert_type.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td style={cellStyle}>
                      {a.owner_cluster_id != null ? (
                        <button
                          onClick={() => setSelectedClusterId(a.owner_cluster_id!)}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: 'var(--accent)',
                            cursor: 'pointer',
                            padding: 0,
                            fontSize: 'inherit',
                          }}
                        >
                          Cluster #{a.owner_cluster_id}
                        </button>
                      ) : '-'}
                    </td>
                    <td style={cellStyle}>
                      {Array.isArray(a.vessel_ids) ? a.vessel_ids.length : '-'}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontWeight: 600 }}>
                      {a.risk_score_component ?? '-'}
                    </td>
                    <td style={{ ...cellStyle, whiteSpace: 'nowrap' }}>
                      {formatTimestamp(a.created_utc)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Owner Clusters */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Owner Clusters</h3>
        {clustersLoading && <Spinner text="Loading clusters..." />}
        {clustersUnavailable && (
          <EmptyState title="Data not available" description="Owner clusters endpoint is not yet available. Run owner deduplication first." />
        )}
        {!clustersLoading && !clustersUnavailable && clusters.length === 0 && (
          <EmptyState title="No owner clusters" description="Run owner deduplication to generate clusters." />
        )}
        {clusters.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Cluster</th>
                  <th style={headStyle}>Canonical Name</th>
                  <th style={headStyle}>Country</th>
                  <th style={headStyle}>Sanctioned</th>
                  <th style={headStyle}>Vessels</th>
                </tr>
              </thead>
              <tbody>
                {clusters.map(c => (
                  <tr
                    key={c.cluster_id}
                    style={{
                      borderBottom: '1px solid var(--border)',
                      cursor: 'pointer',
                      background: selectedClusterId === c.cluster_id ? 'rgba(96, 165, 250, 0.1)' : 'transparent',
                    }}
                    onClick={() => setSelectedClusterId(c.cluster_id)}
                  >
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>#{c.cluster_id}</td>
                    <td style={{ ...cellStyle, fontWeight: 600 }}>{c.canonical_name}</td>
                    <td style={cellStyle}>{c.country ?? '-'}</td>
                    <td style={cellStyle}>
                      {c.is_sanctioned
                        ? <span style={{ color: 'var(--score-critical)', fontWeight: 600 }}>YES</span>
                        : <span style={{ color: 'var(--text-dim)' }}>No</span>}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>{c.vessel_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Cluster Detail Drill-down */}
      {selectedClusterId != null && (
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ ...sectionHead, margin: 0 }}>
              Cluster #{selectedClusterId} Detail
            </h3>
            <button
              onClick={() => setSelectedClusterId(undefined)}
              style={{
                background: 'none',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '4px 10px',
                fontSize: 12,
              }}
            >
              Close
            </button>
          </div>
          {detailLoading && <Spinner text="Loading cluster details..." />}
          {clusterDetail && (
            <>
              <div style={{ fontSize: 13, marginBottom: 12 }}>
                <strong>{clusterDetail.canonical_name}</strong>
                {clusterDetail.country && <span style={{ color: 'var(--text-dim)' }}> ({clusterDetail.country})</span>}
                {clusterDetail.is_sanctioned && (
                  <span style={{
                    marginLeft: 8,
                    padding: '2px 8px',
                    borderRadius: 'var(--radius)',
                    fontSize: 11,
                    fontWeight: 700,
                    background: 'var(--score-critical)',
                    color: 'white',
                  }}>
                    SANCTIONED
                  </span>
                )}
              </div>
              {clusterDetail.members.length > 0 ? (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-base)' }}>
                      <th style={headStyle}>Owner ID</th>
                      <th style={headStyle}>Owner Name</th>
                      <th style={headStyle}>Similarity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clusterDetail.members.map(m => (
                      <tr key={m.member_id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ ...cellStyle, fontFamily: 'monospace' }}>#{m.owner_id}</td>
                        <td style={cellStyle}>{m.owner_name ?? '-'}</td>
                        <td style={{ ...cellStyle, textAlign: 'right' }}>
                          {m.similarity_score != null ? `${(m.similarity_score * 100).toFixed(0)}%` : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="No members" description="This cluster has no linked owners." />
              )}
            </>
          )}
        </Card>
      )}
    </div>
  )
}
