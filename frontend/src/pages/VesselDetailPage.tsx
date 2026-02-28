import { Link, useParams } from 'react-router-dom'
import { useVesselDetail, useVesselHistory, useVesselAliases } from '../hooks/useVessels'
import { useAlerts } from '../hooks/useAlerts'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { StatusBadge } from '../components/ui/StatusBadge'
import { EmptyState } from '../components/ui/EmptyState'
import { VesselTimeline } from '../components/VesselTimeline'

/* ------------------------------------------------------------------ */
/*  Shared styles (matching AlertDetail / AlertList conventions)       */
/* ------------------------------------------------------------------ */

const sectionHead: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 14,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 1,
}

const labelCell: React.CSSProperties = {
  color: 'var(--text-dim)',
  width: 200,
  fontSize: 13,
  paddingRight: 12,
  paddingBottom: 8,
  verticalAlign: 'top',
}

const valueCell: React.CSSProperties = { fontSize: 13, paddingBottom: 8 }

const thStyle: React.CSSProperties = {
  padding: '8px 12px',
  textAlign: 'left',
  fontWeight: 600,
  color: 'var(--text-muted)',
  whiteSpace: 'nowrap',
  fontSize: 12,
}

const tdStyle: React.CSSProperties = { padding: '8px 12px', fontSize: 13 }

const flagRiskColors: Record<string, string> = {
  high: 'var(--score-critical)',
  medium: 'var(--score-medium)',
  low: 'var(--score-low)',
  unknown: 'var(--text-dim)',
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '--'
  return ts.slice(0, 19).replace('T', ' ') + ' UTC'
}

function formatDate(d: string | null | undefined): string {
  if (!d) return '--'
  return d.slice(0, 10)
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function VesselDetailPage() {
  const { id } = useParams<{ id: string }>()

  const { data: vessel, isLoading, error } = useVesselDetail(id)
  const { data: history, isLoading: historyLoading } = useVesselHistory(id)
  const { data: aliasesData } = useVesselAliases(id)
  const { data: alertsData, isLoading: alertsLoading } = useAlerts({
    vessel_id: id,
    limit: 10,
    sort_by: 'risk_score',
    sort_order: 'desc',
  })

  /* Loading / error states */
  if (isLoading) return <Spinner text="Loading vessel..." />
  if (error || !vessel) {
    return (
      <p style={{ color: 'var(--score-critical)' }}>
        Vessel not found.{' '}
        <Link to="/vessels">Back to search</Link>
      </p>
    )
  }

  const name = vessel.name ?? 'Unknown vessel'
  const flagRisk = vessel.flag_risk_category ?? 'unknown'
  const piStatus = vessel.pi_coverage_status ?? 'unknown'
  const totalGaps7d = vessel.total_gaps_7d ?? 0
  const totalGaps30d = vessel.total_gaps_30d ?? 0

  const alerts = alertsData?.items ?? []
  const historyEntries = history ?? []
  const aliases = aliasesData?.aliases ?? []
  const absorbedCount = aliases.filter(a => a.status === 'absorbed').length

  return (
    <div style={{ maxWidth: 960 }}>
      {/* Breadcrumb */}
      <Link to="/vessels" style={{ fontSize: 13 }}>
        &larr; Vessel search
      </Link>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 0 4px' }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{name}</h2>
        {flagRisk !== 'unknown' && (
          <span style={{
            display: 'inline-block',
            padding: '2px 8px',
            borderRadius: 'var(--radius)',
            fontSize: 11,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            background: flagRiskColors[flagRisk] ?? 'var(--text-dim)',
            color: 'white',
          }}>
            {flagRisk} risk flag
          </span>
        )}
        {absorbedCount > 0 && (
          <span style={{
            display: 'inline-block',
            padding: '2px 8px',
            borderRadius: 'var(--radius)',
            fontSize: 11,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            background: 'var(--warning)',
            color: 'white',
          }}>
            MERGED ({absorbedCount} identit{absorbedCount === 1 ? 'y' : 'ies'})
          </span>
        )}
      </div>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        MMSI {vessel.mmsi ?? '?'} &middot; IMO {vessel.imo ?? '?'} &middot; {vessel.flag ?? '??'}
        {vessel.deadweight != null && ` · ${vessel.deadweight.toLocaleString()} DWT`}
      </p>

      {/* ---- Risk badge: gap counts ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>AIS Gap Summary</h3>
        <div style={{ display: 'flex', gap: 32 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Gaps (7 days)</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: totalGaps7d >= 3 ? 'var(--score-critical)' : totalGaps7d >= 1 ? 'var(--score-medium)' : 'var(--score-low)' }}>
              {totalGaps7d}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Gaps (30 days)</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: totalGaps30d >= 10 ? 'var(--score-critical)' : totalGaps30d >= 4 ? 'var(--score-medium)' : 'var(--score-low)' }}>
              {totalGaps30d}
            </div>
          </div>
        </div>
      </Card>

      {/* ---- Known Aliases card ---- */}
      {aliases.length > 1 && (
        <Card style={{ marginBottom: 16 }}>
          <h3 style={sectionHead}>Known Aliases (MMSI History)</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-base)' }}>
                <th style={thStyle}>MMSI</th>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Flag</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Absorbed At</th>
              </tr>
            </thead>
            <tbody>
              {aliases.map((a, i) => (
                <tr key={a.mmsi ?? i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{a.mmsi}</td>
                  <td style={tdStyle}>{a.name ?? '--'}</td>
                  <td style={tdStyle}>{a.flag ?? '--'}</td>
                  <td style={tdStyle}>
                    {a.status === 'current'
                      ? <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Current</span>
                      : <span style={{ color: 'var(--warning)' }}>Absorbed</span>}
                  </td>
                  <td style={tdStyle}>{a.absorbed_at ? formatTimestamp(a.absorbed_at) : '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* ---- Profile card ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Vessel Profile</h3>
        <table><tbody>
          <tr><td style={labelCell}>MMSI</td><td style={valueCell}>{vessel.mmsi ?? '--'}</td></tr>
          <tr><td style={labelCell}>IMO</td><td style={valueCell}>{vessel.imo ?? '--'}</td></tr>
          <tr><td style={labelCell}>Name</td><td style={valueCell}>{name}</td></tr>
          <tr><td style={labelCell}>Flag</td><td style={valueCell}>{vessel.flag ?? '--'}</td></tr>
          <tr><td style={labelCell}>Vessel Type</td><td style={valueCell}>{vessel.vessel_type ?? '--'}</td></tr>
          <tr><td style={labelCell}>Deadweight (DWT)</td><td style={valueCell}>{vessel.deadweight != null ? vessel.deadweight.toLocaleString() : '--'}</td></tr>
          <tr><td style={labelCell}>Year Built</td><td style={valueCell}>{vessel.year_built ?? '--'}</td></tr>
          <tr><td style={labelCell}>AIS Class</td><td style={valueCell}>{vessel.ais_class ?? '--'}</td></tr>
          <tr>
            <td style={labelCell}>Flag Risk Category</td>
            <td style={valueCell}>
              <span style={{ color: flagRiskColors[flagRisk] ?? 'var(--text-body)', fontWeight: 600 }}>
                {flagRisk.toUpperCase()}
              </span>
            </td>
          </tr>
          <tr>
            <td style={labelCell}>P&amp;I Coverage</td>
            <td style={valueCell}>
              <span style={{ color: piStatus === 'confirmed' ? 'var(--score-low)' : piStatus === 'lapsed' ? 'var(--score-critical)' : 'var(--text-muted)' }}>
                {piStatus.replace(/_/g, ' ')}
              </span>
            </td>
          </tr>
          <tr>
            <td style={labelCell}>PSC Detained (12 mo)</td>
            <td style={valueCell}>
              {vessel.psc_detained_last_12m
                ? <span style={{ color: 'var(--score-critical)', fontWeight: 600 }}>Yes</span>
                : <span style={{ color: 'var(--text-muted)' }}>No</span>}
            </td>
          </tr>
          <tr><td style={labelCell}>MMSI First Seen</td><td style={valueCell}>{formatTimestamp(vessel.mmsi_first_seen_utc)}</td></tr>
          <tr>
            <td style={labelCell}>Laid Up (30d / 60d)</td>
            <td style={valueCell}>
              {vessel.vessel_laid_up_30d ? <span style={{ color: 'var(--warning)' }}>Yes (30d)</span> : 'No'}
              {' / '}
              {vessel.vessel_laid_up_60d ? <span style={{ color: 'var(--warning)' }}>Yes (60d)</span> : 'No'}
              {vessel.vessel_laid_up_in_sts_zone && <span style={{ color: 'var(--score-critical)', marginLeft: 8 }}> (in STS zone)</span>}
            </td>
          </tr>
        </tbody></table>
      </Card>

      {/* ---- Watchlist entries ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Watchlist Entries</h3>
        {vessel.watchlist_entries.length === 0
          ? <EmptyState title="No watchlist entries" description="This vessel is not on any known watchlists." />
          : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>Source</th>
                  <th style={thStyle}>Reason</th>
                  <th style={thStyle}>Date Listed</th>
                  <th style={thStyle}>Active</th>
                </tr>
              </thead>
              <tbody>
                {vessel.watchlist_entries.map((w, i) => (
                  <tr key={w.watchlist_entry_id ?? i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={tdStyle}>
                      <span style={{ color: 'var(--score-critical)', fontWeight: 600 }}>
                        {w.watchlist_source}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {w.reason ?? '--'}
                    </td>
                    <td style={tdStyle}>{formatDate(w.date_listed)}</td>
                    <td style={tdStyle}>
                      {w.is_active
                        ? <span style={{ color: 'var(--score-critical)' }}>Active</span>
                        : <span style={{ color: 'var(--text-dim)' }}>Inactive</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Card>

      {/* ---- Identity history timeline ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Identity History</h3>
        {historyLoading && <Spinner text="Loading history..." />}
        {!historyLoading && historyEntries.length === 0 && (
          <EmptyState title="No identity changes recorded" />
        )}
        {!historyLoading && historyEntries.length > 0 && (
          <div style={{ position: 'relative', paddingLeft: 20 }}>
            {/* Vertical timeline line */}
            <div style={{
              position: 'absolute',
              left: 6,
              top: 4,
              bottom: 4,
              width: 2,
              background: 'var(--border)',
            }} />
            {historyEntries.map((h, i) => (
              <div key={h.vessel_history_id ?? i} style={{ position: 'relative', marginBottom: 16 }}>
                {/* Timeline dot */}
                <div style={{
                  position: 'absolute',
                  left: -17,
                  top: 4,
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: 'var(--accent)',
                  border: '2px solid var(--bg-card)',
                }} />
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 2 }}>
                  {formatTimestamp(h.observed_at)}
                </div>
                <div style={{ fontSize: 13 }}>
                  <span style={{ color: 'var(--text-muted)' }}>{h.field_changed}:</span>{' '}
                  <span style={{ color: 'var(--score-medium)', textDecoration: 'line-through' }}>
                    {h.old_value || '(none)'}
                  </span>
                  {' '}&rarr;{' '}
                  <span style={{ color: 'var(--accent)', fontWeight: 600 }}>
                    {h.new_value || '(none)'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* ---- Spoofing anomalies (30d) ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Spoofing Anomalies (30 days)</h3>
        {vessel.spoofing_anomalies_30d.length === 0
          ? <EmptyState title="No spoofing anomalies detected" />
          : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Type</th>
                  <th style={thStyle}>Start Time (UTC)</th>
                  <th style={thStyle}>Score Component</th>
                </tr>
              </thead>
              <tbody>
                {vessel.spoofing_anomalies_30d.map((s, i) => (
                  <tr key={s.anomaly_id ?? i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={tdStyle}>#{s.anomaly_id}</td>
                    <td style={tdStyle}>
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius)',
                        fontSize: 11,
                        background: 'var(--bg-base)',
                        border: '1px solid var(--border)',
                        color: 'var(--warning)',
                      }}>
                        {s.anomaly_type.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td style={tdStyle}>{formatTimestamp(s.start_time_utc)}</td>
                    <td style={tdStyle}>
                      <ScoreBadge score={s.risk_score_component} size="sm" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Card>

      {/* ---- Loitering events (30d) ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Loitering Events (30 days)</h3>
        {vessel.loitering_events_30d.length === 0
          ? <EmptyState title="No loitering events detected" />
          : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Start Time (UTC)</th>
                  <th style={thStyle}>Duration</th>
                  <th style={thStyle}>Corridor</th>
                </tr>
              </thead>
              <tbody>
                {vessel.loitering_events_30d.map((l, i) => (
                  <tr key={l.loiter_id ?? i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={tdStyle}>#{l.loiter_id}</td>
                    <td style={tdStyle}>{formatTimestamp(l.start_time_utc)}</td>
                    <td style={tdStyle}>
                      {l.duration_hours != null
                        ? `${l.duration_hours.toFixed(1)}h`
                        : '--'}
                    </td>
                    <td style={tdStyle}>
                      {l.corridor_id != null
                        ? <Link to={`/corridors/${l.corridor_id}`}>Corridor #{l.corridor_id}</Link>
                        : <span style={{ color: 'var(--text-dim)' }}>--</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Card>

      {/* ---- Activity Timeline ---- */}
      <VesselTimeline vesselId={id!} />

      {/* ---- STS events (60d) ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Ship-to-Ship Events (60 days)</h3>
        {vessel.sts_events_60d.length === 0
          ? <EmptyState title="No STS events detected" />
          : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Partner Vessel</th>
                  <th style={thStyle}>Start Time (UTC)</th>
                  <th style={thStyle}>Detection Type</th>
                </tr>
              </thead>
              <tbody>
                {vessel.sts_events_60d.map((s, i) => {
                  const partnerId = s.vessel_1_id === Number(id) ? s.vessel_2_id : s.vessel_1_id
                  return (
                    <tr key={s.sts_id ?? i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={tdStyle}>#{s.sts_id}</td>
                      <td style={tdStyle}>
                        <Link to={`/vessels/${partnerId}`}>Vessel #{partnerId}</Link>
                      </td>
                      <td style={tdStyle}>{formatTimestamp(s.start_time_utc)}</td>
                      <td style={tdStyle}>
                        <span style={{
                          display: 'inline-block',
                          padding: '2px 6px',
                          borderRadius: 'var(--radius)',
                          fontSize: 11,
                          background: 'var(--bg-base)',
                          border: '1px solid var(--border)',
                          color: 'var(--text-body)',
                        }}>
                          {s.detection_type.replace(/_/g, ' ')}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
      </Card>

      {/* ---- Gap alerts ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Recent Gap Alerts</h3>
        {alertsLoading && <Spinner text="Loading alerts..." />}
        {!alertsLoading && alerts.length === 0 && (
          <EmptyState title="No gap alerts" description="No AIS gap events recorded for this vessel." />
        )}
        {!alertsLoading && alerts.length > 0 && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Score</th>
                  <th style={thStyle}>Gap Start (UTC)</th>
                  <th style={thStyle}>Duration</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Flags</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map(a => (
                  <tr key={a.gap_event_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={tdStyle}>
                      <Link to={`/alerts/${a.gap_event_id}`}>#{a.gap_event_id}</Link>
                    </td>
                    <td style={tdStyle}><ScoreBadge score={a.risk_score} size="sm" /></td>
                    <td style={tdStyle}>{a.gap_start_utc.slice(0, 16).replace('T', ' ')}</td>
                    <td style={tdStyle}>{(a.duration_minutes / 60).toFixed(1)}h</td>
                    <td style={tdStyle}><StatusBadge status={a.status} /></td>
                    <td style={tdStyle}>
                      {a.impossible_speed_flag && <span title="Impossible speed">!! </span>}
                      {a.in_dark_zone && <span title="Dark zone">DZ</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(alertsData?.total ?? 0) > 10 && (
              <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text-muted)' }}>
                Showing 10 of {alertsData?.total} alerts.{' '}
                <Link to={`/alerts?vessel_id=${id}`}>View all</Link>
              </div>
            )}
          </>
        )}
      </Card>

      {/* Back link */}
      <Link
        to="/vessels"
        style={{
          display: 'inline-block',
          padding: '6px 14px',
          background: 'var(--bg-card)',
          color: 'var(--text-dim)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          fontSize: 13,
          marginTop: 8,
        }}
      >
        &larr; Back to vessel search
      </Link>
    </div>
  )
}
