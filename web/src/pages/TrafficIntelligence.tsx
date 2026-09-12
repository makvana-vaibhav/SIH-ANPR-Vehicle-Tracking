/**
 * Traffic Intelligence — the city-wide half of the PS.
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
 *   distance). That is shown, not hidden.
 * - Density is **vehicles in a camera's view**, not vehicles per kilometre.
 *   No camera here is calibrated, so there is no honest conversion.
 * - A stopped vehicle is a **possible obstruction** and never an accident.
 *   The system sees that a vehicle has not moved; it cannot see whether that
 *   is a breakdown, a delivery or a driver reading a map.
 *
 * All of that used to be a paragraph under every one of nine panel headings,
 * stacked into four screens of scrolling. The caveats are unchanged and still
 * on the page — they sit behind the `ⓘ` beside each title, and the panels are
 * grouped into four tabs so each is one screenful.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
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
  InfoHint,
  PageHeader,
  Panel,
  PanelHeader,
  Select,
  SegmentedControl,
  StatTile,
  Table,
  Tabs,
  Td,
  Th,
  Thead,
  Toolbar,
  ToolbarField,
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

type TabKey = 'overview' | 'roads' | 'journeys' | 'forecast'

/**
 * Chart series colours, in fixed slot order.
 *
 * These were `--status-online` and `--priority-high` — so a corridor was drawn
 * in the colour that means "camera healthy" and "needs attention" on every
 * other screen, which is a claim the chart is not making. They were also
 * cycled with `% 4`, which silently gave two corridors the same colour once
 * there were five; this fleet sits on twelve.
 *
 * Four is the cap this palette validates for, so a fifth corridor and beyond
 * fold into one neutral band rather than repeating a hue. Two corridors
 * sharing a colour is worse than not naming them individually.
 */
const SERIES_COLORS = [
  'hsl(var(--chart-1))',
  'hsl(var(--chart-2))',
  'hsl(var(--chart-3))',
  'hsl(var(--chart-4))',
]
const OTHER_COLOR = 'hsl(var(--chart-other))'
const OTHER_KEY = 'Other corridors'
/** Series drawn individually before the rest fold into `OTHER_KEY`. */
const MAX_SERIES = SERIES_COLORS.length

const AXIS_STYLE = { fontSize: 11, fill: 'hsl(var(--muted-foreground))' }
/**
 * Chart margins and axis width.
 *
 * The left margin used to be -20 with a 36px axis, which pulled five-figure
 * counts off the left edge — "20,000" rendered as "i0". Vehicle counts here
 * run into tens of thousands, so the axis gets the room it needs and the
 * labels are abbreviated rather than truncated.
 */
const CHART_MARGIN = { left: 4, right: 8, top: 8, bottom: 0 }
const Y_AXIS_WIDTH = 48

/** 21400 → "21.4k". Keeps a five-figure count inside the gutter. */
function compact(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}
const TOOLTIP_STYLE = {
  background: 'hsl(var(--popover))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 6,
  fontSize: 12,
}
const LEGEND_STYLE = { fontSize: 11, paddingTop: 4 }
/** A hairline of surface between stacked bands, so adjacent fills stay legible. */
const STACK_GAP = 'hsl(var(--card))'

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
  const [tab, setTab] = useState<TabKey>('overview')
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

  /**
   * One row per bucket, one key per corridor — the shape a stacked area chart
   * wants. `flow.series` arrives one array per corridor instead.
   *
   * Past four corridors the remainder is summed into a single neutral band.
   * The alternative, cycling four hues across twelve corridors, drew three
   * different roads in the same colour and called them different things.
   */
  const { flowRows, flowKeys } = useMemo(() => {
    if (!flow) return { flowRows: [], flowKeys: [] as string[] }

    // Ranked by total volume so the four drawn individually are the four worth
    // looking at — then sorted by name, so a corridor keeps the same colour
    // when the window changes and the ranking shifts under it.
    const totals = flow.series.map((s) => ({
      key: s.key,
      total: s.points.reduce((sum, p) => sum + p.vehicles, 0),
    }))
    const named = new Set(
      [...totals]
        .sort((a, b) => b.total - a.total)
        .slice(0, MAX_SERIES)
        .map((t) => t.key),
    )
    const drawn = flow.series
      .map((s) => s.key)
      .filter((k) => named.has(k))
      .sort()
    const hasOther = flow.series.length > drawn.length

    const byBucket = new Map<string, Record<string, number | string>>()
    for (const series of flow.series) {
      const target = named.has(series.key) ? series.key : OTHER_KEY
      for (const point of series.points) {
        const row = byBucket.get(point.ts) ?? { ts: point.ts }
        row[target] = ((row[target] as number | undefined) ?? 0) + point.vehicles
        byBucket.set(point.ts, row)
      }
    }
    return {
      flowRows: Array.from(byBucket.values()).sort((a, b) =>
        String(a.ts).localeCompare(String(b.ts)),
      ),
      flowKeys: hasOther ? [...drawn, OTHER_KEY] : drawn,
    }
  }, [flow])

  const okCorridors = useMemo(
    () => (speed ? speed.corridors.filter((c) => c.status === 'ok') : []),
    [speed],
  )
  const shortCorridors = useMemo(
    () => (speed ? speed.corridors.filter((c) => c.status !== 'ok') : []),
    [speed],
  )

  const windowLabel = WINDOWS.find((w) => w.hours === hours)?.label ?? ''

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header, filters and the KPI strip stay put while the tab below
          scrolls — the window and corridor apply to everything underneath, so
          scrolling a long table should not scroll away the controls that say
          what the table is of. */}
      <div className="shrink-0 space-y-4 border-b border-border px-6 pt-6">
        <PageHeader
          title="Traffic intelligence"
          subtitle={
            <>
              <span>last {windowLabel}</span>
              <span className="text-muted-foreground/40">·</span>
              <span>{corridor || 'all corridors'}</span>
              <InfoHint label="How these figures are produced">
                Every figure here is computed from observed vehicles and
                journeys on the server. Anything that cannot be computed says so
                rather than showing a zero — a plausible-looking invented number
                would survive the demo and destroy the claim.
              </InfoHint>
            </>
          }
          actions={
            <Toolbar>
              {/* Both controls are pinned to the same height so their labels
                  sit on one line — a select and a segmented control have
                  different intrinsic heights, and bottom-aligning them left
                  the two captions stepped. */}
              <ToolbarField label="Corridor">
                <Select
                  value={corridor}
                  onChange={(e) => setCorridor(e.target.value)}
                  className="h-8 py-0"
                >
                  <option value="">All corridors</option>
                  {corridors.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </ToolbarField>
              <ToolbarField label="Window">
                <SegmentedControl
                  className="h-8"
                  value={String(hours)}
                  onChange={(v) => setHours(Number(v))}
                  options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
                />
              </ToolbarField>
            </Toolbar>
          }
        />

        {error && <ErrorBanner>{error}</ErrorBanner>}

        {/* ── KPI strip ─────────────────────────────────────────────── */}
        <section className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border lg:grid-cols-4">
          {loading && !traffic ? (
            <>
              <SkeletonStat />
              <SkeletonStat />
              <SkeletonStat />
              <SkeletonStat />
            </>
          ) : (
            <>
              <StatTile
                variant="strip"
                label="Vehicles observed"
                value={traffic?.total_vehicles.toLocaleString('en-IN') ?? '—'}
                detail={`unique, last ${windowLabel}`}
                info="Counted once when a vehicle's track retires, never once per frame. A vehicle in view for ten seconds is one vehicle."
              />
              <StatTile
                variant="strip"
                label="Active cameras"
                value={traffic ? `${traffic.active_cameras} / ${traffic.fleet_cameras}` : '—'}
                tone={traffic && traffic.active_cameras === 0 ? 'warn' : undefined}
                detail="saw a vehicle in this window"
              />
              <StatTile
                variant="strip"
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
                  traffic && traffic.zones.some((z) => z.congestion === 'severe')
                    ? 'bad'
                    : undefined
                }
                detail={`of ${traffic?.zones.length ?? 0} with traffic`}
              />
              <StatTile
                variant="strip"
                label="Possible obstructions"
                value={traffic ? String(traffic.possible_obstructions.length) : '—'}
                tone={traffic && traffic.possible_obstructions.length > 0 ? 'warn' : undefined}
                detail="stopped longer than a queue explains"
                info="A stopped vehicle, and nothing more. The system can see that it has not moved, not why — a breakdown, a delivery and a police stop all look identical from here."
              />
            </>
          )}
        </section>

        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: 'overview', label: 'Overview' },
            { value: 'roads', label: 'Roads' },
            { value: 'journeys', label: 'Journeys' },
            { value: 'forecast', label: 'Forecast' },
          ]}
        />
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-6">
        {tab === 'overview' && (
          <>
            <Panel>
              <PanelHeader
                title="Traffic over time"
                info="The same five-minute buckets the queue detector runs over, so this chart and the verdict on the Roads tab cannot disagree."
              />
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
                    <AreaChart data={history.points} margin={CHART_MARGIN}>
                      <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                      <XAxis
                        dataKey="bucket"
                        tickFormatter={(v: string) => clockLabel(v, hours)}
                        tick={AXIS_STYLE}
                        axisLine={{ stroke: 'hsl(var(--border))' }}
                        tickLine={false}
                        minTickGap={44}
                      />
                      <YAxis
                        tick={AXIS_STYLE}
                        axisLine={false}
                        tickLine={false}
                        width={Y_AXIS_WIDTH}
                        tickFormatter={compact}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(v: string) => clockLabel(v, hours)}
                      />
                      {/* Two series, so a legend is not optional — without one
                          the only thing telling them apart is colour. */}
                      <Legend wrapperStyle={LEGEND_STYLE} iconType="plainline" />
                      <Area
                        type="monotone"
                        dataKey="vehicles"
                        name="vehicles"
                        stroke={SERIES_COLORS[0]}
                        fill={SERIES_COLORS[0]}
                        fillOpacity={0.2}
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="stationary_vehicles"
                        name="stationary"
                        stroke={SERIES_COLORS[1]}
                        fill={SERIES_COLORS[1]}
                        fillOpacity={0.3}
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel>
                <PanelHeader
                  title="Vehicles by type"
                  info="Unique vehicles, counted once when their track retires — never once per frame."
                />
                {loading && !traffic ? (
                  <div className="mt-3">
                    <SkeletonRows rows={1} height="h-20" />
                  </div>
                ) : !traffic || traffic.total_vehicles === 0 ? (
                  <div className="mt-3">
                    <EmptyState title="No vehicles in this window." />
                  </div>
                ) : (
                  <VehicleMix traffic={traffic} />
                )}
              </Panel>

              <Panel>
                <PanelHeader title="Busiest cameras" info={hotspots?.note} />
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
                      <li key={hot.camera_code} className="flex items-center gap-3">
                        <span className="w-24 shrink-0 truncate font-mono text-xs font-semibold">
                          {hot.camera_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                          {hot.camera_name}
                          {hot.corridor ? ` · ${hot.corridor}` : ''}
                        </span>
                        <div className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.round(hot.intensity * 100)}%`,
                              background: SERIES_COLORS[0],
                            }}
                          />
                        </div>
                        <span className="w-12 shrink-0 text-right font-mono text-xs tabular-nums">
                          {hot.vehicles}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>

            <Panel>
              <PanelHeader
                title="Vehicle flow by corridor"
                info={`The ${MAX_SERIES} busiest corridors are drawn individually; everything else is summed into one neutral band. Cycling a small set of colours across twelve corridors would paint different roads the same colour and call them different things.`}
              />
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
                    <AreaChart data={flowRows} margin={CHART_MARGIN}>
                      <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                      <XAxis
                        dataKey="ts"
                        tickFormatter={(v: string) => clockLabel(v, hours)}
                        tick={AXIS_STYLE}
                        axisLine={{ stroke: 'hsl(var(--border))' }}
                        tickLine={false}
                        minTickGap={44}
                      />
                      <YAxis
                        tick={AXIS_STYLE}
                        axisLine={false}
                        tickLine={false}
                        width={Y_AXIS_WIDTH}
                        tickFormatter={compact}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(v: string) => clockLabel(v, hours)}
                      />
                      <Legend wrapperStyle={LEGEND_STYLE} iconType="square" />
                      {flowKeys.map((key, i) => (
                        <Area
                          key={key}
                          type="monotone"
                          dataKey={key}
                          name={key}
                          stackId="flow"
                          // The stroke is the card surface, not the fill: a
                          // hairline of background between stacked bands is
                          // what keeps two adjacent fills legible as two.
                          stroke={STACK_GAP}
                          strokeWidth={2}
                          fill={key === OTHER_KEY ? OTHER_COLOR : SERIES_COLORS[i]}
                          fillOpacity={0.85}
                        />
                      ))}
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Panel>
          </>
        )}

        {tab === 'roads' && (
          <>
            <Panel>
              <PanelHeader
                title="Road state"
                info={
                  <>
                    Density is vehicles in view at once, not vehicles per
                    kilometre — no camera here is calibrated, so there is no
                    honest conversion between the two. {traffic?.note}
                  </>
                }
              />
              {loading && !traffic ? (
                <div className="mt-3">
                  <SkeletonRows rows={5} />
                </div>
              ) : !traffic || traffic.zones.length === 0 ? (
                <div className="mt-3">
                  <EmptyState title="No traffic observed in this window." />
                </div>
              ) : (
                <div className="mt-3">
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
                            <Td className="text-right font-mono tabular-nums">
                              {zone.vehicles.toLocaleString('en-IN')}
                            </Td>
                            {/* Rounded: a vehicle count per hour arrives as a
                                float and "833.33 vehicles" claims a precision
                                the measurement does not have. */}
                            <Td className="text-right font-mono tabular-nums">
                              {zone.vehicles_per_hour === null
                                ? '—'
                                : Math.round(zone.vehicles_per_hour).toLocaleString('en-IN')}
                            </Td>
                            <Td className="text-right font-mono tabular-nums">
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
                            <Td className="text-right font-mono tabular-nums">
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
                            <Td className="text-right font-mono tabular-nums">
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
                </div>
              )}
            </Panel>

            {traffic && traffic.possible_obstructions.length > 0 && (
              <Panel>
                <PanelHeader
                  title="Possible obstructions"
                  info="Vehicles that stopped and stayed stopped. The system can see that they did not move, not why — a breakdown, a delivery and a police stop all look identical from here. Never an accident, and never a claim about a person."
                />
                <div className="mt-3">
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
                          <Td className="capitalize">{row.vehicle_type ?? '—'}</Td>
                          <Td className="font-mono text-xs">{row.plate ?? '—'}</Td>
                          <Td className="text-right font-mono tabular-nums">
                            {seconds(row.stationary_seconds)}
                          </Td>
                          <Td className="text-right text-[11px] tabular-nums text-muted-foreground">
                            {api.formatIST(row.last_seen, false)}
                          </Td>
                        </Tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </Panel>
            )}
          </>
        )}

        {tab === 'journeys' && (
          <>
            <Panel>
              <PanelHeader
                title="Route density"
                caption="Camera-to-camera movements, ranked by distinct vehicles."
                info="Distances are straight-line between cameras, so they are a floor on the road distance actually driven — which is why every distance here is prefixed ≥."
              />
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
                              ? (pair.to_corridor ?? '—')
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

            <Panel>
              <PanelHeader
                title="Travel time vs baseline"
                caption={`Against the same time of day on the previous ${travelTime?.baseline_window_days ?? 7} days.`}
              />
              {loading && !travelTime ? (
                <div className="mt-3">
                  <SkeletonRows rows={3} height="h-16" />
                </div>
              ) : !travelTime || travelTime.segments.length === 0 ? (
                <div className="mt-3">
                  <EmptyState title="No segments observed in this window." />
                </div>
              ) : (
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {travelTime.segments.slice(0, 12).map((segment) => (
                    <div
                      key={`${segment.from_camera}-${segment.to_camera}`}
                      className="rounded-md border border-border px-3 py-2.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-xs font-semibold">
                          {segment.from_camera} → {segment.to_camera}
                        </span>
                        {segment.status === 'ok' ? (
                          <Badge
                            tone={
                              segment.delta_pct !== null && segment.delta_pct > 15
                                ? 'danger'
                                : segment.delta_pct !== null && segment.delta_pct < -15
                                  ? 'success'
                                  : 'neutral'
                            }
                          >
                            {segment.delta_pct !== null
                              ? `${segment.delta_pct > 0 ? '+' : ''}${segment.delta_pct}%`
                              : 'baseline'}
                          </Badge>
                        ) : (
                          <Badge tone="neutral">{segment.status.replace('_', ' ')}</Badge>
                        )}
                      </div>
                      <p className="mt-1 truncate text-[11px] text-muted-foreground">
                        {segment.corridor ?? 'no corridor'}
                      </p>
                      <div className="mt-2 flex items-baseline gap-4">
                        <div>
                          <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                            Now
                          </p>
                          <p className="font-mono text-lg tabular-nums">
                            {seconds(segment.current_seconds)}
                          </p>
                        </div>
                        <div>
                          <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                            Baseline
                          </p>
                          <p className="font-mono text-sm tabular-nums text-muted-foreground">
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
            </Panel>
          </>
        )}

        {tab === 'forecast' && (
          <>
            <Panel>
              <PanelHeader title="Corridor speed" info={speed?.provenance.note} />
              {loading && !speed ? (
                <div className="mt-3">
                  <SkeletonRows rows={1} height="h-48" />
                </div>
              ) : okCorridors.length === 0 ? (
                <div className="mt-3">
                  <EmptyState
                    title="No corridor has a published speed figure in this window."
                    hint="Every observed leg was excluded as physically implausible. This is the expected state on replayed demo footage, and resolves unchanged on real camera timing."
                  />
                </div>
              ) : (
                <div className="mt-3 h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={okCorridors} margin={{ ...CHART_MARGIN, top: 20 }}>
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
                        width={Y_AXIS_WIDTH}
                        label={{
                          value: 'km/h',
                          angle: -90,
                          position: 'insideLeft',
                          style: { fontSize: 10, fill: 'hsl(var(--muted-foreground))' },
                        }}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        cursor={{ fill: 'hsl(var(--muted) / 0.3)' }}
                      />
                      {/* One series, so no legend — the title names it.
                          Direct labels instead, which beat a tooltip when
                          there are a handful of bars to compare at a glance. */}
                      <Bar
                        dataKey="median_kmph"
                        name="median km/h"
                        fill={SERIES_COLORS[0]}
                        radius={[4, 4, 0, 0]}
                      >
                        <LabelList
                          dataKey="median_kmph"
                          position="top"
                          style={{ fontSize: 11, fill: 'hsl(var(--foreground))' }}
                        />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {shortCorridors.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-muted-foreground">No figure yet:</span>
                  {shortCorridors.map((c) => (
                    <Badge key={c.corridor} tone="neutral" title={`${c.samples} plausible leg(s)`}>
                      {c.corridor}
                    </Badge>
                  ))}
                </div>
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Predicted congestion"
                caption="Current volume against this camera's own typical volume for this hour."
                info="100% is normal for this hour of day, not an absolute road-capacity reading — no capacity data exists to compute one. The +15 and +30 min figures are a straight-line trend through the last 30 minutes, and the error beside each is that same method checked against buckets already inside this window."
              />
              {loading && !congestion ? (
                <div className="mt-3">
                  <SkeletonRows rows={3} height="h-20" />
                </div>
              ) : !congestion || congestion.series.length === 0 ? (
                <div className="mt-3">
                  <EmptyState title="No cameras with enough recent volume to forecast." />
                </div>
              ) : (
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {congestion.series.slice(0, 12).map((c) => (
                    <div key={c.key} className="rounded-md border border-border px-3 py-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-xs font-semibold">{c.label}</span>
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
                        <p className="mt-1 truncate text-[11px] text-muted-foreground">
                          {c.corridor}
                        </p>
                      )}
                      <div className="mt-2 flex items-baseline gap-4">
                        <div>
                          <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                            Now
                          </p>
                          <p className="font-mono text-lg tabular-nums">
                            {c.current_index_pct !== null
                              ? `${Math.round(c.current_index_pct)}%`
                              : '—'}
                          </p>
                        </div>
                        {c.forecasts.map((f) => (
                          <div key={f.horizon_minutes}>
                            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                              +{f.horizon_minutes} min
                            </p>
                            <p className="font-mono text-sm tabular-nums text-muted-foreground">
                              {f.index_pct !== null ? `${Math.round(f.index_pct)}%` : '—'}
                            </p>
                          </div>
                        ))}
                      </div>
                      {/* Optional chaining, not just the length guard: this
                          project compiles with `noUncheckedIndexedAccess`, so
                          an index read is `T | undefined` however it is
                          guarded. */}
                      {c.factors.length > 0 && (
                        <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
                          {c.factors[0]?.detail}
                        </p>
                      )}
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {c.backtest.status === 'ok' && c.backtest.mae_pct !== null
                          ? `Backtested error: ±${c.backtest.mae_pct} pts (${c.backtest.samples} held-out buckets)`
                          : 'Not enough history yet to backtest this forecast.'}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Vehicle mix as one hundred-percent bar rather than five equal boxes.
 *
 * This is parts-of-a-whole data, and five boxes each showing a count and its
 * own percentage made the reader compose the whole in their head. A single
 * stacked bar shows the composition directly; the legend beside it carries the
 * identity and the numbers, so the bar is never read by colour alone.
 */
function VehicleMix({ traffic }: { traffic: TrafficResponse }) {
  const rows = (
    [
      ['Car', traffic.totals_by_type.car],
      ['Motorcycle', traffic.totals_by_type.motorcycle],
      ['Bus', traffic.totals_by_type.bus],
      ['Truck', traffic.totals_by_type.truck],
      ['Other', traffic.totals_by_type.other],
    ] as const
  )
    .map(([label, count], i) => ({
      label,
      count,
      pct: traffic.total_vehicles > 0 ? (count / traffic.total_vehicles) * 100 : 0,
      colour: label === 'Other' ? OTHER_COLOR : SERIES_COLORS[i],
    }))
    .filter((row) => row.count > 0)

  return (
    <div className="mt-3">
      <div className="flex h-8 gap-0.5 overflow-hidden rounded">
        {rows.map((row) => (
          <div
            key={row.label}
            title={`${row.label}: ${row.count.toLocaleString('en-IN')} (${Math.round(row.pct)}%)`}
            style={{ width: `${row.pct}%`, background: row.colour }}
            className="h-full first:rounded-l last:rounded-r"
          />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-5 gap-y-1.5 sm:grid-cols-3">
        {rows.map((row) => (
          <li key={row.label} className="flex items-baseline gap-1.5 text-xs">
            <span
              aria-hidden
              className="h-2.5 w-2.5 shrink-0 translate-y-px rounded-sm"
              style={{ background: row.colour }}
            />
            <span className="truncate text-muted-foreground">{row.label}</span>
            <span className="ml-auto font-mono tabular-nums">
              {row.count.toLocaleString('en-IN')}
            </span>
            <span className="w-9 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
              {Math.round(row.pct)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
