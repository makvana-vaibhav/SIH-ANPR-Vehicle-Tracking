/**
 * Traffic Intelligence — Judge Moment 8, and the city-wide half of the PS.
 *
 * This page was `Analytics.tsx` and absorbed the traffic feature rather than
 * sitting beside it. Flow, speed, route density, travel time, hotspots and
 * the congestion forecast all describe the same roads as the counts, density,
 * queues and obstructions added here; two pages would have shown the same
 * corridor twice and let the two disagree.
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
 * - Density is **vehicles in a camera's view**, not vehicles per kilometre,
 *   and the panel says so. No camera here is calibrated, so there is no
 *   honest conversion between the two.
 * - A stopped vehicle is a **possible obstruction** and never an accident.
 *   The system sees that a vehicle has not moved; it cannot see whether that
 *   is a breakdown, a delivery or a driver reading a map.
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
import type { BadgeTone } from '@/components/ui'
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
  CongestionLevel,
  CongestionResponse,
  FlowResponse,
  HotspotResponse,
  RouteDensityResponse,
  SpeedResponse,
  TrafficHistoryResponse,
  TrafficResponse,
  TrafficZone,
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

/** Congestion level → the app's own semantic tokens. Never an invented
 *  palette: green/amber/red mean the same here as everywhere else on screen. */
const CONGESTION_TONE: Record<CongestionLevel, BadgeTone> = {
  free: 'success',
  moderate: 'warning',
  heavy: 'warning',
  severe: 'danger',
}

/** The one place a missing figure becomes text. Everything that can be absent
 *  goes through here, so "not measured" can never render as a zero. */
function orDash(value: number | null | undefined, suffix = ''): string {
  return value === null || value === undefined ? '—' : `${value}${suffix}`
}

function CongestionBadge({ zone }: { zone: TrafficZone }) {
  if (!zone.congestion) {
    return (
      <Badge tone="neutral" title="No occupancy, speed or travel-time figure for this window">
        no data
      </Badge>
    )
  }
  return (
    <Badge
      tone={CONGESTION_TONE[zone.congestion]}
      title={zone.factors.map((f) => f.detail).join(' · ')}
    >
      {zone.congestion}
    </Badge>
  )
}

export default function TrafficIntelligence() {
  const [hours, setHours] = useState(24)
  const [corridor, setCorridor] = useState('')
  const [corridors, setCorridors] = useState<string[]>([])

  const [flow, setFlow] = useState<FlowResponse | null>(null)
  const [speed, setSpeed] = useState<SpeedResponse | null>(null)
  const [routes, setRoutes] = useState<RouteDensityResponse | null>(null)
  const [travelTime, setTravelTime] = useState<TravelTimeResponse | null>(null)
  const [hotspots, setHotspots] = useState<HotspotResponse | null>(null)
  const [congestion, setCongestion] = useState<CongestionResponse | null>(null)
  const [traffic, setTraffic] = useState<TrafficResponse | null>(null)
  const [history, setHistory] = useState<TrafficHistoryResponse | null>(null)
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
      const [
        flowBody,
        speedBody,
        routesBody,
        travelBody,
        hotspotsBody,
        congestionBody,
        trafficBody,
        historyBody,
      ] = await Promise.all([
        api.getAnalyticsFlow({ since: windowSince, bucket, group_by: 'corridor', corridor }),
        api.getAnalyticsSpeed({ since: windowSince, corridor }),
        api.getAnalyticsRoutes({ since: windowSince, limit: 15 }),
        api.getAnalyticsTravelTime({ since: windowSince }),
        api.getAnalyticsHotspots({ since: windowSince, limit: 8 }),
        api.getCongestionForecast({ group_by: 'camera', corridor }),
        // Grouped by corridor: a road is what an operator acts on, and it is
        // also the only grouping for which speed and travel time exist at all
        // — both come from the distance between two cameras.
        api.getTrafficState({ since: windowSince, group_by: 'corridor', corridor }),
        api.getTrafficHistory({
          since: windowSince,
          group_by: 'corridor',
          key: corridor || undefined,
        }),
      ])
      setFlow(flowBody)
      setSpeed(speedBody)
      setRoutes(routesBody)
      setTravelTime(travelBody)
      setHotspots(hotspotsBody)
      setCongestion(congestionBody)
      setTraffic(trafficBody)
      setHistory(historyBody)
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
    <div className="h-full space-y-6 overflow-y-auto p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Traffic intelligence</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            City-wide volume, density, speed, congestion and queues — computed
            from observed vehicles and journeys. Figures that cannot be
            computed say so rather than showing a zero.
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
              value={traffic?.total_vehicles.toLocaleString('en-IN') ?? '—'}
              detail={`unique vehicles over the last ${
                WINDOWS.find((w) => w.hours === hours)?.label
              }`}
            />
            <StatTile
              label="Active cameras"
              value={
                traffic ? `${traffic.active_cameras} / ${traffic.fleet_cameras}` : '—'
              }
              tone={traffic && traffic.active_cameras === 0 ? 'warn' : undefined}
              detail="saw at least one vehicle in this window"
            />
            <StatTile
              label="Roads congested"
              value={
                traffic
                  ? String(
                      traffic.zones.filter(
                        (z) => z.congestion === 'heavy' || z.congestion === 'severe',
                      ).length,
                    )
                  : '—'
              }
              tone={
                traffic &&
                traffic.zones.some((z) => z.congestion === 'severe')
                  ? 'bad'
                  : undefined
              }
              detail={`of ${traffic?.zones.length ?? 0} with traffic in this window`}
            />
            <StatTile
              label="Possible obstructions"
              value={traffic ? String(traffic.possible_obstructions.length) : '—'}
              tone={
                traffic && traffic.possible_obstructions.length > 0 ? 'warn' : undefined
              }
              detail="vehicles stopped far longer than a queue explains"
            />
          </>
        )}
      </section>

      {/* ── Vehicle mix ───────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Vehicles by type" />
        <p className="mt-1 text-xs text-muted-foreground">
          Unique vehicles, counted once when their track retires — never once
          per frame.
        </p>
        {loading && !traffic ? (
          <div className="mt-3">
            <SkeletonRows rows={1} height="h-20" />
          </div>
        ) : !traffic || traffic.total_vehicles === 0 ? (
          <div className="mt-3">
            <EmptyState title="No vehicles in this window." />
          </div>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {(
              [
                ['Car', traffic.totals_by_type.car],
                ['Motorcycle', traffic.totals_by_type.motorcycle],
                ['Bus', traffic.totals_by_type.bus],
                ['Truck', traffic.totals_by_type.truck],
                ['Other', traffic.totals_by_type.other],
              ] as const
            ).map(([label, count]) => (
              <div key={label} className="rounded-md border border-border px-3 py-2">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  {label}
                </p>
                <p className="mt-0.5 font-mono text-lg font-semibold">
                  {count.toLocaleString('en-IN')}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {traffic.total_vehicles > 0
                    ? `${Math.round((count / traffic.total_vehicles) * 100)}% of traffic`
                    : '—'}
                </p>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* ── Road state ────────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Road state" />
        <p className="mt-1 text-xs text-muted-foreground">
          Density is vehicles in view at once, not vehicles per kilometre — no
          camera here is calibrated, so there is no honest conversion between
          the two.
        </p>
        {loading && !traffic ? (
          <div className="mt-3">
            <SkeletonRows rows={5} />
          </div>
        ) : !traffic || traffic.zones.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No traffic observed in this window." />
          </div>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <Table>
              <Thead>
                <tr>
                  <Th>Road</Th>
                  <Th>Congestion</Th>
                  <Th className="text-right">Vehicles</Th>
                  <Th className="text-right">Per hour</Th>
                  <Th className="text-right">Density</Th>
                  <Th className="text-right">Speed</Th>
                  <Th className="text-right">Travel time</Th>
                  <Th>Queue</Th>
                </tr>
              </Thead>
              <tbody>
                {[...traffic.zones]
                  .sort((a, b) => (b.congestion_score ?? -1) - (a.congestion_score ?? -1))
                  .map((zone) => (
                    <Tr key={zone.key}>
                      <Td>
                        <span className="font-medium">{zone.label}</span>
                      </Td>
                      <Td>
                        <CongestionBadge zone={zone} />
                      </Td>
                      <Td className="text-right font-mono">
                        {zone.vehicles.toLocaleString('en-IN')}
                      </Td>
                      <Td className="text-right font-mono">
                        {orDash(zone.vehicles_per_hour)}
                      </Td>
                      <Td className="text-right font-mono">
                        {zone.density_status === 'ok' ? (
                          zone.peak_occupancy
                        ) : (
                          <span
                            className="text-muted-foreground"
                            title="No dwell times recorded for these vehicles"
                          >
                            —
                          </span>
                        )}
                      </Td>
                      <Td className="text-right font-mono">
                        {zone.speed_status === 'ok' ? (
                          `${zone.median_speed_kmph} km/h`
                        ) : (
                          <span
                            className="text-muted-foreground"
                            title="No physically plausible leg — expected on replayed demo footage"
                          >
                            —
                          </span>
                        )}
                      </Td>
                      <Td className="text-right font-mono">
                        {zone.travel_time_status === 'ok' &&
                        zone.travel_time_delta_pct !== null ? (
                          <span
                            className={
                              zone.travel_time_delta_pct > 15
                                ? 'text-priority-high'
                                : undefined
                            }
                          >
                            {zone.travel_time_delta_pct > 0 ? '+' : ''}
                            {zone.travel_time_delta_pct}%
                          </span>
                        ) : (
                          <span
                            className="text-muted-foreground"
                            title="No baseline yet for this time of day"
                          >
                            —
                          </span>
                        )}
                      </Td>
                      <Td>
                        {zone.queue.present ? (
                          <Badge tone="warning">
                            {zone.queue.length_vehicles} vehicles
                          </Badge>
                        ) : (
                          <span className="text-[11px] text-muted-foreground">none</span>
                        )}
                      </Td>
                    </Tr>
                  ))}
              </tbody>
            </Table>
            <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
              {traffic.note}
            </p>
          </div>
        )}
      </Panel>

      {/* ── Trend ─────────────────────────────────────────────────────── */}
      <Panel>
        <PanelHeader title="Traffic over time" />
        <p className="mt-1 text-xs text-muted-foreground">
          The same five-minute buckets the queue detector runs over, so the
          chart and the verdict beside it cannot disagree.
        </p>
        {loading && !history ? (
          <div className="mt-3">
            <SkeletonRows rows={1} height="h-56" />
          </div>
        ) : !history || history.points.length === 0 ? (
          <div className="mt-3">
            <EmptyState title="No history in this window." />
          </div>
        ) : (
          <div className="mt-3 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={history.points} margin={{ left: -20, right: 8, top: 8 }}>
                <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                <XAxis
                  dataKey="bucket"
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
                <Area
                  type="monotone"
                  dataKey="vehicles"
                  name="vehicles"
                  stroke="hsl(var(--primary))"
                  fill="hsl(var(--primary))"
                  fillOpacity={0.2}
                />
                <Area
                  type="monotone"
                  dataKey="stationary_vehicles"
                  name="stationary"
                  stroke="hsl(var(--priority-high))"
                  fill="hsl(var(--priority-high))"
                  fillOpacity={0.35}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {/* ── Possible obstructions ─────────────────────────────────────── */}
      {traffic && traffic.possible_obstructions.length > 0 && (
        <Panel>
          <PanelHeader title="Possible obstructions" />
          <p className="mt-1 text-xs text-muted-foreground">
            Vehicles that stopped and stayed stopped. The system can see that
            they did not move, not why — a breakdown, a delivery and a police
            stop all look identical from here.
          </p>
          <div className="mt-3 overflow-x-auto">
            <Table>
              <Thead>
                <tr>
                  <Th>Camera</Th>
                  <Th>Road</Th>
                  <Th>Vehicle</Th>
                  <Th>Plate</Th>
                  <Th className="text-right">Stationary for</Th>
                  <Th className="text-right">Last seen</Th>
                </tr>
              </Thead>
              <tbody>
                {traffic.possible_obstructions.slice(0, 12).map((row) => (
                  <Tr key={`${row.camera_code}:${row.track_id}`}>
                    <Td className="font-mono text-xs">{row.camera_code}</Td>
                    <Td>{row.corridor ?? '—'}</Td>
                    <Td>{row.vehicle_type ?? '—'}</Td>
                    <Td className="font-mono text-xs">{row.plate ?? '—'}</Td>
                    <Td className="text-right font-mono">
                      {seconds(row.stationary_seconds)}
                    </Td>
                    <Td className="text-right text-[11px] text-muted-foreground">
                      {api.formatIST(row.last_seen, false)}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </div>
        </Panel>
      )}

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
