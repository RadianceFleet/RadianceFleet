import { Link, useParams } from 'react-router-dom'
import { useVesselDetail, useVesselHistory, useVesselAliases } from '../hooks/useVessels'
import { useAlerts } from '../hooks/useAlerts'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { StatusBadge } from '../components/ui/StatusBadge'
import { EmptyState } from '../components/ui/EmptyState'
import { VerificationPanel } from '../components/VerificationPanel'
import { VerificationBadge } from '../components/VerificationBadge'
import { VesselTimeline } from '../components/VesselTimeline'
import { TipForm } from '../components/TipForm'
import { RouteLaunderingSection } from '../components/RouteLaunderingSection'
import { SubscribeForm } from '../components/SubscribeForm'
import { VesselInfoSection } from './VesselInfoSection'
import { VesselIdentityTimeline } from './VesselIdentityTimeline'
import { sectionHead, thStyle, tdStyle, tableStyle, theadRow, tbodyRow, flagRiskColors } from '../styles/tables'

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '--'
  return ts.slice(0, 19).replace('T', ' ') + ' UTC'
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
        <button
          onClick={() => navigator.clipboard.writeText(window.location.href)}
          style={{ fontSize: 12, padding: '4px 10px', border: '1px solid var(--border)', borderRadius: 'var(--radius)', cursor: 'pointer', background: 'transparent', color: 'var(--text-muted)' }}
          title="Copy link to clipboard"
        >
          Share
        </button>
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
        <VerificationBadge verifiedBy={vessel.owner_name} />
      </div>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        MMSI {vessel.mmsi ?? '?'} &middot; IMO {vessel.imo ?? '?'} &middot; {vessel.flag ?? '??'}
        {vessel.deadweight != null && ` · ${vessel.deadweight.toLocaleString()} DWT`}
      </p>

      {/* Extracted: gap summary, aliases, profile, watchlist */}
      <VesselInfoSection
        vessel={vessel}
        aliases={aliases}
        totalGaps7d={totalGaps7d}
        totalGaps30d={totalGaps30d}
      />

      {/* ---- Ownership Verification ---- */}
      <VerificationPanel vesselId={id!} currentOwner={vessel.owner_name} />

      {/* Extracted: identity history timeline */}
      <VesselIdentityTimeline historyEntries={historyEntries} isLoading={historyLoading} />

      {/* ---- Spoofing anomalies (30d) ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Spoofing Anomalies (30 days)</h3>
        {vessel.spoofing_anomalies_30d.length === 0
          ? <EmptyState title="No spoofing anomalies detected" />
          : (
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Type</th>
                  <th style={thStyle}>Start Time (UTC)</th>
                  <th style={thStyle}>Score Component</th>
                </tr>
              </thead>
              <tbody>
                {vessel.spoofing_anomalies_30d.map((s, i) => (
                  <tr key={s.anomaly_id ?? i} style={tbodyRow}>
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
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
                  <th style={thStyle}>ID</th>
                  <th style={thStyle}>Start Time (UTC)</th>
                  <th style={thStyle}>Duration</th>
                  <th style={thStyle}>Corridor</th>
                </tr>
              </thead>
              <tbody>
                {vessel.loitering_events_30d.map((l, i) => (
                  <tr key={l.loiter_id ?? i} style={tbodyRow}>
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
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
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
                    <tr key={s.sts_id ?? i} style={tbodyRow}>
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

      {/* ---- Route Laundering ---- */}
      <RouteLaunderingSection vesselId={id!} />

      {/* ---- Gap alerts ---- */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Recent Gap Alerts</h3>
        {alertsLoading && <Spinner text="Loading alerts..." />}
        {!alertsLoading && alerts.length === 0 && (
          <EmptyState title="No gap alerts" description="No AIS gap events recorded for this vessel." />
        )}
        {!alertsLoading && alerts.length > 0 && (
          <>
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
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
                  <tr key={a.gap_event_id} style={tbodyRow}>
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

      {/* Tip and Subscribe forms */}
      {vessel && (
        <div style={{ marginTop: 24 }}>
          <div style={{ marginBottom: 16 }}>
            <SubscribeForm mmsi={vessel.mmsi} label={`Get alerts for ${vessel.name || vessel.mmsi}`} />
          </div>
          <TipForm mmsi={vessel.mmsi} vesselName={vessel.name || vessel.mmsi} />
        </div>
      )}

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
