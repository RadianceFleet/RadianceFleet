import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { MapContainer, Marker, Popup, GeoJSON, Polyline } from 'react-leaflet'
import type { AISPointSummary, MovementEnvelope } from '../types/api'
import { useCorridorDetail } from '../hooks/useCorridors'
import { MapLayerControl } from './map/LayerControl'

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
  corridorId?: number
}

function CorridorOverlay({ corridorId }: { corridorId: number }) {
  const { data: corridor } = useCorridorDetail(String(corridorId))
  const geometry = (corridor as Record<string, unknown> | undefined)?.geometry as GeoJSON.GeoJsonObject | undefined
  if (!geometry) return null
  return (
    <GeoJSON
      data={geometry}
      style={{ color: '#f59e0b', weight: 2, fillColor: '#f59e0b', fillOpacity: 0.08, dashArray: '6 4' }}
    />
  )
}

export function AlertMap({ lastPoint, firstPointAfter, envelope, corridorId }: Props) {
  if (!lastPoint && !firstPointAfter) {
    return (
      <div style={{
        height: 280, background: 'var(--bg-card)', borderRadius: 'var(--radius-md)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-dim)', fontSize: 13, marginBottom: 16,
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
    <div style={{ height: 280, borderRadius: 'var(--radius-md)', overflow: 'hidden', marginBottom: 16 }}>
      <MapContainer center={center} zoom={6} style={{ height: '100%', width: '100%' }} attributionControl={false}>
        <MapLayerControl />

        {corridorId != null && <CorridorOverlay corridorId={corridorId} />}

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
