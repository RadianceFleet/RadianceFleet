import type { VesselDetail, VesselAlias } from '../types/api'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { sectionHead, labelCell, valueCell, thStyle, tdStyle, tableStyle, theadRow, tbodyRow, flagRiskColors } from '../styles/tables'

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
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface VesselInfoSectionProps {
  vessel: VesselDetail
  aliases: VesselAlias[]
  totalGaps7d: number
  totalGaps30d: number
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function VesselInfoSection({ vessel, aliases, totalGaps7d, totalGaps30d }: VesselInfoSectionProps) {
  const name = vessel.name ?? 'Unknown vessel'
  const flagRisk = vessel.flag_risk_category ?? 'unknown'
  const piStatus = vessel.pi_coverage_status ?? 'unknown'

  return (
    <>
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
          <table style={tableStyle}>
            <thead>
              <tr style={theadRow}>
                <th style={thStyle}>MMSI</th>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Flag</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Absorbed At</th>
              </tr>
            </thead>
            <tbody>
              {aliases.map((a, i) => (
                <tr key={a.mmsi ?? i} style={tbodyRow}>
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
            <table style={tableStyle}>
              <thead>
                <tr style={theadRow}>
                  <th style={thStyle}>Source</th>
                  <th style={thStyle}>Reason</th>
                  <th style={thStyle}>Date Listed</th>
                  <th style={thStyle}>Active</th>
                </tr>
              </thead>
              <tbody>
                {vessel.watchlist_entries.map((w, i) => (
                  <tr key={w.watchlist_entry_id ?? i} style={tbodyRow}>
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
    </>
  )
}
