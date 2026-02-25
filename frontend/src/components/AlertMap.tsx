import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { MapContainer, TileLayer, Marker, Popup, GeoJSON, Polyline } from 'react-leaflet'
import type { AISPointSummary, MovementEnvelope } from '../types/api'

// Fix Leaflet default icon paths broken by Vite bundler (use CDN fallback)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
delete (L.Icon.Default.prototype as any)._getIconUrl
L.Icon.Default.mergeOptions({
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

function dotIcon(color: string) {
  return L.divIcon({
    className: '',
    html: `<div style="width:12px;height:12px;background:${color};border-radius:50%;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`,
    iconAnchor: [6, 6],
  })
}

interface Props {
  lastPoint: AISPointSummary | null
  firstPointAfter: AISPointSummary | null
  envelope: MovementEnvelope | null
}

export function AlertMap({ lastPoint, firstPointAfter, envelope }: Props) {
  if (!lastPoint && !firstPointAfter) {
    return (
      <div style={{
        height: 280, background: '#1e293b', borderRadius: 8,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#64748b', fontSize: 13, marginBottom: 16,
      }}>
        No AIS boundary points — map unavailable
      </div>
    )
  }

  const center: [number, number] = lastPoint
    ? [lastPoint.lat, lastPoint.lon]
    : [firstPointAfter!.lat, firstPointAfter!.lon]

  const trackLine = envelope?.interpolated_positions_json?.map(
    p => [p.lat, p.lon] as [number, number]
  ) ?? null

  return (
    <div style={{ height: 280, borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
      <MapContainer center={center} zoom={6} style={{ height: '100%', width: '100%' }} attributionControl={false}>
        <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />

        {lastPoint && (
          <Marker position={[lastPoint.lat, lastPoint.lon]} icon={dotIcon('#16a34a')}>
            <Popup>
              <b>Last known position</b><br />
              {lastPoint.timestamp_utc.slice(0, 16)}<br />
              SOG: {lastPoint.sog ?? '?'} kn
            </Popup>
          </Marker>
        )}

        {firstPointAfter && (
          <Marker position={[firstPointAfter.lat, firstPointAfter.lon]} icon={dotIcon('#dc2626')}>
            <Popup>
              <b>First position after gap</b><br />
              {firstPointAfter.timestamp_utc.slice(0, 16)}<br />
              SOG: {firstPointAfter.sog ?? '?'} kn
            </Popup>
          </Marker>
        )}

        {envelope?.confidence_ellipse_geojson && (
          <GeoJSON
            data={envelope.confidence_ellipse_geojson as GeoJSON.GeoJsonObject}
            style={{ color: '#3b82f6', weight: 1.5, fillColor: '#3b82f6', fillOpacity: 0.12 }}
          />
        )}

        {trackLine && trackLine.length > 1 && (
          <Polyline positions={trackLine} color="#f59e0b" weight={1.5} dashArray="4 4" opacity={0.7} />
        )}
      </MapContainer>
    </div>
  )
}
