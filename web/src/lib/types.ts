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
