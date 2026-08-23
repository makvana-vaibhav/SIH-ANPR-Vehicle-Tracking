/**
 * API client for the Sentinel-GJ backend.
 *
 * Single place where the backend base URL is resolved. In the container build
 * VITE_API_BASE_URL is baked in at build time; in the dev server the Vite proxy
 * forwards /api and /ws, so a relative base works.
 */

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

export const WS_URL: string =
  (import.meta.env.VITE_WS_URL as string | undefined) ??
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/events`

/** A dependency as reported by the backend readiness probe. */
export interface DependencyHealth {
  status: 'ok' | 'degraded' | 'down'
  critical?: boolean
  error?: string
  server_version?: string
  cluster_status?: string
  extensions?: Record<string, string>
  fallback?: string
  active_paths?: number
}

export interface ReadinessResponse {
  status: 'ready' | 'degraded' | 'unavailable'
  probe_duration_ms: number
  dependencies: Record<string, DependencyHealth>
  critical_failures: string[]
  degraded: string[]
  now: string
}

export interface LivenessResponse {
  status: string
  service: string
  environment: string
  uptime_seconds: number
  started_at: string
  now: string
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/**
 * Fetch JSON from the API with a bounded timeout.
 *
 * Per the UI rules in CLAUDE.md the interface must never block indefinitely on
 * a slow request — every call carries its own abort deadline.
 */
export async function apiFetch<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 8000, ...init } = options
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.headers ?? {}),
      },
    })

    // Readiness deliberately returns 503 with a useful body; parse it rather
    // than throwing away the diagnosis.
    if (!response.ok && response.status !== 503) {
      throw new ApiError(
        `${init.method ?? 'GET'} ${path} failed: ${response.status} ${response.statusText}`,
        response.status,
      )
    }

    return (await response.json()) as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`Request to ${path} timed out after ${timeoutMs}ms`, 408)
    }
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

export const getLiveness = () => apiFetch<LivenessResponse>('/health')
export const getReadiness = () => apiFetch<ReadinessResponse>('/ready')

/** Format a UTC ISO timestamp for display in IST (storage stays UTC). */
export function formatIST(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'medium',
    hour12: false,
  }).format(new Date(iso))
}
