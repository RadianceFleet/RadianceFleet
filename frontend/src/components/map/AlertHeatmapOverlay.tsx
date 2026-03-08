import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.heat'
import { useAlertMapPoints } from '../../hooks/useAlerts'

// Extend L types for leaflet.heat
declare module 'leaflet' {
  function heatLayer(
    latlngs: Array<[number, number, number?]>,
    options?: Record<string, unknown>
  ): L.Layer
}

export function AlertHeatmapOverlay() {
  const map = useMap()
  const { data } = useAlertMapPoints()

  useEffect(() => {
    if (!data?.points?.length) return

    const heatData: [number, number, number][] = data.points
      .filter(p => p.last_lat != null && p.last_lon != null)
      .map(p => [p.last_lat!, p.last_lon!, (p.risk_score ?? 50) / 100])

    const heat = L.heatLayer(heatData, {
      radius: 25,
      blur: 15,
      maxZoom: 10,
      max: 1.0,
      gradient: {
        0.2: '#16a34a',
        0.4: '#d97706',
        0.6: '#ea580c',
        0.8: '#dc2626',
        1.0: '#7f1d1d',
      },
    })

    heat.addTo(map)
    return () => { map.removeLayer(heat) }
  }, [map, data])

  return null
}
