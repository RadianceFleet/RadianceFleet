import { useState } from 'react'
import 'leaflet/dist/leaflet.css'
import { MapContainer, Marker, Popup } from 'react-leaflet'
import { useAlertMapPoints } from '../hooks/useAlerts'
import { MapLayerControl } from '../components/map/LayerControl'
import { CorridorZoneOverlay } from '../components/map/CorridorZoneOverlay'
import { Link } from 'react-router-dom'
import L from 'leaflet'
import { Spinner } from '../components/ui/Spinner'

function scoreIcon(score: number) {
  const hex =
    score >= 76 ? '#dc2626' :
    score >= 51 ? '#ea580c' :
    score >= 21 ? '#d97706' :
    '#16a34a'
  return L.divIcon({
    className: '',
    html: `<div style="width:14px;height:14px;background:${hex};border-radius:50%;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`,
    iconAnchor: [7, 7],
  })
}

export function MapOverviewPage() {
  const { data, isLoading } = useAlertMapPoints()
  const [showCorridors, setShowCorridors] = useState(true)

  const alerts = (data?.points ?? []).filter(a => a.last_lat != null && a.last_lon != null)

  return (
    <div style={{ height: 'calc(100vh - 120px)', borderRadius: 'var(--radius-md)', overflow: 'hidden', position: 'relative' }}>
      {isLoading && <Spinner text="Loading map data…" />}
      <div style={{
        position: 'absolute', top: 10, right: 10, zIndex: 1000,
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius)', padding: '6px 10px', fontSize: 12,
      }}>
        <label style={{ cursor: 'pointer', color: 'var(--text-body)' }}>
          <input
            type="checkbox"
            checked={showCorridors}
            onChange={e => setShowCorridors(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          Corridors
        </label>
      </div>
      <MapContainer
        center={[40, 25]}
        zoom={4}
        style={{ height: '100%', width: '100%' }}
        attributionControl={false}
      >
        <MapLayerControl />
        {showCorridors && <CorridorZoneOverlay />}

        {alerts.map(a => (
          <Marker
            key={a.gap_event_id}
            position={[a.last_lat!, a.last_lon!]}
            icon={scoreIcon(a.risk_score)}
          >
            <Popup>
              <div style={{ fontSize: 13, fontFamily: 'monospace' }}>
                <b>Alert #{a.gap_event_id}</b><br />
                Score: <b>{a.risk_score}</b><br />
                {a.vessel_name ?? 'Unknown vessel'}<br />
                {a.gap_start_utc.slice(0, 16).replace('T', ' ')} UTC<br />
                Duration: {(a.duration_minutes / 60).toFixed(1)}h<br />
                <Link to={`/alerts/${a.gap_event_id}`}>View details</Link>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  )
}
