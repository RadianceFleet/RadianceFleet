import { useState } from 'react'
import { useHuntTargets, useHuntMissions, useHuntCandidates } from '../hooks/useHunt'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { Pagination } from '../components/ui/Pagination'

const PAGE_SIZE = 20

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}
const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: '0.5rem 1rem',
  cursor: 'pointer',
  borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
  color: active ? 'var(--accent)' : 'var(--text-muted)',
  background: 'none',
  border: 'none',
  fontSize: '0.875rem',
  fontWeight: active ? 600 : 400,
})

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '-'
  return ts.slice(0, 16).replace('T', ' ')
}

type Tab = 'targets' | 'missions'

export function HuntPage() {
  const [tab, setTab] = useState<Tab>('missions')
  const [selectedMissionId, setSelectedMissionId] = useState<number | null>(null)

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Vessel Hunt
      </h2>

      {selectedMissionId != null ? (
        <MissionDetail missionId={selectedMissionId} onBack={() => setSelectedMissionId(null)} />
      ) : (
        <>
          <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', marginBottom: '1rem' }}>
            <button style={tabStyle(tab === 'missions')} onClick={() => setTab('missions')}>Missions</button>
            <button style={tabStyle(tab === 'targets')} onClick={() => setTab('targets')}>Targets</button>
          </div>

          {tab === 'targets' && <TargetsTab />}
          {tab === 'missions' && <MissionsTab onSelectMission={setSelectedMissionId} />}
        </>
      )}
    </div>
  )
}

function TargetsTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useHuntTargets({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const targets = data ?? []
  const totalPages = Math.max(1, Math.ceil(targets.length / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading targets..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load targets</p>}

      {targets.length === 0 && !isLoading && !error && (
        <EmptyState title="No hunt targets" description="Create a vessel target profile to begin hunting" />
      )}

      {targets.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Profile ID</th>
                  <th style={headStyle}>Vessel ID</th>
                  <th style={headStyle}>DWT</th>
                  <th style={headStyle}>LOA (m)</th>
                  <th style={headStyle}>Last Lat</th>
                  <th style={headStyle}>Last Lon</th>
                  <th style={headStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((t) => (
                  <tr key={t.profile_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{t.profile_id}</td>
                    <td style={cellStyle}>{t.vessel_id}</td>
                    <td style={cellStyle}>{t.deadweight_dwt ?? '-'}</td>
                    <td style={cellStyle}>{t.loa_meters ?? '-'}</td>
                    <td style={cellStyle}>{t.last_ais_position_lat?.toFixed(4) ?? '-'}</td>
                    <td style={cellStyle}>{t.last_ais_position_lon?.toFixed(4) ?? '-'}</td>
                    <td style={cellStyle}>{formatTimestamp(t.profile_created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pagination page={page} totalPages={totalPages} total={targets.length} onPageChange={setPage} label="targets" />
          )}
        </>
      )}
    </Card>
  )
}

function MissionsTab({ onSelectMission }: { onSelectMission: (id: number) => void }) {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useHuntMissions({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const missions = data ?? []
  const totalPages = Math.max(1, Math.ceil(missions.length / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading missions..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load missions</p>}

      {missions.length === 0 && !isLoading && !error && (
        <EmptyState title="No search missions" description="Create a search mission to find vessels" />
      )}

      {missions.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Mission ID</th>
                  <th style={headStyle}>Vessel ID</th>
                  <th style={headStyle}>Search Start</th>
                  <th style={headStyle}>Search End</th>
                  <th style={headStyle}>Status</th>
                  <th style={headStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {missions.map((m) => (
                  <tr
                    key={m.mission_id}
                    style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                    onClick={() => onSelectMission(m.mission_id)}
                  >
                    <td style={{ ...cellStyle, fontFamily: 'monospace', color: 'var(--accent)' }}>{m.mission_id}</td>
                    <td style={cellStyle}>{m.vessel_id}</td>
                    <td style={cellStyle}>{formatTimestamp(m.search_start_utc)}</td>
                    <td style={cellStyle}>{formatTimestamp(m.search_end_utc)}</td>
                    <td style={cellStyle}>{m.status}</td>
                    <td style={cellStyle}>{formatTimestamp(m.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pagination page={page} totalPages={totalPages} total={missions.length} onPageChange={setPage} label="missions" />
          )}
        </>
      )}
    </Card>
  )
}

function MissionDetail({ missionId, onBack }: { missionId: number; onBack: () => void }) {
  const [page, setPage] = useState(0)
  const { data: candidates, isLoading, error } = useHuntCandidates(missionId, { skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const items = candidates?.items ?? []
  const total = candidates?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <>
      <button
        onClick={onBack}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--accent)',
          cursor: 'pointer',
          fontSize: '0.875rem',
          marginBottom: '0.75rem',
          padding: 0,
        }}
      >
        &larr; Back to missions
      </button>

      <h3 style={{ margin: '0 0 1rem', fontSize: '0.9375rem', color: 'var(--text-muted)' }}>
        Mission #{missionId} &mdash; Candidates
      </h3>

      <Card>
        {isLoading && <Spinner text="Loading candidates..." />}
        {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load candidates</p>}

        {items.length === 0 && !isLoading && !error && (
          <EmptyState title="No candidates" description="Run analysis on this mission to generate candidates" />
        )}

        {items.length > 0 && (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--bg-base)' }}>
                    <th style={headStyle}>Candidate ID</th>
                    <th style={headStyle}>Score</th>
                    <th style={headStyle}>Similarity</th>
                    <th style={headStyle}>Length (m)</th>
                    <th style={headStyle}>Detected</th>
                    <th style={headStyle}>Review Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.candidate_id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{c.candidate_id}</td>
                      <td style={cellStyle}>{c.hunt_score?.toFixed(2) ?? '-'}</td>
                      <td style={cellStyle}>{c.visual_similarity_score?.toFixed(2) ?? '-'}</td>
                      <td style={cellStyle}>{c.length_estimate_m?.toFixed(1) ?? '-'}</td>
                      <td style={cellStyle}>{formatTimestamp(c.detection_time_utc)}</td>
                      <td style={cellStyle}>{c.analyst_review_status ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {totalPages > 1 && (
              <Pagination page={page} totalPages={totalPages} total={total} onPageChange={setPage} label="candidates" />
            )}
          </>
        )}
      </Card>
    </>
  )
}

export default HuntPage
