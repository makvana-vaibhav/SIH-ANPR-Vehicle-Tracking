/** Shared API types for the command centre. */

export type CameraStatus = 'online' | 'offline' | 'degraded' | 'unknown'

export interface Camera {
  id: string
  camera_code: string
  name: string
  department_code: string | null
  department_name: string | null
  vms_name: string | null
  vms_vendor: string | null
  /** The road this camera sits on, for corridor-level analytics — see
   *  migration 0004. Null for a camera on no corridor the platform models. */
  corridor: string | null
  district: string | null
  city: string | null
  junction: string | null
  address: string | null
  lat: number
  lon: number
  heading_deg: number | null
  camera_type: string | null
  protocol: string | null
  resolution: string | null
  fps: number | null
  anpr_enabled: boolean
  status: CameraStatus
  installed_on: string | null
  tags: string[] | null
  /** Whether a video source is configured. The URL itself is never sent to the
   *  browser — a federated camera's carries the grid credentials. */
  has_stream: boolean
  created_at: string
  updated_at: string
  distance_km?: number | null
}

export interface CameraPage {
  items: Camera[]
  total: number
  limit: number
  offset: number
}

export interface CameraFeatureProperties {
  id: string
  camera_code: string
  name: string
  corridor: string | null
  district: string | null
  city: string | null
  junction: string | null
  status: CameraStatus
  camera_type: string | null
  anpr_enabled: boolean
  heading_deg: number | null
  department_code: string | null
  department_name: string | null
  vendor: string | null
}

export interface CameraGeoJSON {
  type: 'FeatureCollection'
  features: Array<{
    type: 'Feature'
    id?: string
    geometry: { type: 'Point'; coordinates: [number, number] }
    properties: CameraFeatureProperties
  }>
  properties: { count: number; by_status: Record<string, number> }
}

export interface FleetHealth {
  total: number
  online: number
  offline: number
  degraded: number
  unknown: number
  integrated: number
  awaiting_integration: number
  availability_pct: number
  integrated_availability_pct: number | null
  by_department: Array<{
    department: string
    total: number
    online: number
    offline: number
    degraded: number
    availability_pct: number
  }>
  by_vendor: Array<{
    vendor: string
    total: number
    online: number
    availability_pct: number
  }>
  top_errors: Array<{ error_code: string; count: number }>
  generated_at: string
}

export interface FleetSummary {
  total: number
  by_status: Record<string, number>
  by_department: Record<string, number>
  by_district: Record<string, number>
  anpr_enabled: number
}

export interface StreamGrant {
  camera_id: string
  camera_code: string
  name: string
  status: string
  token: string
  expires_in: number
  whep_url: string | null
  hls_url: string | null
  rtsp_url: string | null
  protocol_preference: string[]
}

export interface CameraHealthHistory {
  camera_id: string
  camera_code: string
  status: CameraStatus
  uptime: {
    window_hours: number
    probes: number
    uptime_pct: number | null
    avg_latency_ms: number | null
    avg_fps: number | null
  }
  history: Array<{
    ts: string
    reachable: boolean | null
    fps_actual: number | null
    latency_ms: number | null
    bitrate_kbps: number | null
    error_code: string | null
  }>
}

export interface GapReport {
  window_hours: number
  generated_at: string
  summary: {
    unavailable_cameras: number
    districts_below_anpr_target: number
    districts_with_no_cameras: number
    unreliable_cameras: number
  }
  availability_gaps: Array<{
    camera_code: string
    name: string
    district: string | null
    status: string
    department: string
  }>
  capability_gaps: Array<{
    district: string
    cameras: number
    anpr_cameras: number
    anpr_share: number
    reason: string
  }>
  coverage_gaps: Array<{ district: string; reason: string }>
  reliability_gaps: Array<{
    camera_code: string
    name: string
    district: string | null
    uptime_pct: number
    reason: string
  }>
  district_coverage: Array<{
    district: string
    cameras: number
    anpr_cameras: number
    online: number
    anpr_share: number
    availability_pct: number
  }>
}

export interface UserProfile {
  id: string
  username: string
  full_name: string | null
  role: string
  department_id: string | null
  is_active: boolean
  last_login_at: string | null
  permissions: string[]
  /** An administrator chose this password; it is a shared secret until changed. */
  must_change_password?: boolean
}

export interface Department {
  id: string
  code: string
  name: string
  contact_email: string | null
  camera_count: number
}

export interface VmsInstance {
  id: string
  name: string
  vendor: string
  adapter_type: string
  base_url: string | null
  status: string
  last_sync_at: string | null
  camera_count: number
}

// ── Intelligence: detections, watchlist, alerts ───────────────────────

export interface BBox {
  x1: number
  y1: number
  x2: number
  y2: number
  w: number
  h: number
}

export interface Detection {
  id: string
  ts: string
  camera_id: string | null
  camera_code: string
  camera_name: string
  track_id: string
  vehicle_type: string | null
  plate: string | null
  plate_confidence: number | null
  detection_confidence: number | null
  grammar_valid: boolean | null
  bbox: BBox | null
  plate_bbox: BBox | null
  crop_key: string | null
  /** Short-lived signed URL for the plate crop. See Alert.crop_url. */
  crop_url: string | null
  reads_total: number | null
  agreement: number | null
  corrected_from: string | null
  ambiguous: boolean
  candidates: { text: string; score: number; grammar_valid: boolean }[]
}

export interface DetectionPage {
  items: Detection[]
  total: number
  limit: number
  offset: number
}

export type Priority = 'low' | 'medium' | 'high' | 'critical'

export type AlertStatus =
  | 'new'
  | 'acknowledged'
  | 'dispatched'
  | 'closed'
  | 'false_positive'

export interface WatchlistEntry {
  id: string
  plate_normalised: string
  category: string
  priority: Priority
  case_ref: string | null
  remarks: string | null
  valid_from: string | null
  valid_to: string | null
  active: boolean
  added_by: string | null
  created_at: string
}

/** One factor behind an alert. `factor` is a machine-stable slug the UI can
 *  branch or group on; `detail` is the sentence an operator reads. */
export interface AlertReason {
  factor: string
  detail: string
}

export interface Alert {
  id: string
  created_at: string
  alert_type: string
  priority: Priority
  plate_normalised: string | null
  confidence: number | null
  status: AlertStatus
  camera_id: string | null
  detection_id: string | null
  watchlist_id: string | null
  acknowledged_by: string | null
  acknowledged_at: string | null
  notes: string | null
  /**
   * Short-lived signed URL for the plate crop of the detection that raised
   * this alert. Null when there is no crop. May 404 briefly after a detection
   * while the upload is still in flight, so render it with an onError that
   * hides the image rather than showing a broken one.
   */
  crop_url: string | null
  /** Why this fired — the explainability rule in CLAUDE.md §5. Null for a
   *  watchlist hit or camera-down alert (the match/notes already say why);
   *  populated for `anomaly`. */
  reasons: AlertReason[] | null
}

export interface AlertPage {
  items: Alert[]
  total: number
  limit: number
  offset: number
}

/**
 * What arrives on `/ws/events`.
 *
 * Three shapes share the socket. `vehicle.observed` is a vehicle still in
 * view — the one to draw on live video. `vehicle.completed` is the settled
 * consensus, and the one that was written to the database. `alert.raised` is
 * a watchlist hit.
 */
export interface LiveVehicleEvent {
  event: 'vehicle.observed' | 'vehicle.completed'
  /** When the worker emitted this. Later than `captured_at` by the pipeline. */
  event_time: string
  /**
   * When the frame these boxes were measured in was captured.
   *
   * This, not `event_time`, is the timestamp an overlay has to draw against:
   * the difference between the two is `latency_ms`, and a box placed at the
   * emit time lands wherever the vehicle has got to since. Null on events from
   * a worker that predates the field.
   */
  captured_at?: string | null
  /** Capture-to-event, in milliseconds. */
  latency_ms?: number
  /**
   * True when this repeats a reading already reported, to update only where
   * the vehicle now is. For drawing, never for counting — see
   * `lib/events.ts`.
   */
  position_refresh?: boolean
  source: { camera_id: string; name?: string }
  vehicle: {
    vehicle_id: number
    track_ids: number[]
    type: string
    confidence: number
    /** The best sighting — largest and most confident. What the crop came from. */
    bbox: BBox | null
    /**
     * Where the vehicle was in the `captured_at` frame. The only box with a
     * timestamp, and so the only one worth drawing over live video.
     */
    live_bbox?: BBox | null
    first_seen_s: number
    last_seen_s: number
  }
  plate: {
    text: string
    confidence: number
    readable: boolean
    grammar_valid: boolean
    ambiguous: boolean
    corrected_from: string | null
    format: string
    bbox?: BBox | null
    evidence?: { reads_total?: number; agreement?: number; method?: string }
  }
  /**
   * Crop evidence for this sighting. The worker publishes only the object
   * `plate_crop` key — putting the JPEG on the bus would multiply event
   * traffic tenfold — and the API signs `plate_crop_url` on the way out,
   * because a browser can use neither a key nor an Authorization header on an
   * `<img>`. Absent until the upload lands, and possibly never.
   */
  evidence?: {
    plate_crop?: string | null
    vehicle_crop?: string | null
    plate_crop_url?: string | null
  }
  frame?: { width: number; height: number } | null
}

export interface LiveAlertEvent {
  event: 'alert.raised'
  alert_id: string
  alert_type: string
  priority: Priority
  plate: string | null
  confidence: number | null
  status: AlertStatus
  camera_id: string | null
  detection_id: string | null
  created_at: string
  notes: string | null
  reasons: AlertReason[] | null
  watchlist?: {
    id: string
    plate: string
    category: string
    case_ref: string | null
    exact: boolean
  }
}

export type LiveEvent =
  | LiveVehicleEvent
  | LiveAlertEvent
  | { event: 'connected'; subscribers: number }
  | { event: 'keepalive' }

// ── Vehicle intelligence: routes and convoys ──────────────────────────

/**
 * One camera in a reconstructed journey, and the leg that led to it.
 *
 * `flags` is why the correlator does not believe part of this leg. An empty
 * list means nothing was found wrong, which is weaker than "this leg is
 * correct" — distances are straight lines between cameras, so a plausible-
 * looking leg has only passed a weak test.
 */
export interface RouteHop {
  camera_id: string
  camera_code: string
  camera_name: string
  city: string | null
  district: string | null
  lat: number
  lon: number
  arrived_at: string
  departed_at: string
  dwell_s: number
  sightings: number
  plate_confidence: number
  distance_m: number | null
  distance_km: number | null
  elapsed_s: number | null
  implied_kmph: number | null
  bearing_deg: number | null
  flags: string[]
  plausible: boolean
}

export interface VehicleRoute {
  plate: string
  window: { from: string | null; to: string | null }
  first_seen: string | null
  last_seen: string | null
  hop_count: number
  camera_count: number
  distance_km: number
  duration_s: number
  /** Time between cameras, excluding dwell at each one. */
  moving_s: number
  /**
   * Journey average, first sighting to last — so it includes time spent
   * stationary. A lower bound twice over: distances are great-circle rather
   * than road, and dwell inflates the denominator. Null when there is no
   * elapsed time to divide by; a single sighting has no speed.
   */
  average_kmph: number | null
  /** Average while moving, ignoring dwell. Null on the same terms. */
  moving_kmph: number | null
  is_plausible: boolean
  confidence: number
  flagged_hop_count: number
  hops: RouteHop[]
  geometry_note: string
}

export type RouteGeoJSON = GeoJSON.FeatureCollection

export interface Convoy {
  plate: string
  with_plate: string
  shared_cameras: number
  first_together: string
  last_together: string
  median_gap_s: number
  /**
   * The partner plate is within one character of the subject — almost always
   * one vehicle read two ways rather than two vehicles travelling together.
   */
  likely_same_vehicle: boolean
}

export interface ConvoyReport {
  plate: string
  window: { from: string | null; to: string | null }
  criteria: { seconds_apart: number; min_shared_cameras: number }
  convoys: Convoy[]
  note: string
}

export interface RoutablePlates {
  since: string
  min_cameras: number
  plates: { plate: string; cameras: number }[]
}

// ── Fuzzy plate search (P8) ─────────────────────────────────────────────

export interface WatchlistHit {
  category: string
  priority: Priority
  case_ref: string | null
}

export interface PlateSearchResult {
  plate_normalised: string
  /** pg_trgm trigram similarity to the query, 0-1. 1.0 means an exact match. */
  similarity: number
  sightings: number
  cameras: number
  first_seen: string
  last_seen: string
  /** Present when this plate is on an active watchlist entry. */
  watchlist: WatchlistHit | null
}

export interface PlateSearchResponse {
  query: string
  /** The minimum trigram similarity a result had to clear. */
  threshold: number
  /** True when `query` itself is among the results at similarity 1.0. */
  exact_match: boolean
  results: PlateSearchResult[]
}

// ── User administration and audit ─────────────────────────────────────

export type Role =
  | 'admin'
  | 'supervisor'
  | 'operator'
  | 'analyst'
  | 'auditor'
  | 'api_client'

export interface ManagedUser {
  id: string
  username: string
  full_name: string | null
  role: Role
  department_id: string | null
  is_active: boolean
  /** An administrator chose this password; the holder must replace it. */
  must_change_password: boolean
  last_login_at: string | null
  created_at: string | null
}

export interface UserPage {
  items: ManagedUser[]
  total: number
}

export interface AuditEntry {
  id: number
  ts: string
  user_id: string | null
  username: string | null
  role: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  ip: string | null
  result: string
  params: Record<string, unknown> | null
}

export interface AuditPage {
  items: AuditEntry[]
  total: number
  limit: number
  offset: number
}

// ── City traffic analytics ─────────────────────────────────────────────
// Mirrors app/schemas/analytics.py exactly. Every figure that can be absent
// carries a `status` rather than a substituted number — see that module's
// docstring. `DataStatus` is a closed set so the UI can branch on it
// exhaustively instead of pattern-matching prose.

export type DataStatus = 'ok' | 'insufficient_history' | 'insufficient_data'

export interface AnalyticsWindow {
  start: string
  end: string
  /** Width of one time bucket, in seconds. 0 when the response is not bucketed. */
  bucket_seconds: number
}

export interface FlowPoint {
  ts: string
  vehicles: number
  with_plate: number
}

export interface FlowSeries {
  /** Camera code, or corridor name when grouped by corridor. */
  key: string
  label: string
  corridor: string | null
  points: FlowPoint[]
  total: number
}

export interface FlowResponse {
  window: AnalyticsWindow
  group_by: 'camera' | 'corridor'
  series: FlowSeries[]
  total_vehicles: number
}

export interface SpeedProvenance {
  source: 'observed_journeys'
  legs_considered: number
  legs_excluded_implausible: number
  note: string
}

export interface SegmentSpeed {
  from_camera: string
  to_camera: string
  corridor: string | null
  distance_km: number
  status: DataStatus
  samples: number
  median_kmph: number | null
  p85_kmph: number | null
  median_seconds: number | null
}

export interface CorridorSpeed {
  corridor: string
  status: DataStatus
  samples: number
  median_kmph: number | null
  segments: SegmentSpeed[]
}

export interface SpeedResponse {
  window: AnalyticsWindow
  provenance: SpeedProvenance
  corridors: CorridorSpeed[]
}

export interface RoutePair {
  from_camera: string
  to_camera: string
  from_corridor: string | null
  to_corridor: string | null
  journeys: number
  distance_km: number
  median_gap_seconds: number
  same_corridor: boolean
}

export interface RouteDensityResponse {
  window: AnalyticsWindow
  pairs: RoutePair[]
  total_journeys: number
}

export interface TravelTime {
  from_camera: string
  to_camera: string
  corridor: string | null
  status: DataStatus
  current_seconds: number | null
  current_samples: number
  baseline_seconds: number | null
  baseline_samples: number
  baseline_days: number
  /** Positive means slower than baseline. Null unless both figures exist. */
  delta_pct: number | null
}

export interface TravelTimeResponse {
  window: AnalyticsWindow
  segments: TravelTime[]
  baseline_window_days: number
}

export interface Hotspot {
  camera_code: string
  camera_name: string
  corridor: string | null
  lat: number
  lon: number
  vehicles: number
  /** Share of the busiest camera's count, 0-1. */
  intensity: number
}

export interface HotspotResponse {
  window: AnalyticsWindow
  by_volume: Hotspot[]
  by_slowdown: TravelTime[]
  slowdown_status: DataStatus
  note: string
}

export interface HeatmapFeatureProperties {
  camera_code: string
  camera_name: string
  corridor: string | null
  vehicles: number
  intensity: number
}

export interface HeatmapResponse {
  type: 'FeatureCollection'
  features: Array<{
    type: 'Feature'
    geometry: { type: 'Point'; coordinates: [number, number] }
    properties: HeatmapFeatureProperties
  }>
  window: AnalyticsWindow
  max_vehicles: number
}
