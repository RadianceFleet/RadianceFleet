import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

export interface CoverageFeature {
  type: 'Feature'
  geometry: object | null
  properties: {
    name: string
    quality: string
    description: string
  }
}

export interface CoverageGeoJSON {
  type: 'FeatureCollection'
  features: CoverageFeature[]
}

export function useCoverageGeoJSON() {
  return useQuery({
    queryKey: ['coverage-geojson'],
    queryFn: () => apiFetch<CoverageGeoJSON>('/coverage/geojson'),
    staleTime: 5 * 60_000,
  })
}
