import React from 'react'
import { MapContainer, TileLayer, Marker } from 'react-leaflet'
import type { WidgetTheme } from './widgetTheme'
import 'leaflet/dist/leaflet.css'

// Fix Leaflet default icon issue in bundled builds
import L from 'leaflet'

const defaultIcon = L.icon({
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
})

interface PositionData {
  vessel_id: number
  lat: number | null
  lon: number | null
  timestamp: string | null
  sog: number | null
  cog: number | null
}

interface Props {
  data: PositionData
  theme: WidgetTheme
}

export default function MapSnippetWidget({ data, theme }: Props) {
  if (data.lat == null || data.lon == null) {
    return (
      <div style={{ color: theme.textSecondary, padding: 8, fontFamily: 'system-ui, sans-serif' }}>
        No position data available.
      </div>
    )
  }

  const position: [number, number] = [data.lat, data.lon]

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif' }}>
      <MapContainer
        center={position}
        zoom={7}
        style={{ height: 200, width: '100%', borderRadius: 6 }}
        zoomControl={false}
        dragging={false}
        scrollWheelZoom={false}
        doubleClickZoom={false}
        touchZoom={false}
        attributionControl={false}
      >
        <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <Marker position={position} icon={defaultIcon} />
      </MapContainer>
      {data.timestamp && (
        <div style={{ color: theme.textSecondary, fontSize: '0.8em', marginTop: 4, textAlign: 'center' }}>
          Last seen: {new Date(data.timestamp).toLocaleString()}
          {data.sog != null && ` | SOG: ${data.sog} kn`}
        </div>
      )}
    </div>
  )
}
