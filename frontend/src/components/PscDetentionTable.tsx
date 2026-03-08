import { useState } from 'react'
import type { PscDetention } from '../types/api'

interface Props {
  detentions: PscDetention[]
  detentionCount?: number
  latestDate?: string | null
}

export default function PscDetentionTable({ detentions, detentionCount, latestDate }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (!detentions || detentions.length === 0) {
    return <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>No PSC detentions on record</div>
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          fontSize: 13, fontWeight: 500, color: 'var(--accent)',
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
        }}
      >
        {expanded ? '\u25BC' : '\u25B6'} PSC Detentions ({detentionCount ?? detentions.length})
        {latestDate && <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>Latest: {latestDate}</span>}
      </button>
      {expanded && (
        <table style={{ marginTop: 8, width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>Date</th>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>MOU</th>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>Port</th>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>Country</th>
              <th style={{ textAlign: 'right', padding: '4px 8px' }}>Deficiencies</th>
              <th style={{ textAlign: 'left', padding: '4px 8px' }}>Reason</th>
            </tr>
          </thead>
          <tbody>
            {detentions.map((d) => (
              <tr key={d.psc_detention_id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 8px' }}>{d.detention_date}</td>
                <td style={{ padding: '4px 8px' }}>{d.mou_source}</td>
                <td style={{ padding: '4px 8px' }}>{d.port_name || '\u2014'}</td>
                <td style={{ padding: '4px 8px' }}>{d.port_country || '\u2014'}</td>
                <td style={{ padding: '4px 8px', textAlign: 'right' }}>
                  {d.deficiency_count} ({d.major_deficiency_count} major)
                </td>
                <td style={{ padding: '4px 8px', color: 'var(--text-dim)' }}>
                  {d.detention_reason || '\u2014'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
