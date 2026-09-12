/**
 * City traffic analytics — Judge Moment 8, and until this phase the one PS
 * demo step with nothing behind it at all: no router, no page, no heatmap,
 * `recharts` imported zero times. See docs/ROADMAP.md#p4.
 *
 * ## The rule this page cannot break
 *
 * Every figure on this screen is computed from observed rows on the server
 * (`app/routers/analytics.py`) and carries the evidence it was computed
 * from. This page's only job is to render that honestly:
 *
 * - A `status` of `insufficient_data` or `insufficient_history` is rendered
 *   as exactly that — never as a zero, never as a chart bar that looks like
 *   a real measurement sitting at nought.
 * - The demo fleet replays recorded clips, so the speed endpoint usually
 *   excludes every leg as implausible (a clip loop makes the gap between two
 *   cameras seconds, not minutes, which is hundreds of km/h over real
 *   distance). That is shown as the provenance note explains it, not hidden.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { SkeletonRows, SkeletonStat } from '@/components/Skeleton'
import {
  Badge,
  EmptyState,
  ErrorBanner,
  Panel,
  PanelHeader,
  Select,
  SegmentedControl,
  StatTile,
  Table,
  Td,
  Th,
  Thead,
  Tr,
} from '@/components/ui'
import * as api from '@/lib/api'
import type {
  CongestionResponse,
  FlowResponse,
  HotspotResponse,
  RouteDensityResponse,
  SpeedResponse,
  TravelTimeResponse,
} from '@/lib/types'

const WINDOWS = [
  { value: '1', label: '1 h', hours: 1, bucket: '5m' },
  { value: '6', label: '6 h', hours: 6, bucket: '15m' },
  { value: '24', label: '24 h', hours: 24, bucket: '1h' },
  { value: '168', label: '7 d', hours: 168, bucket: '1d' },
] as const

/** Cycled across corridor series in the flow chart. Drawn from the app's own
 *  semantic tokens — never an invented chart palette — so a corridor's
 *  colour agrees with what the same word means everywhere else on screen. */
const SERIES_COLORS = [
  'hsl(var(--primary))',
  'hsl(var(--status-online))',
  'hsl(var(--priority-high))',
  'hsl(var(--status-unknown))',
]

const AXIS_STYLE = { fontSize: 11, fill: 'hsl(var(--muted-foreground))' }
const TOOLTIP_STYLE = {
  background: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 6,
  fontSize: 12,
}

function since(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

function clockLabel(iso: string, hours: number): string {
  const date = new Date(iso)
  return hours > 24
    ? date.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' })
    : date.toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
}

function seconds(value: number | null): string {
  if (value === null) return '—'
  if (value < 60) return `${Math.round(value)}s`
  return `${Math.round(value / 60)} min`
}

export default function Analytics() {
  const [hours, setHours] = useState(24)
  const [corridor, setCorridor] = useState('')
  const [corridors, setCorridors] = useState<string[]>([])

  const [flow, setFlow] = useState<FlowResponse | null>(null)
  const [speed, setSpeed] = useState<SpeedResponse | null>(null)
  const [routes, setRoutes] = useState<RouteDensityResponse | null>(null)
  const [travelTime, setTravelTime] = useState<TravelTimeResponse | null>(null)
  const [hotspots, setHotspots] = useState<HotspotResponse | null>(null)
  const [congestion, setCongestion] = useState<CongestionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const bucket = WINDOWS.find((w) => w.hours === hours)?.bucket ?? '15m'

  // The corridor list, once. There is no dedicated endpoint for it — the
  // camera registry already knows every corridor a camera sits on.
  useEffect(() => {
    api
      .getCameras({ limit: '500' })
      .then((page) => {
        const found = Array.from(
          new Set(
            page.items.map((c) => c.corridor).filter((c): c is string => Boolean(c)),
          ),
        ).sort()
        setCorridors(found)
      })
      .catch(() => undefined)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const windowSince = since(hours)
      const [flowBody, speedBody, routesBody, travelBody, hotspotsBody, congestionBody] =
        await Promise.all([
          api.getAnalyticsFlow({ since: windowSince, bucket, group_by: 'corridor', corridor }),
          api.getAnalyticsSpeed({ since: windowSince, corridor }),
          api.getAnalyticsRoutes({ since: windowSince, limit: 15 }),
          api.getAnalyticsTravelTime({ since: windowSince }),
          api.getAnalyticsHotspots({ since: windowSince, limit: 8 }),
          api.getCongestionForecast({ group_by: 'camera', corridor }),
        ])
      setFlow(flowBody)
      setSpeed(speedBody)
      setRoutes(routesBody)
      setTravelTime(travelBody)
      setHotspots(hotspotsBody)
      setCongestion(congestionBody)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [hours, corridor, bucket])

  useEffect(() => {
    void load()
  }, [load])

  // One row per bucket, one key per corridor — the shape a stacked area
  // chart wants. `flow.series` arrives one array per corridor instead.
  const flowRows = useMemo(() => {
    if (!flow) return []
    const byBucket = new Map<string, Record<string, number | string>>()
    for (const series of flow.series) {
      for (const point of series.points) {
        const row = byBucket.get(point.ts) ?? { ts: point.ts }
        row[series.key] = point.vehicles
        byBucket.set(point.ts, row)
      }
    }
    return Array.from(byBucket.values()).sort((a, b) =>
      String(a.ts).localeCompare(String(b.ts)),
    )
  }, [flow])

  const corridorKeys = useMemo(
    () => (flow ? flow.series.map((s) => s.key).slice(0, SERIES_COLORS.length) : []),
    [flow],
  )

  const okCorridors = useMemo(
    () => (speed ? speed.corridors.filter((c) => c.status === 'ok') : []),
    [speed],
  )
  const shortCorridors = useMemo(
    () => (speed ? speed.corridors.filter((c) => c.status !== 'ok') : []),
    [speed],
  )

  return (
    <div className="space-y-6 overflow-y-auto p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Traffic analytics</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            City-wide flow, speed, route density and travel time — computed from
            observed detections and journeys, not estimated.
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Corridor
            </span>
            <div className="mt-1">
              <Select
                value={corridor}
                onChange={(e) => setCorridor(e.target.value)}
                className="mt-0 py-1.5"
              >
                <option value="">All corridors</option>
                {corridors.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div>
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Window
            </span>
            <div className="mt-1">
              <SegmentedControl
                value={String(hours)}
                onChange={(v) => setHours(Number(v))}
                options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
              />
            </div>
          </div>
        </div>
      </header>

      {error && <ErrorBanner>{error}</ErrorBanner>}

      {/* ── KPI strip ─────────────────────────────────────────────────── */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {loading && !flow ? (
          <>
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
            <SkeletonStat />
          </>
        ) : (
          <>
            <StatTile
              label="Vehicles observed"
              value={flow?.total_vehicles.toLocaleString('en-IN') ?? '—'}
              detail={`over the last ${WINDOWS.find((w) => w.hours === hours)?.label}`}
            />
            <StatTile
              label="Journeys reconstructed"
              value={routes?.total_journeys.toLocaleString('en-IN') ?? '—'}
              detail={`across ${routes?.pairs.length ?? 0} distinct routes`}
            />
            <StatTile
              label="Corridors with a speed figure"
              value={speed ? `${okCorridors.length} / ${speed.corridors.length}` : '—'}
              tone={speed && okCorridors.length === 0 ? 'warn' : undefined}
              detail={
                speed
                  ? `${speed.provenance.legs_excluded_implausible} of ${speed.provenance.legs_considered} legs excluded as implausible`
                  : undefined
              }
            />
            <StatTile
              label="Busiest camera"
              value={hotspots?.by_volume[0]?.camera_code ?? '—'}
              detail={
                hotspots?.by_volume[0]
                  ? `${hotspots.by_volume[0].vehicles} vehicles · ${hotspots.by_volume[0].corridor ?? 'no corridor'}`
                  : 'no detections in this window'
              }
            />
          </>
        )}
      </section>

      {/* ── Flow ──────────────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Vehicle flow by corridor" />
        {loading && !flow ? (
          <div className="mt-3">
            <SkeletonRows rows={1} height="h-64" />
          </div>
        ) : flowRows.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No detections in this window." />
          </div>
        ) : (
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={flowRows} margin={{ left: -20, right: 8, top: 8 }}>
                <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                <XAxis
                  dataKey="ts"
                  tickFormatter={(v: string) => clockLabel(v, hours)}
                  tick={AXIS_STYLE}
                  axisLine={{ stroke: 'hsl(var(--border))' }}
                  tickLine={false}
                />
                <YAxis tick={AXIS_STYLE} axisLine={false} tickLine={false} width={36} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelFormatter={(v: string) => clockLabel(v, hours)}
                />
                {corridorKeys.map((key, i) => (
                  <Area
                    key={key}
                    type="monotone"
                    dataKey={key}
                    name={key}
                    stackId="flow"
                    stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                    fill={SERIES_COLORS[i % SERIES_COLORS.length]}
                    fillOpacity={0.25}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {/* ── Speed ─────────────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Corridor speed" />
        {speed && (
          <p className="mt-1 text-xs text-muted-foreground">{speed.provenance.note}</p>
        )}
        {loading && !speed ? (
          <div className="mt-3">
            <SkeletonRows rows={1} height="h-48" />
          </div>
        ) : okCorridors.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              title="No corridor has a published speed figure in this window."
              hint="Every observed leg was excluded as physically implausible — see the note above. This is the expected state on replayed demo footage and resolves unchanged on real camera timing (P7)."
            />
          </div>
        ) : (
          <div className="mt-3 h-48">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={okCorridors} margin={{ left: -20, right: 8, top: 8 }}>
                <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                <XAxis
                  dataKey="corridor"
                  tick={AXIS_STYLE}
                  axisLine={{ stroke: 'hsl(var(--border))' }}
                  tickLine={false}
                />
                <YAxis
                  tick={AXIS_STYLE}
                  axisLine={false}
                  tickLine={false}
                  width={36}
                  label={{
                    value: 'km/h',
                    angle: -90,
                    position: 'insideLeft',
                    style: { fontSize: 10, fill: 'hsl(var(--muted-foreground))' },
                  }}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="median_kmph" name="median km/h" fill="hsl(var(--primary))" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {shortCorridors.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground">
              No figure yet:
            </span>
            {shortCorridors.map((c) => (
              <Badge key={c.corridor} tone="neutral" title={`${c.samples} plausible leg(s)`}>
                {c.corridor}
              </Badge>
            ))}
          </div>
        )}
      </Panel>

      {/* ── Route density ─────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Route density" />
        <p className="mt-1 text-xs text-muted-foreground">
          Camera-to-camera movements, ranked by how many distinct vehicles made
          the trip.
        </p>
        {loading && !routes ? (
          <div className="mt-3">
            <SkeletonRows rows={5} height="h-9" />
          </div>
        ) : !routes || routes.pairs.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No multi-camera journeys observed in this window." />
          </div>
        ) : (
          <div className="mt-3">
            <Table>
              <Thead>
                <tr>
                  <Th>From</Th>
                  <Th>To</Th>
                  <Th>Corridor</Th>
                  <Th className="text-right">Journeys</Th>
                  <Th className="text-right">Distance</Th>
                  <Th className="text-right">Median gap</Th>
                </tr>
              </Thead>
              <tbody>
                {routes.pairs.map((pair) => (
                  <Tr key={`${pair.from_camera}-${pair.to_camera}`}>
                    <Td className="font-mono text-xs">{pair.from_camera}</Td>
                    <Td className="font-mono text-xs">{pair.to_camera}</Td>
                    <Td className="text-xs text-muted-foreground">
                      {pair.same_corridor
                        ? pair.to_corridor ?? '—'
                        : `${pair.from_corridor ?? '?'} → ${pair.to_corridor ?? '?'}`}
                    </Td>
                    <Td className="text-right font-mono tabular-nums">{pair.journeys}</Td>
                    <Td className="text-right font-mono tabular-nums text-muted-foreground">
                      ≥ {pair.distance_km} km
                    </Td>
                    <Td className="text-right font-mono tabular-nums text-muted-foreground">
                      {seconds(pair.median_gap_seconds)}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </Panel>

      {/* ── Travel time ───────────────────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold">Travel time vs baseline</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Each segment's current duration against the same time of day on the
          previous {travelTime?.baseline_window_days ?? 7} days.
        </p>
        {loading && !travelTime ? (
          <div className="mt-3">
            <SkeletonRows rows={3} height="h-16" />
          </div>
        ) : !travelTime || travelTime.segments.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No segments observed in this window." />
          </div>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {travelTime.segments.slice(0, 12).map((segment) => (
              <div
                key={`${segment.from_camera}-${segment.to_camera}`}
                className="rounded-md border border-border bg-card px-4 py-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs font-semibold">
                    {segment.from_camera} → {segment.to_camera}
                  </span>
                  {segment.status === 'ok' ? (
                    <Badge tone={
                      segment.delta_pct !== null && segment.delta_pct > 15
                        ? 'danger'
                        : segment.delta_pct !== null && segment.delta_pct < -15
                          ? 'success'
                          : 'neutral'
                    }>
                      {segment.delta_pct !== null
                        ? `${segment.delta_pct > 0 ? '+' : ''}${segment.delta_pct}%`
                        : 'baseline'}
                    </Badge>
                  ) : (
                    <Badge tone="neutral">{segment.status.replace('_', ' ')}</Badge>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {segment.corridor ?? 'no corridor'}
                </p>
                <div className="mt-2 flex items-baseline gap-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Now
                    </p>
                    <p className="font-mono text-lg">{seconds(segment.current_seconds)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Baseline
                    </p>
                    <p className="font-mono text-sm text-muted-foreground">
                      {segment.status === 'insufficient_history'
                        ? `${segment.baseline_samples} of 5+ needed`
                        : seconds(segment.baseline_seconds)}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Hotspots ──────────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Busiest cameras" />
        <p className="mt-1 text-xs text-muted-foreground">{hotspots?.note}</p>
        {loading && !hotspots ? (
          <div className="mt-3">
            <SkeletonRows rows={4} height="h-9" />
          </div>
        ) : !hotspots || hotspots.by_volume.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No detections in this window." />
          </div>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {hotspots.by_volume.map((hot) => (
              <li
                key={hot.camera_code}
                className="flex items-center gap-3 rounded-md border border-border bg-card px-3 py-2"
              >
                <span className="w-24 shrink-0 truncate font-mono text-xs font-semibold">
                  {hot.camera_code}
                </span>
                <span className="flex-1 truncate text-xs text-muted-foreground">
                  {hot.camera_name}
                  {hot.corridor ? ` · ${hot.corridor}` : ''}
                </span>
                <div className="h-1.5 w-32 shrink-0 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.round(hot.intensity * 100)}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums">
                  {hot.vehicles}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ── Predicted congestion ──────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Predicted congestion" />
        <p className="mt-1 text-xs text-muted-foreground">
          Each figure is this camera&apos;s current volume against its own typical
          volume for this hour of day — 100% is normal, not an absolute road-capacity
          reading (no capacity data exists to compute one). The +15/+30 min figures are
          a straight-line trend through the last 30 minutes; the error shown beside each
          is that same method checked against buckets already inside this window.
        </p>
        {loading && !congestion ? (
          <div className="mt-3">
            <SkeletonRows rows={3} height="h-20" />
          </div>
        ) : !congestion || congestion.series.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No cameras with enough recent volume to forecast." />
          </div>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {congestion.series.slice(0, 12).map((c) => (
              <div key={c.key} className="rounded-md border border-border bg-card px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs font-semibold">{c.label}</span>
                  {c.status === 'ok' && c.current_index_pct !== null ? (
                    <Badge
                      tone={
                        c.current_index_pct > 150
                          ? 'danger'
                          : c.current_index_pct > 110
                            ? 'warning'
                            : 'neutral'
                      }
                    >
                      {Math.round(c.current_index_pct)}% of typical
                    </Badge>
                  ) : (
                    <Badge tone="neutral">{c.status.replace('_', ' ')}</Badge>
                  )}
                </div>
                {c.corridor && (
                  <p className="mt-1 text-[11px] text-muted-foreground">{c.corridor}</p>
                )}
                <div className="mt-2 flex items-baseline gap-4">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Now
                    </p>
                    <p className="font-mono text-lg">
                      {c.current_index_pct !== null ? `${Math.round(c.current_index_pct)}%` : '—'}
                    </p>
                  </div>
                  {c.forecasts.map((f) => (
                    <div key={f.horizon_minutes}>
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        +{f.horizon_minutes} min
                      </p>
                      <p className="font-mono text-sm text-muted-foreground">
                        {f.index_pct !== null ? `${Math.round(f.index_pct)}%` : '—'}
                      </p>
                    </div>
                  ))}
                </div>
                {/* Optional chaining, not just the length guard: this project
                    compiles with `noUncheckedIndexedAccess`, so an index read
                    is `T | undefined` however it is guarded. */}
                {c.factors.length > 0 && (
                  <p className="mt-2 text-[11px] text-muted-foreground">{c.factors[0]?.detail}</p>
                )}
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {c.backtest.status === 'ok' && c.backtest.mae_pct !== null
                    ? `Forecast error, backtested: ±${c.backtest.mae_pct} pts (${c.backtest.samples} held-out buckets)`
                    : 'Not enough history yet to backtest this forecast.'}
                </p>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}
