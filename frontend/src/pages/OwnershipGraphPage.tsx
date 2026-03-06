import { useFleetClusters, useFleetClusterDetail } from '../hooks/useFleet'
import { useState } from 'react'
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

export function OwnershipGraphPage() {
  const { data, isLoading, error } = useFleetClusters(200)
  const clusters = data?.clusters ?? []
  const unavailable = error && !isLoading
  const [expandedId, setExpandedId] = useState<number | null>(null)

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 0.5rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Ownership Graph
      </h2>
      <p style={{ margin: '0 0 1rem', fontSize: 13, color: 'var(--text-dim)' }}>
        Shell chain hierarchical view of owner clusters. Click a row to expand members.
      </p>

      <Card>
        {isLoading && <Spinner text="Loading ownership data..." />}
        {unavailable && (
          <EmptyState
            title="Data not available"
            description="Ownership graph data is not yet available. Run owner deduplication to generate clusters."
          />
        )}
        {!isLoading && !unavailable && clusters.length === 0 && (
          <EmptyState title="No ownership clusters" description="No owner clusters found." />
        )}
        {clusters.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={{ ...headStyle, width: 30 }}></th>
                  <th style={headStyle}>Canonical Name</th>
                  <th style={headStyle}>Country</th>
                  <th style={headStyle}>Sanctioned</th>
                  <th style={headStyle}>Vessels</th>
                </tr>
              </thead>
              <tbody>
                {clusters.map(c => (
                  <ClusterRow
                    key={c.cluster_id}
                    clusterId={c.cluster_id}
                    name={c.canonical_name}
                    country={c.country}
                    isSanctioned={c.is_sanctioned}
                    vesselCount={c.vessel_count}
                    expanded={expandedId === c.cluster_id}
                    onToggle={() => setExpandedId(expandedId === c.cluster_id ? null : c.cluster_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

function ClusterRow({
  clusterId, name, country, isSanctioned, vesselCount, expanded, onToggle,
}: {
  clusterId: number
  name: string
  country: string | null
  isSanctioned: boolean
  vesselCount: number
  expanded: boolean
  onToggle: () => void
}) {
  const { data: detail, isLoading } = useFleetClusterDetail(expanded ? clusterId : undefined)

  return (
    <>
      <tr
        style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
        onClick={onToggle}
      >
        <td style={{ ...cellStyle, textAlign: 'center', fontSize: 11 }}>
          {expanded ? '\u25BC' : '\u25B6'}
        </td>
        <td style={{ ...cellStyle, fontWeight: 600 }}>
          {isSanctioned && (
            <span style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--score-critical)',
              marginRight: 6,
            }} />
          )}
          {name}
        </td>
        <td style={cellStyle}>{country ?? '-'}</td>
        <td style={cellStyle}>
          {isSanctioned
            ? <span style={{ color: 'var(--score-critical)', fontWeight: 600 }}>YES</span>
            : <span style={{ color: 'var(--text-dim)' }}>No</span>}
        </td>
        <td style={{ ...cellStyle, textAlign: 'right' }}>{vesselCount}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} style={{ padding: 0, background: 'var(--bg-base)' }}>
            <div style={{ padding: '8px 16px 8px 40px' }}>
              {isLoading && <Spinner text="Loading members..." />}
              {detail && detail.members.length === 0 && (
                <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>No members.</span>
              )}
              {detail && detail.members.length > 0 && (
                <div>
                  {detail.members.map((m, i) => (
                    <div key={m.member_id} style={{
                      display: 'flex',
                      alignItems: 'center',
                      padding: '4px 0',
                      fontSize: 13,
                      borderBottom: i < detail.members.length - 1 ? '1px solid var(--border)' : 'none',
                    }}>
                      <span style={{ color: 'var(--text-dim)', width: 20 }}>
                        {i === detail.members.length - 1 ? '\u2514' : '\u251C'}
                      </span>
                      <span style={{ flex: 1 }}>{m.owner_name ?? `Owner #${m.owner_id}`}</span>
                      <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                        {m.similarity_score != null ? `${(m.similarity_score * 100).toFixed(0)}% match` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
