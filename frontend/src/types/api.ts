export interface AlertSummary {
  gap_event_id: number
  vessel_id: number
  gap_start_utc: string
  gap_end_utc: string
  duration_minutes: number
  corridor_id: number | null
  risk_score: number
  status: string
  analyst_notes: string | null
  impossible_speed_flag: boolean
  in_dark_zone: boolean
  velocity_plausibility_ratio: number | null
  max_plausible_distance_nm: number | null
  actual_gap_distance_nm: number | null
  risk_breakdown_json: Record<string, unknown> | null
}

export interface AISPointSummary {
  timestamp_utc: string
  lat: number
  lon: number
  sog: number | null
  cog: number | null
}

export interface MovementEnvelope {
  envelope_id: number
  max_plausible_distance_nm: number | null
  actual_gap_distance_nm: number | null
  velocity_plausibility_ratio: number | null
  envelope_semi_major_nm: number | null
  envelope_semi_minor_nm: number | null
  envelope_heading_degrees: number | null
  confidence_ellipse_geojson: object | null
  interpolated_positions_json: Array<{ lat: number; lon: number }> | null
  estimated_method: 'linear' | 'spline' | 'kalman' | null
}

export interface SatelliteCheckSummary {
  sat_check_id: number
  provider: string | null
  review_status: 'not_checked' | 'candidate_scenes_found' | 'reviewed'
  copernicus_url: string | null
  imagery_url: string | null
  cloud_cover_pct: number | null
}

export interface AlertDetail extends AlertSummary {
  vessel_name: string | null
  vessel_mmsi: string | null
  vessel_flag: string | null
  vessel_deadweight: number | null
  corridor_name: string | null
  movement_envelope: MovementEnvelope | null
  satellite_check: SatelliteCheckSummary | null
  last_point: AISPointSummary | null
  first_point_after: AISPointSummary | null
}

export type AlertStatus =
  | 'new'
  | 'under_review'
  | 'needs_satellite_check'
  | 'documented'
  | 'dismissed'
