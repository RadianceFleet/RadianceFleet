import { useState } from 'react'
import type { ExportResponse } from '../types/api'
import { apiFetch } from '../lib/api'
import { btnStyle } from '../styles/tables'

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface AlertExportPanelProps {
  alertId: string
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function AlertExportPanel({ alertId }: AlertExportPanelProps) {
  const [exportError, setExportError] = useState<string | null>(null)
  const [satLoading, setSatLoading] = useState(false)
  const [satResult, setSatResult] = useState<string | null>(null)

  const handleExport = async (fmt: 'md' | 'json') => {
    setExportError(null)
    try {
      const data = await apiFetch<ExportResponse>(`/alerts/${alertId}/export?format=${fmt}`, { method: 'POST' })
      const content = data.content ?? JSON.stringify(data, null, 2)
      const blob = new Blob([content], { type: fmt === 'json' ? 'application/json' : 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `alert_${alertId}.${fmt}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  const handleSatelliteCheck = async () => {
    setSatLoading(true)
    setSatResult(null)
    try {
      await apiFetch(`/alerts/${alertId}/satellite-check`, { method: 'POST' })
      setSatResult('Satellite check prepared')
    } catch (err) {
      setSatResult(err instanceof Error ? err.message : 'Failed to prepare satellite check')
    } finally {
      setSatLoading(false)
    }
  }

  return (
    <>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button onClick={() => handleExport('md')} style={{ ...btnStyle, background: 'var(--bg-base)', color: 'var(--accent)' }}>
          Export Markdown
        </button>
        <button onClick={() => handleExport('json')} style={{ ...btnStyle, background: 'var(--bg-base)', color: 'var(--accent)' }}>
          Export JSON
        </button>
        <button
          onClick={handleSatelliteCheck}
          disabled={satLoading}
          style={{ ...btnStyle, background: 'var(--bg-base)', color: 'var(--warning)', opacity: satLoading ? 0.6 : 1 }}
        >
          {satLoading ? 'Preparing…' : 'Prepare satellite check'}
        </button>
      </div>
      {exportError && (
        <p style={{ fontSize: 12, color: 'var(--score-critical)', marginTop: 8 }}>{exportError}</p>
      )}
      {satResult && (
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>{satResult}</p>
      )}
      <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 12 }}>
        Note: export requires status ≠ "new" (analyst review gate — NFR7)
      </p>
    </>
  )
}
