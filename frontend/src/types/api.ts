// ---------------------------------------------------------------------------
// Alert types
// ---------------------------------------------------------------------------

export interface AlertSummary {
  gap_event_id: number;
  vessel_id: number;
  gap_start_utc: string;
  gap_end_utc: string;
  duration_minutes: number;
  corridor_id: number | null;
  risk_score: number;
  status: string;
  analyst_notes: string | null;
  impossible_speed_flag: boolean;
  in_dark_zone: boolean;
  velocity_plausibility_ratio: number | null;
  max_plausible_distance_nm: number | null;
  actual_gap_distance_nm: number | null;
  risk_breakdown_json: Record<string, unknown> | null;
  last_lat: number | null;
  last_lon: number | null;
  vessel_name: string | null;
  vessel_mmsi: string | null;
  pre_gap_sog?: number;
  dark_zone_id?: number;
  prior_similar_count?: number | null;
  is_recurring_pattern?: boolean | null;
  is_false_positive?: boolean | null;
  reviewed_by?: string | null;
  review_date?: string | null;
  assigned_to?: number | null;
  assigned_to_username?: string | null;
  assigned_at?: string | null;
  version: number;
}

export interface AISPointSummary {
  timestamp_utc: string;
  lat: number;
  lon: number;
  sog: number | null;
  cog: number | null;
}

export interface MovementEnvelope {
  envelope_id: number;
  max_plausible_distance_nm: number | null;
  actual_gap_distance_nm: number | null;
  velocity_plausibility_ratio: number | null;
  envelope_semi_major_nm: number | null;
  envelope_semi_minor_nm: number | null;
  envelope_heading_degrees: number | null;
  confidence_ellipse_geojson: object | null;
  interpolated_positions_json: Array<{ lat: number; lon: number }> | null;
  estimated_method: "linear" | "spline" | "kalman" | null;
}

export interface SatelliteCheckSummary {
  sat_check_id: number;
  provider: string | null;
  review_status: "not_checked" | "candidate_scenes_found" | "reviewed";
  copernicus_url: string | null;
  imagery_url: string | null;
  cloud_cover_pct: number | null;
}

// ---------------------------------------------------------------------------
// Alert enrichment types (Phase H)
// ---------------------------------------------------------------------------

export interface SpoofingAnomalySummary {
  anomaly_id: number;
  anomaly_type: string;
  start_time_utc: string;
  risk_score_component: number | null;
  evidence_json: Record<string, unknown> | null;
}

export interface LoiteringSummary {
  loiter_id: number;
  start_time_utc: string;
  duration_hours: number | null;
  mean_lat: number | null;
  mean_lon: number | null;
  median_sog_kn: number | null;
}

export interface StsSummary {
  sts_id: number;
  partner_name: string | null;
  partner_mmsi: string | null;
  detection_type: string | null;
  start_time_utc: string;
}

export interface AlertDetail extends AlertSummary {
  vessel_name: string | null;
  vessel_mmsi: string | null;
  vessel_flag: string | null;
  vessel_deadweight: number | null;
  corridor_name: string | null;
  movement_envelope: MovementEnvelope | null;
  satellite_check: SatelliteCheckSummary | null;
  last_point: AISPointSummary | null;
  first_point_after: AISPointSummary | null;
  spoofing_anomalies?: SpoofingAnomalySummary[] | null;
  loitering_events?: LoiteringSummary[] | null;
  sts_events?: StsSummary[] | null;
  prior_similar_count?: number | null;
  is_recurring_pattern?: boolean | null;
}

export type AlertStatus =
  | "new"
  | "under_review"
  | "needs_satellite_check"
  | "documented"
  | "dismissed"
  | "confirmed_fp"
  | "confirmed_tp";

export interface AlertMapPoint {
  gap_event_id: number;
  vessel_id: number;
  last_lat: number | null;
  last_lon: number | null;
  risk_score: number;
  vessel_name: string | null;
  gap_start_utc: string;
  duration_minutes: number;
}

// ---------------------------------------------------------------------------
// Vessel types
// ---------------------------------------------------------------------------

export interface VesselSummary {
  vessel_id: number;
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag: string | null;
  vessel_type: string | null;
  deadweight: number | null;
  last_risk_score: number | null;
  watchlist_status: boolean;
  matched_via_absorbed_mmsi?: string;
}

export interface WatchlistEntry {
  watchlist_entry_id: number;
  watchlist_source: string;
  reason: string | null;
  date_listed: string | null;
  is_active: boolean;
}

export interface SpoofingAnomaly {
  anomaly_id: number;
  anomaly_type: string;
  start_time_utc: string;
  risk_score_component: number;
}

export interface LoiteringEvent {
  loiter_id: number;
  start_time_utc: string;
  duration_hours: number | null;
  corridor_id: number | null;
}

export interface StsEventNested {
  sts_id: number;
  vessel_1_id: number;
  vessel_2_id: number;
  start_time_utc: string;
  detection_type: string;
}

export interface VesselDetail {
  vessel_id: number;
  mmsi: string;
  imo: string | null;
  name: string | null;
  flag: string | null;
  vessel_type: string | null;
  deadweight: number | null;
  year_built: number | null;
  ais_class: string | null;
  flag_risk_category: string | null;
  pi_coverage_status: string | null;
  psc_detained_last_12m: boolean;
  psc_major_deficiencies_last_12m?: number;
  callsign?: string;
  owner_name?: string;
  mmsi_first_seen_utc: string | null;
  vessel_laid_up_30d: boolean;
  vessel_laid_up_60d: boolean;
  vessel_laid_up_in_sts_zone: boolean;
  merged_into_vessel_id: number | null;
  watchlist_entries: WatchlistEntry[];
  spoofing_anomalies_30d: SpoofingAnomaly[];
  loitering_events_30d: LoiteringEvent[];
  sts_events_60d: StsEventNested[];
  total_gaps_7d: number;
  total_gaps_30d: number;
}

export interface VesselHistoryEntry {
  vessel_history_id: number;
  field_changed: string;
  old_value: string | null;
  new_value: string | null;
  observed_at: string;
  source: string | null;
}

export interface PortCallItem {
  port_call_id: number;
  vessel_id: number;
  port_id: number | null;
  port_name: string | null;
  arrival_utc: string | null;
  departure_utc: string | null;
  source: string | null;
}

// ---------------------------------------------------------------------------
// Corridor types
// ---------------------------------------------------------------------------

export interface CorridorSummary {
  corridor_id: number;
  name: string;
  corridor_type: string;
  risk_weight: number | null;
  is_jamming_zone: boolean;
  description: string | null;
  alert_count_7d: number;
  alert_count_30d: number;
  avg_risk_score: number | null;
}

export interface CorridorDetail {
  corridor_id: number;
  name: string;
  corridor_type: string;
  risk_weight: number | null;
  is_jamming_zone: boolean;
  description: string | null;
  alert_count_7d: number;
  alert_count_30d: number;
}

export interface CorridorUpdatePayload {
  name: string;
  risk_weight: number;
  description: string;
  is_jamming_zone: boolean;
}

export interface CorridorCreatePayload {
  name: string;
  corridor_type?: string;
  risk_weight?: number;
  description?: string;
  is_jamming_zone?: boolean;
  geometry_wkt?: string;
}

// ---------------------------------------------------------------------------
// STS types
// ---------------------------------------------------------------------------

export interface StsEventSummary {
  sts_id: number;
  vessel_1_id: number;
  vessel_2_id: number;
  detection_type: string;
  start_time_utc: string;
  end_time_utc: string;
  duration_minutes: number | null;
  mean_proximity_meters: number | null;
  mean_lat: number | null;
  mean_lon: number | null;
  corridor_id: number | null;
  satellite_confirmation_status: string | null;
  eta_minutes: number | null;
  risk_score_component: number;
  user_validated: boolean | null;
  confidence_override: number | null;
}

// ---------------------------------------------------------------------------
// Export types
// ---------------------------------------------------------------------------

export interface ExportResponse {
  content: string;
  media_type: string;
  evidence_card_id: number;
}

// ---------------------------------------------------------------------------
// v1.1 stub types (backend models exist, API not yet wired)
// ---------------------------------------------------------------------------

export interface DarkVesselDetection {
  detection_id: number;
  scene_id: string | null;
  detection_lat: number | null;
  detection_lon: number | null;
  detection_time_utc: string | null;
  length_estimate_m: number | null;
  model_confidence: number | null;
  vessel_type_inferred: string | null;
  ais_match_attempted: boolean;
  ais_match_result: string | null;
  matched_vessel_id: number | null;
  corridor_id: number | null;
  created_gap_event_id: number | null;
}

export interface HuntCandidate {
  candidate_id: number;
  mission_id: number;
  detection_lat: number | null;
  detection_lon: number | null;
  detection_time_utc: string | null;
  visual_similarity_score: number | null;
  length_estimate_m: number | null;
  heading_estimate_deg: number | null;
  hunt_score: number | null;
  score_breakdown_json: Record<string, unknown> | null;
  satellite_scene_id: string | null;
  image_chip_ref: string | null;
  analyst_review_status: string | null;
  government_alert_sent: boolean;
}

export interface SearchMission {
  mission_id: number;
  vessel_id: number;
  profile_id: number | null;
  search_start_utc: string | null;
  search_end_utc: string | null;
  created_at: string;
  analyst_id: string | null;
  search_ellipse_wkt: string | null;
  center_lat: number | null;
  center_lon: number | null;
  max_radius_nm: number | null;
  elapsed_hours: number | null;
  confidence: string | null;
  status: string;
}

export interface VesselTargetProfile {
  profile_id: number;
  vessel_id: number;
  reference_images_json: unknown[] | null;
  hull_type: string | null;
  deadweight_dwt: number | null;
  loa_meters: number | null;
  beam_meters: number | null;
  typical_draft_meters: number | null;
  funnel_color: string | null;
  hull_color: string | null;
  last_ais_position_lat: number | null;
  last_ais_position_lon: number | null;
  last_ais_timestamp_utc: string | null;
  profile_created_at: string;
  created_by_analyst_id: string | null;
}

// ---------------------------------------------------------------------------
// Merge types
// ---------------------------------------------------------------------------

export interface MergeCandidateSummary {
  candidate_id: number;
  vessel_a: { vessel_id: number; mmsi: string | null; name: string | null };
  vessel_b: { vessel_id: number; mmsi: string | null; name: string | null };
  distance_nm: number | null;
  time_delta_hours: number | null;
  confidence_score: number;
  match_reasons: Record<string, unknown> | null;
  satellite_corroboration: Record<string, unknown> | null;
  status: string;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface VesselAlias {
  mmsi: string;
  name: string | null;
  flag: string | null;
  status: "current" | "absorbed";
  absorbed_at?: string | null;
}

export interface TimelineEvent {
  event_type: string;
  timestamp: string | null;
  summary: string;
  details: Record<string, unknown>;
  related_entity_id: number;
}

// ---------------------------------------------------------------------------
// Merge Chain types
// ---------------------------------------------------------------------------

export interface MergeChainNode {
  vessel_id: number;
  mmsi: string;
  name: string | null;
  flag: string | null;
  role: string;
}

export interface MergeChainEdge {
  source_id: number;
  target_id: number;
  confidence: number;
  evidence: string | null;
}

export interface MergeChainItem {
  chain_id: number;
  confidence_band: string;
  confidence: number;
  chain_length: number;
  nodes: MergeChainNode[];
  edges: MergeChainEdge[];
}

// ---------------------------------------------------------------------------
// Auth / Analyst types
// ---------------------------------------------------------------------------

export interface AlertLockInfo {
  lock_id: number;
  alert_id: number;
  analyst_id: number;
  analyst_username: string;
  acquired_at: string;
  expires_at: string;
}

export interface AnalystInfo {
  analyst_id: number;
  username: string;
  display_name: string | null;
  role: "analyst" | "senior_analyst" | "admin";
}

// ---------------------------------------------------------------------------
// PSC detention types
// ---------------------------------------------------------------------------

export interface PscDetention {
  psc_detention_id: number;
  detention_date: string;
  release_date: string | null;
  port_name: string | null;
  port_country: string | null;
  mou_source: string;
  deficiency_count: number;
  major_deficiency_count: number;
  detention_reason: string | null;
  authority_name: string | null;
  flag_at_detention: string | null;
}

// ---------------------------------------------------------------------------
// Satellite order types
// ---------------------------------------------------------------------------

export interface SatelliteOrderSummary {
  satellite_order_id: number;
  provider: string;
  order_type: string;
  external_order_id: string | null;
  status: string;
  cost_usd: number | null;
  created_utc: string | null;
  updated_utc: string | null;
}

export interface SatelliteBudget {
  budget_usd: number;
  spent_usd: number;
  remaining_usd: number;
}

// ---------------------------------------------------------------------------
// Narrative types
// ---------------------------------------------------------------------------

export interface NarrativeResponse {
  narrative: string;
  format: string;
  strength: number;
  warnings: string[];
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Investigation Case types
// ---------------------------------------------------------------------------

export interface CaseCreate {
  title: string;
  description?: string | null;
  priority?: string;
  vessel_id?: number | null;
  corridor_id?: number | null;
  tags?: string[] | null;
}

export interface CaseUpdate {
  title?: string | null;
  description?: string | null;
  status?: string | null;
  priority?: string | null;
  assigned_to?: number | null;
  tags?: string[] | null;
}

export interface CaseResponse {
  case_id: number;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  assigned_to: number | null;
  assigned_to_username: string | null;
  created_by: number | null;
  vessel_id: number | null;
  corridor_id: number | null;
  tags: string[];
  alert_count: number;
  created_at: string;
  updated_at: string;
}

export interface CaseSuggestion {
  alert_id: number;
  related_alerts: Array<{
    alert_id: number;
    reason: string;
    score: number;
  }>;
}

// ---------------------------------------------------------------------------
// Regional FP Tuning types
// ---------------------------------------------------------------------------

export interface ScoringRegion {
  region_id: number;
  name: string;
  description: string | null;
  corridor_ids: number[];
  signal_overrides: Record<string, number> | null;
  corridor_multiplier_override: number | null;
  gap_duration_multiplier: number;
  is_active: boolean;
  fp_rate: number | null;
  created_at: string;
  updated_at: string;
}

export interface ShadowScoreResult {
  alert_id: number;
  original_score: number;
  proposed_score: number;
  original_band: string;
  proposed_band: string;
  band_changed: boolean;
}

export interface ShadowScoreResponse {
  corridor_id: number;
  alerts_scored: number;
  band_changes: number;
  avg_score_delta: number;
  predicted_fp_rate_change: number | null;
  results: ShadowScoreResult[];
}
