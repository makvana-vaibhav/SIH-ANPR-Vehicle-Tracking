/**
 * GIS map screen — Judge Moment 1 and challenge FAQ Q15's "interactive GIS map
 * with layered filters".
 *
 * 250 cameras on a Gujarat map, filterable by department, status, district,
 * vendor and ANPR capability, with a detail panel on click.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import CameraMap from '@/components/CameraMap'
import CameraPanel from '@/components/CameraPanel'
import * as api from '@/lib/api'
import type {
  CameraFeatureProperties,
  CameraGeoJSON,
  Department,
  FleetHealth,
  VmsInstance,
} from '@/lib/types'

const STATUSES = ['online', 'offline', 'degraded', 'unknown'] as const

const STATUS_LABEL = {
  online: 'Online',
  offline: 'Offline',
  degraded: 'Degraded',
  unknown: 'Awaiting integration',
} as const

const STATUS_DOT = {
  online: 'bg-status-online',
  offline: 'bg-status-offline',
  degraded: 'bg-status-degraded',
  unknown: 'bg-status-unknown',
} as const

export default function MapView() {
  const [cameras, setCameras] = useState<CameraGeoJSON | null>(null)
  const [districts, setDistricts] = useState<GeoJSON.FeatureCollection | null>(null)
  const [health, setHealth] = useState<FleetHealth | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [vmsInstances, setVmsInstances] = useState<VmsInstance[]>([])
  const [selected, setSelected] = useState<CameraFeatureProperties | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Filters — the "layered filters" the challenge asks for.
  const [department, setDepartment] = useState('')
  const [status, setStatus] = useState('')
  const [vendor, setVendor] = useState('')
  const [anprOnly, setAnprOnly] = useState(false)
  const [search, setSearch] = useState('')

  // District boundaries are a static asset: fetched once, cached by the
  // browser, and served from our own origin — no tile server involved.
  useEffect(() => {
    fetch('/data/gujarat_districts.geojson')
      .then((r) => (r.ok ? r.json() : null))
      .then(setDistricts)
      .catch(() => setDistricts(null))
  }, [])

  useEffect(() => {
    Promise.all([api.getDepartments(), api.getVmsInstances()])
      .then(([d, v]) => {
        setDepartments(d)
        setVmsInstances(v)
      })
      .catch(() => undefined)
  }, [])

  const loadCameras = useCallback(async () => {
    const params: Record<string, string> = {}
    if (department) params.department_code = department
    if (status) params.status = status
    if (vendor) params.vendor = vendor
    if (anprOnly) params.anpr_enabled = 'true'

    try {
      const [geo, fleet] = await Promise.all([
        api.getCamerasGeoJSON(params),
        api.getFleetHealth(),
      ])
      setCameras(geo)
      setHealth(fleet)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [department, status, vendor, anprOnly])

  useEffect(() => {
    void loadCameras()
  }, [loadCameras])

  // Live fleet state, refreshed on the health-probe cadence.
  useEffect(() => {
    const timer = window.setInterval(() => void loadCameras(), 30_000)
    return () => window.clearInterval(timer)
  }, [loadCameras])

  // Text search is applied client-side against the already-loaded set, so
  // typing filters instantly instead of round-tripping per keystroke.
  const visible = useMemo(() => {
    if (!cameras) return null
    if (!search.trim()) return cameras
    const needle = search.trim().toLowerCase()
    return {
      ...cameras,
      features: cameras.features.filter((f) => {
        const p = f.properties
        return (
          p.camera_code.toLowerCase().includes(needle) ||
          p.name.toLowerCase().includes(needle) ||
          (p.district ?? '').toLowerCase().includes(needle) ||
          (p.junction ?? '').toLowerCase().includes(needle)
        )
      }),
    }
  }, [cameras, search])

  const vendors = useMemo(
    () => Array.from(new Set(vmsInstances.map((v) => v.vendor))).sort(),
    [vmsInstances],
  )

  const shownCount = visible?.features.length ?? 0

  return (
    <div className="flex h-full flex-col">
      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border md:grid-cols-5">
        <Kpi
          label="Cameras"
          value={health?.total ?? '—'}
          hint={`${shownCount} shown`}
        />
        <Kpi
          label="Online"
          value={health?.online ?? '—'}
          tone="text-status-online"
          hint={
            health?.integrated_availability_pct != null
              ? `${health.integrated_availability_pct}% of integrated`
              : undefined
          }
        />
        <Kpi
          label="Offline"
          value={health?.offline ?? '—'}
          tone={health && health.offline > 0 ? 'text-status-offline' : undefined}
        />
        <Kpi
          label="Degraded"
          value={health?.degraded ?? '—'}
          tone={health && health.degraded > 0 ? 'text-status-degraded' : undefined}
        />
        <Kpi
          label="Awaiting integration"
          value={health?.awaiting_integration ?? '—'}
          tone="text-status-unknown"
          hint="Registered, no live feed"
        />
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Filter rail */}
        <aside className="w-64 shrink-0 overflow-y-auto border-r border-border bg-card/40 p-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Filters
          </h2>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-muted-foreground">Search</span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Code, name, junction…"
              className="mt-1 w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary"
            />
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-muted-foreground">Department</span>
            <select
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              className="mt-1 w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary"
            >
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.code} value={d.code}>
                  {d.code} ({d.camera_count})
                </option>
              ))}
            </select>
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-muted-foreground">VMS vendor</span>
            <select
              value={vendor}
              onChange={(e) => setVendor(e.target.value)}
              className="mt-1 w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary"
            >
              <option value="">All vendors</option>
              {vendors.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>

          <fieldset className="mt-4">
            <legend className="text-xs font-medium text-muted-foreground">Status</legend>
            <div className="mt-1.5 space-y-1">
              <StatusOption
                label="All statuses"
                active={status === ''}
                onClick={() => setStatus('')}
              />
              {STATUSES.map((s) => (
                <StatusOption
                  key={s}
                  label={STATUS_LABEL[s]}
                  dot={STATUS_DOT[s]}
                  count={health ? (health[s] as number) : undefined}
                  active={status === s}
                  onClick={() => setStatus(status === s ? '' : s)}
                />
              ))}
            </div>
          </fieldset>

          <label className="mt-4 flex items-center gap-2">
            <input
              type="checkbox"
              checked={anprOnly}
              onChange={(e) => setAnprOnly(e.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            <span className="text-sm">ANPR-capable only</span>
          </label>

          {(department || status || vendor || anprOnly || search) && (
            <button
              type="button"
              onClick={() => {
                setDepartment('')
                setStatus('')
                setVendor('')
                setAnprOnly(false)
                setSearch('')
              }}
              className="mt-4 w-full rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
            >
              Clear filters
            </button>
          )}

          <div className="mt-6 border-t border-border pt-4">
            <p className="text-xs text-muted-foreground">
              Basemap renders Gujarat district boundaries from local GeoJSON.
              <strong className="block pt-1 text-foreground/70">
                No tile server — works fully offline.
              </strong>
            </p>
          </div>
        </aside>

        {/* Map */}
        <main className="relative min-w-0 flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}
          {error && (
            <div className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded border border-status-offline/40 bg-status-offline/15 px-4 py-2 text-sm text-status-offline">
              {error}
            </div>
          )}
          <CameraMap
            cameras={visible}
            districts={districts}
            onSelect={setSelected}
            selectedCode={selected?.camera_code ?? null}
          />

          {/* Legend */}
          <div className="pointer-events-none absolute bottom-4 right-4 rounded-lg border border-border bg-card/90 p-3 backdrop-blur">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Camera status
            </p>
            <ul className="mt-2 space-y-1">
              {STATUSES.map((s) => (
                <li key={s} className="flex items-center gap-2 text-xs">
                  <span className={`h-2.5 w-2.5 rounded-full ${STATUS_DOT[s]}`} />
                  <span className="text-muted-foreground">{STATUS_LABEL[s]}</span>
                </li>
              ))}
            </ul>
          </div>
        </main>

        {/* Detail panel */}
        {selected && (
          <CameraPanel
            camera={selected}
            onClose={() => setSelected(null)}
            onRefresh={() => void loadCameras()}
          />
        )}
      </div>
    </div>
  )
}

function Kpi({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: number | string
  tone?: string
  hint?: string
}) {
  return (
    <div className="bg-card px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`mt-0.5 text-2xl font-semibold tabular-nums ${tone ?? ''}`}>
        {value}
      </p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  )
}

function StatusOption({
  label,
  dot,
  count,
  active,
  onClick,
}: {
  label: string
  dot?: string
  count?: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm transition ${
        active ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/50'
      }`}
    >
      {dot && <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />}
      <span className="flex-1 truncate">{label}</span>
      {count !== undefined && (
        <span className="font-mono text-xs tabular-nums">{count}</span>
      )}
    </button>
  )
}
