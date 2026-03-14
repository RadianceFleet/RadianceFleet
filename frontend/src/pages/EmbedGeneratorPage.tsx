import React, { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'

const WIDGET_TYPES = ['summary', 'timeline', 'risk', 'map'] as const
const THEMES = ['light', 'dark'] as const
const SIZES = ['compact', 'normal', 'large'] as const

const SIZE_DIMENSIONS: Record<string, { width: number; height: number }> = {
  compact: { width: 280, height: 250 },
  normal: { width: 400, height: 300 },
  large: { width: 600, height: 400 },
}

interface VesselOption {
  vessel_id: number
  name: string | null
  mmsi: string
}

export default function EmbedGeneratorPage() {
  const [vesselId, setVesselId] = useState('')
  const [widgetType, setWidgetType] = useState<(typeof WIDGET_TYPES)[number]>('summary')
  const [theme, setTheme] = useState<(typeof THEMES)[number]>('light')
  const [size, setSize] = useState<(typeof SIZES)[number]>('normal')
  const [accent, setAccent] = useState('#60a5fa')
  const [apiKey, setApiKey] = useState('')
  const [copied, setCopied] = useState(false)

  const { data: vessels } = useQuery<{ items: VesselOption[] }>({
    queryKey: ['vessels-list-embed'],
    queryFn: async () => {
      const resp = await fetch('/api/v1/vessels?limit=100')
      if (!resp.ok) throw new Error('Failed to load vessels')
      return resp.json()
    },
  })

  const baseUrl = window.location.origin
  const dims = SIZE_DIMENSIONS[size]

  const embedUrl = useMemo(() => {
    const params = new URLSearchParams({
      type: widgetType,
      vessel: vesselId,
      theme,
      size,
      accent,
      apiKey,
      apiUrl: `${baseUrl}/api/v1`,
    })
    return `${baseUrl}/embed/?${params.toString()}`
  }, [vesselId, widgetType, theme, size, accent, apiKey, baseUrl])

  const iframeCode = useMemo(() => {
    return `<iframe src="${embedUrl}" width="${dims.width}" height="${dims.height}" frameborder="0" style="border-radius:8px;border:1px solid #e5e7eb;"></iframe>`
  }, [embedUrl, dims])

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(iframeCode).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [iframeCode])

  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontWeight: 600,
    marginBottom: 4,
    fontSize: 14,
  }

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '6px 10px',
    borderRadius: 6,
    border: '1px solid #d1d5db',
    fontSize: 14,
    boxSizing: 'border-box',
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24, fontFamily: 'system-ui, sans-serif' }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 24 }}>
        Embed Widget Generator
      </h1>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
        {/* Configuration form */}
        <div>
          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Vessel</label>
            <select
              value={vesselId}
              onChange={(e) => setVesselId(e.target.value)}
              style={inputStyle}
              data-testid="vessel-select"
            >
              <option value="">Select a vessel...</option>
              {(vessels?.items || []).map((v) => (
                <option key={v.vessel_id} value={v.vessel_id}>
                  {v.name || v.mmsi} (ID: {v.vessel_id})
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Widget Type</label>
            <select
              value={widgetType}
              onChange={(e) => setWidgetType(e.target.value as typeof widgetType)}
              style={inputStyle}
              data-testid="type-select"
            >
              {WIDGET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Theme</label>
            <select
              value={theme}
              onChange={(e) => setTheme(e.target.value as typeof theme)}
              style={inputStyle}
            >
              {THEMES.map((t) => (
                <option key={t} value={t}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Size</label>
            <select
              value={size}
              onChange={(e) => setSize(e.target.value as typeof size)}
              style={inputStyle}
            >
              {SIZES.map((s) => (
                <option key={s} value={s}>
                  {s.charAt(0).toUpperCase() + s.slice(1)} ({SIZE_DIMENSIONS[s].width}px)
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Accent Color</label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="color"
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                style={{ width: 40, height: 32, border: 'none', cursor: 'pointer' }}
              />
              <input
                type="text"
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                style={{ ...inputStyle, flex: 1 }}
              />
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>API Key</label>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Your API key for widget auth"
              style={inputStyle}
              data-testid="apikey-input"
            />
          </div>

          {/* Embed code */}
          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle}>Embed Code</label>
            <textarea
              readOnly
              value={iframeCode}
              rows={4}
              style={{ ...inputStyle, fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }}
              data-testid="embed-code"
            />
            <button
              onClick={handleCopy}
              style={{
                marginTop: 8,
                padding: '8px 16px',
                borderRadius: 6,
                border: 'none',
                background: '#3b82f6',
                color: '#fff',
                cursor: 'pointer',
                fontWeight: 600,
              }}
              data-testid="copy-button"
            >
              {copied ? 'Copied!' : 'Copy to Clipboard'}
            </button>
          </div>
        </div>

        {/* Live preview */}
        <div>
          <label style={labelStyle}>Preview</label>
          <div
            style={{
              background: '#f3f4f6',
              padding: 16,
              borderRadius: 8,
              minHeight: 300,
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'center',
            }}
          >
            {vesselId && apiKey ? (
              <iframe
                src={embedUrl}
                width={dims.width}
                height={dims.height}
                frameBorder="0"
                style={{ borderRadius: 8, border: '1px solid #e5e7eb' }}
                title="Widget Preview"
                data-testid="preview-iframe"
              />
            ) : (
              <div style={{ color: '#6b7280', textAlign: 'center', paddingTop: 40 }}>
                Select a vessel and enter an API key to see the preview.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
