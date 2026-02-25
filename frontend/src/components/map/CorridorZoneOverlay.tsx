import { GeoJSON } from 'react-leaflet'
import { useCorridorGeoJSON } from '../../hooks/useCorridors'
import type { PathOptions } from 'leaflet'

const CORRIDOR_COLORS: Record<string, string> = {
  export_route: '#f97316',    // orange
  sts_zone: '#ef4444',        // red
  dark_zone: '#a855f7',       // purple
  import_route: '#3b82f6',    // blue
}

function corridorStyle(feature: GeoJSON.Feature | undefined): PathOptions {
  const type = feature?.properties?.corridor_type ?? ''
  const isJamming = feature?.properties?.is_jamming_zone
  return {
    color: isJamming ? '#a855f7' : (CORRIDOR_COLORS[type] ?? '#64748b'),
    weight: 2,
    opacity: 0.7,
    fillOpacity: 0.12,
  }
}

export function CorridorZoneOverlay() {
  const { data: geojson } = useCorridorGeoJSON()

  if (!geojson || geojson.features.length === 0) return null

  return (
    <GeoJSON
      key={geojson.features.length}
      data={geojson}
      style={corridorStyle}
      onEachFeature={(feature, layer) => {
        const props = feature.properties
        if (props) {
          layer.bindTooltip(
            `${props.name ?? 'Unknown'} (${(props.corridor_type ?? '').replace(/_/g, ' ')})`,
            { sticky: true }
          )
        }
      }}
    />
  )
}
