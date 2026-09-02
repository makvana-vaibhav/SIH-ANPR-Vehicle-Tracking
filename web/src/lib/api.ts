/**
 * API client for the Sentinel-GJ backend.
 *
 * Holds the access token, refreshes it transparently when it expires, and
 * gives every request a bounded timeout — per the UI rule in CLAUDE.md that
 * the interface must never block indefinitely on a slow request.
 */

import type {
  Alert,
  AlertPage,
  AlertStatus,
  AuditPage,
  Camera,
  CameraGeoJSON,
  CameraHealthHistory,
  CameraPage,
  Department,
  DetectionPage,
  ManagedUser,
  FleetHealth,
  FleetSummary,
  GapReport,
  StreamGrant,
  ConvoyReport,
  Priority,
  Role,
  RoutablePlates,
  UserProfile,
  UserPage,
  VehicleRoute,
  VmsInstance,
  WatchlistEntry,
} from '@/lib/types'

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

const ACCESS_KEY = 'sentinel.access_token'
const REFRESH_KEY = 'sentinel.refresh_token'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ── Token storage ─────────────────────────────────────────────────────
// sessionStorage, not localStorage: an operator's credentials should not
// outlive the browser session on a shared control-room workstation.

function safeGet(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key)
    else window.sessionStorage.setItem(key, value)
  } catch {
    /* storage unavailable (private mode); tokens stay in memory only */
  }
}

let accessToken: string | null = safeGet(ACCESS_KEY)
let refreshToken: string | null = safeGet(REFRESH_KEY)

export function setTokens(access: string | null, refresh: string | null): void {
  accessToken = access
  refreshToken = refresh
  safeSet(ACCESS_KEY, access)
  safeSet(REFRESH_KEY, refresh)
}

export function getAccessToken(): string | null {
  return accessToken
}

export function isAuthenticated(): boolean {
  return accessToken !== null
}

/** Called when the session cannot be recovered, so the app can show login. */
let onSessionExpired: (() => void) | null = null
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler
}

// ── Core request ──────────────────────────────────────────────────────

interface RequestOptions extends RequestInit {
  timeoutMs?: number
  skipAuth?: boolean
  /** Internal: prevents infinite refresh recursion. */
  _retried?: boolean
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = 15000, skipAuth = false, _retried = false, ...init } = options

  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (!skipAuth && accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`Request to ${path} timed out after ${timeoutMs}ms`, 408)
    }
    throw new ApiError(
      `Cannot reach the platform API. Is it running? (${String(error)})`,
      0,
    )
  } finally {
    window.clearTimeout(timer)
  }

  // An expired access token is routine — refresh once and retry silently
  // rather than interrupting an operator mid-task.
  if (response.status === 401 && !skipAuth && !_retried && refreshToken) {
    const refreshed = await tryRefresh()
    if (refreshed) {
      return request<T>(path, { ...options, _retried: true })
    }
    setTokens(null, null)
    onSessionExpired?.()
    throw new ApiError('Session expired. Please sign in again.', 401)
  }

  if (response.status === 204) {
    return undefined as T
  }

  let body: unknown = null
  const text = await response.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!response.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? (body as { detail: unknown }).detail
        : body
    throw new ApiError(
      typeof detail === 'string'
        ? detail
        : `${init.method ?? 'GET'} ${path} failed (${response.status})`,
      response.status,
      detail,
    )
  }

  return body as T
}

async function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return false
  try {
    const tokens = await request<{ access_token: string; refresh_token: string }>(
      '/api/v1/auth/refresh',
      {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken }),
        skipAuth: true,
        _retried: true,
      },
    )
    setTokens(tokens.access_token, tokens.refresh_token)
    return true
  } catch {
    return false
  }
}

// ── Endpoints ─────────────────────────────────────────────────────────

export async function login(
  username: string,
  password: string,
): Promise<UserProfile> {
  const tokens = await request<{ access_token: string; refresh_token: string }>(
    '/api/v1/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      skipAuth: true,
    },
  )
  setTokens(tokens.access_token, tokens.refresh_token)
  return getProfile()
}

export async function logout(): Promise<void> {
  const token = refreshToken
  setTokens(null, null)
  if (!token) return
  try {
    await request('/api/v1/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: token }),
    })
  } catch {
    /* already signed out locally; the token expires on its own */
  }
}

export const getProfile = () => request<UserProfile>('/api/v1/auth/me')

export const getCamerasGeoJSON = (params: Record<string, string> = {}) => {
  const query = new URLSearchParams({ limit: '5000', ...params })
  return request<CameraGeoJSON>(`/api/v1/cameras/geojson?${query}`)
}

export const getCameras = (params: Record<string, string> = {}) => {
  const query = new URLSearchParams(params)
  return request<CameraPage>(`/api/v1/cameras?${query}`)
}

export const getCamera = (id: string) => request<Camera>(`/api/v1/cameras/${id}`)

export const getFleetSummary = () =>
  request<FleetSummary>('/api/v1/cameras/summary')

export const getFleetHealth = () => request<FleetHealth>('/api/v1/health/fleet')

export const getGapReport = (hours = 24) =>
  request<GapReport>(`/api/v1/health/gaps?hours=${hours}`)

export const getCameraHealth = (id: string, hours = 24) =>
  request<CameraHealthHistory>(`/api/v1/cameras/${id}/health?hours=${hours}`)

export const probeCamera = (id: string) =>
  request<Record<string, unknown>>(`/api/v1/cameras/${id}/probe`, { method: 'POST' })

export const openStream = (id: string) =>
  request<StreamGrant>(`/api/v1/cameras/${id}/stream`)

export const getDepartments = () => request<Department[]>('/api/v1/departments')

export const getVmsInstances = () => request<VmsInstance[]>('/api/v1/vms')

export const getAdapters = () =>
  request<{ adapters: Record<string, string>; detail: string }>(
    '/api/v1/integration/adapters',
  )

export const getNearby = (lat: number, lon: number, radiusKm = 5) =>
  request<Camera[]>(
    `/api/v1/cameras/nearby?lat=${lat}&lon=${lon}&radius_km=${radiusKm}&limit=50`,
  )

// ── Detections ────────────────────────────────────────────────────────

export interface DetectionQuery {
  camera_id?: string
  plate?: string
  plate_prefix?: string
  since?: string
  readable_only?: boolean
  min_confidence?: number
  limit?: number
  offset?: number
}

export const getDetections = (query: DetectionQuery = {}) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return request<DetectionPage>(`/api/v1/detections?${params}`)
}

// ── Watchlist ─────────────────────────────────────────────────────────

export const getWatchlist = (params: Record<string, string> = {}) =>
  request<WatchlistEntry[]>(`/api/v1/watchlist?${new URLSearchParams(params)}`)

export interface WatchlistDraft {
  plate: string
  category: string
  priority?: Priority
  case_ref?: string | null
  remarks?: string | null
  valid_to?: string | null
}

export const addToWatchlist = (draft: WatchlistDraft) =>
  request<WatchlistEntry>('/api/v1/watchlist', {
    method: 'POST',
    body: JSON.stringify(draft),
  })

export const updateWatchlistEntry = (
  id: string,
  changes: Partial<WatchlistDraft> & { active?: boolean },
) =>
  request<WatchlistEntry>(`/api/v1/watchlist/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })

export const deleteWatchlistEntry = (id: string) =>
  request<void>(`/api/v1/watchlist/${id}`, { method: 'DELETE' })

// ── Alerts ────────────────────────────────────────────────────────────

export interface AlertQuery {
  status?: AlertStatus
  plate?: string
  camera_id?: string
  open_only?: boolean
  limit?: number
  offset?: number
}

export const getAlerts = (query: AlertQuery = {}) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return request<AlertPage>(`/api/v1/alerts?${params}`)
}

/** Move an alert along its lifecycle. Every transition records who and when. */
export const transitionAlert = (id: string, status: AlertStatus, notes?: string) =>
  request<Alert>(`/api/v1/alerts/${id}/transition`, {
    method: 'POST',
    body: JSON.stringify({ status, notes: notes ?? null }),
  })

// ── Vehicle intelligence ──────────────────────────────────────────────

export interface RouteQuery {
  since?: string
  until?: string
}

export const getVehicleRoute = (plate: string, query: RouteQuery = {}) => {
  const params = new URLSearchParams(query as Record<string, string>)
  return request<VehicleRoute>(
    `/api/v1/vehicles/${encodeURIComponent(plate)}/route?${params}`,
  )
}

/** The same route as GeoJSON, for drawing. */
export const getVehicleRouteGeoJSON = (plate: string, query: RouteQuery = {}) => {
  const params = new URLSearchParams({ ...query, format: 'geojson' } as Record<
    string,
    string
  >)
  return request<GeoJSON.FeatureCollection>(
    `/api/v1/vehicles/${encodeURIComponent(plate)}/route?${params}`,
  )
}

export const getConvoys = (
  plate: string,
  query: RouteQuery & { window_s?: number; min_shared_cameras?: number } = {},
) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return request<ConvoyReport>(
    `/api/v1/vehicles/${encodeURIComponent(plate)}/convoy?${params}`,
  )
}

/**
 * Plates seen on enough cameras to have a route at all.
 *
 * Without this an operator searching a plate that was only ever seen once
 * concludes the feature is broken, when the honest answer is that there is
 * nothing to draw.
 */
export const getRoutablePlates = (minCameras = 2, limit = 25, since?: string) => {
  const params = new URLSearchParams({
    min_cameras: String(minCameras),
    limit: String(limit),
  })
  if (since) params.set('since', since)
  return request<RoutablePlates>(`/api/v1/vehicles/routable?${params}`)
}

// ── User administration ───────────────────────────────────────────────

export const getUsers = () => request<UserPage>('/api/v1/users')

export interface UserDraft {
  username: string
  password: string
  full_name?: string | null
  role: Role
  must_change_password?: boolean
}

export const createUser = (draft: UserDraft) =>
  request<ManagedUser>('/api/v1/users', {
    method: 'POST',
    body: JSON.stringify(draft),
  })

export const updateUser = (
  id: string,
  changes: { full_name?: string | null; role?: Role; is_active?: boolean },
) =>
  request<ManagedUser>(`/api/v1/users/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })

export const resetUserPassword = (
  id: string,
  newPassword: string,
  mustChange = true,
) =>
  request<ManagedUser>(`/api/v1/users/${id}/password`, {
    method: 'POST',
    body: JSON.stringify({
      new_password: newPassword,
      must_change_password: mustChange,
    }),
  })

export const deleteUser = (id: string) =>
  request<void>(`/api/v1/users/${id}`, { method: 'DELETE' })

/** Change your own password, re-verifying the current one. */
export const changeOwnPassword = (currentPassword: string, newPassword: string) =>
  request<{ detail: string }>('/api/v1/auth/password', {
    method: 'POST',
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })

// ── Audit trail ───────────────────────────────────────────────────────

export interface AuditQuery {
  action?: string
  username?: string
  result?: string
  since?: string
  limit?: number
  offset?: number
}

export const getAudit = (query: AuditQuery = {}) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return request<AuditPage>(`/api/v1/audit?${params}`)
}

// ── Formatting ────────────────────────────────────────────────────────

/** Format a UTC ISO timestamp for display in IST. Storage stays UTC. */
export function formatIST(iso: string, withDate = true): string {
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    ...(withDate ? { dateStyle: 'medium' as const } : {}),
    timeStyle: 'medium',
    hour12: false,
  }).format(new Date(iso))
}

export function relativeTime(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (seconds < 60) return `${Math.floor(seconds)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
