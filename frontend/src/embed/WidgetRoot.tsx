import React, { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getTheme, type WidgetTheme } from './widgetTheme'
import AlertTimelineWidget from './AlertTimelineWidget'
import RiskBreakdownWidget from './RiskBreakdownWidget'
import MapSnippetWidget from './MapSnippetWidget'

/** Read URL search params for widget configuration. */
function useWidgetParams() {
  return useMemo(() => {
    const params = new URLSearchParams(window.location.search)
    return {
      type: params.get('type') || 'summary',
      vessel: params.get('vessel') || '',
      theme: params.get('theme') || 'light',
      accent: params.get('accent') || '#60a5fa',
      size: params.get('size') || 'normal',
      apiKey: params.get('apiKey') || '',
      apiUrl: params.get('apiUrl') || '/api/v1',
    }
  }, [])
}

async function fetchWidget(apiUrl: string, vesselId: string, endpoint: string, apiKey: string) {
  const resp = await fetch(`${apiUrl}/embed/vessel/${vesselId}/${endpoint}`, {
    headers: { 'X-API-Key': apiKey },
  })
  if (!resp.ok) throw new Error(`API error ${resp.status}`)
  return resp.json()
}

function SummaryWidget({ data, theme }: { data: Record<string, unknown>; theme: WidgetTheme }) {
  const tier = (data.risk_tier as string) || 'unknown'
  const tierColor = theme.tierColors[tier] || theme.tierColors.unknown
  return (
    <div style={{ fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ fontSize: '1.2em', fontWeight: 700, marginBottom: 8, color: theme.text }}>
        {(data.name as string) || 'Unknown Vessel'}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <span style={{ color: theme.textSecondary }}>MMSI: {data.mmsi as string}</span>
        {data.imo && <span style={{ color: theme.textSecondary }}>IMO: {data.imo as string}</span>}
        {data.flag && <span style={{ color: theme.textSecondary }}>Flag: {data.flag as string}</span>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <span
          style={{
            display: 'inline-block',
            padding: '2px 10px',
            borderRadius: 12,
            background: tierColor,
            color: '#fff',
            fontWeight: 600,
            fontSize: '0.9em',
          }}
        >
          {tier.toUpperCase()}
        </span>
        {data.risk_score != null && (
          <span style={{ color: theme.textSecondary }}>Score: {data.risk_score as number}</span>
        )}
        {data.on_watchlist && (
          <span style={{ color: theme.tierColors.critical, fontWeight: 600 }}>Watchlisted</span>
        )}
      </div>
    </div>
  )
}

function ErrorMessage({ message, theme }: { message: string; theme: WidgetTheme }) {
  return <div style={{ color: theme.tierColors.critical, padding: 8 }}>{message}</div>
}

function LoadingSpinner({ theme }: { theme: WidgetTheme }) {
  return <div style={{ color: theme.textSecondary, padding: 8 }}>Loading...</div>
}

export default function WidgetRoot() {
  const params = useWidgetParams()
  const theme = useMemo(() => getTheme(params), [params])

  const endpointMap: Record<string, string> = {
    summary: 'summary',
    timeline: 'timeline',
    risk: 'risk',
    map: 'position',
  }

  const endpoint = endpointMap[params.type] || 'summary'

  const { data, isLoading, error } = useQuery({
    queryKey: ['embed', params.type, params.vessel],
    queryFn: () => fetchWidget(params.apiUrl, params.vessel, endpoint, params.apiKey),
    enabled: !!params.vessel && !!params.apiKey,
  })

  const containerStyle: React.CSSProperties = {
    background: theme.bg,
    color: theme.text,
    padding: theme.padding,
    fontSize: theme.fontSize,
    maxWidth: theme.width,
    borderRadius: 8,
    border: `1px solid ${theme.border}`,
    boxSizing: 'border-box',
    overflow: 'hidden',
  }

  if (!params.vessel) {
    return (
      <div style={containerStyle}>
        <ErrorMessage message="Missing vessel parameter" theme={theme} />
      </div>
    )
  }

  if (!params.apiKey) {
    return (
      <div style={containerStyle}>
        <ErrorMessage message="Missing apiKey parameter" theme={theme} />
      </div>
    )
  }

  if (isLoading) {
    return (
      <div style={containerStyle}>
        <LoadingSpinner theme={theme} />
      </div>
    )
  }

  if (error) {
    return (
      <div style={containerStyle}>
        <ErrorMessage message="Failed to load widget data" theme={theme} />
      </div>
    )
  }

  return (
    <div style={containerStyle} data-testid="widget-root">
      {params.type === 'summary' && <SummaryWidget data={data} theme={theme} />}
      {params.type === 'timeline' && <AlertTimelineWidget data={data} theme={theme} />}
      {params.type === 'risk' && <RiskBreakdownWidget data={data} theme={theme} />}
      {params.type === 'map' && <MapSnippetWidget data={data} theme={theme} />}
    </div>
  )
}
