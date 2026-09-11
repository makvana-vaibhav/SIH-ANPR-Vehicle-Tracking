/**
 * Vehicle search: a plate in, a journey out.
 *
 * This is Judge Moment 4 — type a plate, get every sighting and a route drawn
 * across cameras with timestamps and implied speeds.
 *
 * ## What this screen is careful about
 *
 * The route is inference, and the screen says so in three places rather than
 * one: the map caption, the hop table's speed column, and the summary. That
 * repetition is deliberate. Somebody reading a printed screenshot of this in a
 * case file will not have the docstring, and "37 km/h between Rajkot and
 * Gondal" reads as a measurement unless it is told otherwise.
 *
 * Legs the correlator could not believe are shown, in red, with the reason.
 * They are not filtered out — an impossible leg is the signature of a cloned
 * plate, and hiding it would remove the most useful thing on the page.
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'

import RouteMap from '@/components/RouteMap'
import { useToast } from '@/components/Toast'
import { Button, ErrorBanner, EmptyState, Field, Input, SegmentedControl } from '@/components/ui'
import * as api from '@/lib/api'
import type { Convoy, RouteGeoJSON, VehicleRoute } from '@/lib/types'

/** Windows an operator actually asks for. */
const WINDOWS = [
  { value: '1', label: '1 h', hours: 1 },
  { value: '6', label: '6 h', hours: 6 },
  { value: '24', label: '24 h', hours: 24 },
  { value: '168', label: '7 d', hours: 168 },
  { value: '720', label: '30 d', hours: 720 },
] as const

/** Plain-English reasons, so a flag never appears as a bare identifier. */
const FLAG_TEXT: Record<string, string> = {
  implausible_speed:
    'Faster than a vehicle can travel — and the distance used is a straight line, so the real journey was longer still.',
  impossible_simultaneous:
    'The same plate at two separated cameras at the same instant. One vehicle cannot do this.',
  revisit:
    'The vehicle returned to a camera it had already passed — it doubled back, or waited nearby and came back.',
  co_located:
    'Two different cameras within 50 m of each other; a speed here would be position error, not motion.',
  unobserved_gap: 'Over an hour with no sighting — the vehicle passed through unwatched roads.',
  heading_conflict:
    'The camera faces away from the direction of travel, so the reading may belong to a different carriageway.',
}

function since(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hrs = Math.floor(minutes / 60)
  return `${hrs}h ${minutes % 60}m`
}

export default function VehicleSearch() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [plate, setPlate] = useState<string | null>(null)
  const [hours, setHours] = useState<number>(24)

  const [route, setRoute] = useState<VehicleRoute | null>(null)
  const [geojson, setGeojson] = useState<RouteGeoJSON | null>(null)
  const [convoys, setConvoys] = useState<Convoy[]>([])
  const [suggestions, setSuggestions] = useState<{ plate: string; cameras: number }[]>([])
  const [activeHop, setActiveHop] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Which plates have a route worth drawing, over the window actually being
  // searched. Computing these over a longer window than the search would
  // promise a multi-camera route and then deliver a single-camera one.
  useEffect(() => {
    async function load() {
      try {
        setSuggestions((await api.getRoutablePlates(2, 12, since(hours))).plates)
      } catch {
        /* the search still works without suggestions */
      }
    }
    void load()
  }, [hours])

  const search = useCallback(async (target: string, windowHours: number) => {
    const normalised = target.toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (normalised.length < 4) {
      toast.error('A plate needs at least four letters or digits to search on.')
      return
    }

    setBusy(true)
    setError(null)
    setActiveHop(null)
    try {
      const window = { since: since(windowHours) }
      const [document, shape, convoyReport] = await Promise.all([
        api.getVehicleRoute(normalised, window),
        api.getVehicleRouteGeoJSON(normalised, window),
        api.getConvoys(normalised, { ...window, min_shared_cameras: 2 }),
      ])
      setPlate(normalised)
      setRoute(document)
      setGeojson(shape)
      setConvoys(convoyReport.convoys)
    } catch (err) {
      toast.error(err)
      setRoute(null)
      setGeojson(null)
      setConvoys([])
    } finally {
      setBusy(false)
    }
  }, [toast])

  function submit(event: FormEvent) {
    event.preventDefault()
    void search(query, hours)
  }

  function changeWindow(next: number) {
    setHours(next)
    if (plate) void search(plate, next)
  }

  const hops = route?.hops ?? []
  // A window spanning midnight makes time-only stamps look out of order
  // (17:49 then 12:32), so the date is shown whenever the route crosses a day.
  const spansDays =
    hops.length > 1 &&
    new Date(hops[0]!.arrived_at).toDateString() !==
      new Date(hops[hops.length - 1]!.arrived_at).toDateString()

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <header>
        <h1 className="text-xl font-semibold">Vehicle search</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Every sighting of a plate, and the journey they imply.
        </p>
      </header>

      {/* ── Search ──────────────────────────────────────────────────── */}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Field label="Plate">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value.toUpperCase())}
              placeholder="GJ03AB1234"
              className="font-mono uppercase"
            />
          </Field>
        </div>

        <div>
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Window
          </span>
          <div className="mt-1">
            <SegmentedControl
              value={String(hours)}
              onChange={(v) => changeWindow(Number(v))}
              options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
            />
          </div>
        </div>

        <Button type="submit" disabled={busy}>
          {busy ? 'Searching…' : 'Search'}
        </Button>
      </form>

      {suggestions.length > 0 && !route && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {suggestions.length > 0
              ? 'Plates seen on more than one camera in this window'
              : 'No plate was seen on more than one camera in this window'}
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {suggestions.map((s) => (
              <li key={s.plate}>
                <button
                  type="button"
                  onClick={() => {
                    setQuery(s.plate)
                    void search(s.plate, hours)
                  }}
                  className="rounded border border-border px-2 py-0.5 font-mono text-[11px] transition hover:border-primary"
                >
                  {s.plate}
                  <span className="ml-1 text-muted-foreground">{s.cameras} cams</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {error && <ErrorBanner>{error}</ErrorBanner>}

      {route && (
        <>
          {/* ── Summary ───────────────────────────────────────────── */}
          <section className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-border bg-card px-4 py-3">
            <div>
              <p className="font-mono text-lg font-bold tracking-wide">{route.plate}</p>
              <p className="text-[10px] text-muted-foreground">
                {route.hop_count} sighting{route.hop_count === 1 ? '' : 's'} on{' '}
                {route.camera_count} camera{route.camera_count === 1 ? '' : 's'}
              </p>
            </div>

            {route.hop_count > 0 && (
              <>
                {/* First and last seen were already in the API payload and in
                    the TS type, and were rendered on no screen — so the one
                    question an operator asks first ("when was it about?") had
                    no answer in the product. */}
                {route.first_seen && (
                  <Stat label="First seen" value={api.formatIST(route.first_seen, false)} />
                )}
                {route.last_seen && (
                  <Stat label="Last seen" value={api.formatIST(route.last_seen, false)} />
                )}
                {route.camera_count > 1 && (
                  <Stat
                    label="Straight-line"
                    value={`≥ ${route.distance_km} km`}
                    hint="Roads are never shorter than the straight line, so this is a floor."
                  />
                )}
                <Stat label="Elapsed" value={duration(route.duration_s)} />
                {route.average_kmph !== null && (
                  <Stat
                    label="Average speed"
                    value={`≥ ${route.average_kmph} km/h`}
                    hint={
                      'First sighting to last, so it includes time parked at a junction. ' +
                      'Distances are straight-line. Both push this down, so the real ' +
                      'average is at least this.' +
                      (route.moving_kmph !== null && route.moving_kmph !== route.average_kmph
                        ? ` Excluding dwell: ≥ ${route.moving_kmph} km/h.`
                        : '')
                    }
                  />
                )}
                <Stat
                  label="Confidence"
                  value={route.confidence.toFixed(2)}
                  hint="Bounded by the weakest plate read in the route."
                />
                <span
                  className={`rounded px-2 py-1 text-[11px] font-medium ${
                    route.is_plausible
                      ? 'bg-status-online/15 text-status-online'
                      : 'bg-status-offline/15 text-status-offline'
                  }`}
                >
                  {route.is_plausible
                    ? 'no impossible legs'
                    : `${route.flagged_hop_count} leg(s) need explaining`}
                </span>
              </>
            )}
          </section>

          {route.hop_count === 0 ? (
            <EmptyState
              title={
                <>
                  <span className="font-mono">{route.plate}</span> was not seen in this window.
                </>
              }
              hint="Try a longer window, or pick a plate from the list above."
            />
          ) : (
            <>
            {route.camera_count === 1 && (
              <p className="rounded border border-priority-high/40 bg-priority-high/10 px-4 py-2 text-xs text-priority-high">
                All {route.hop_count} sightings are on one camera, so there is no
                journey to draw — only a record of the vehicle passing{' '}
                {route.hops[0]?.camera_code} more than once. A route needs the plate
                read on cameras in different places.
              </p>
            )}
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
              <section className="h-[460px]">
                <RouteMap
                  route={geojson}
                  hops={hops}
                  activeSequence={activeHop}
                  onSelectHop={setActiveHop}
                />
              </section>

              {/* ── Hop table ─────────────────────────────────────── */}
              <section className="space-y-3">
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Journey
                  </h2>
                  <ol className="mt-1.5 space-y-1.5">
                    {hops.map((hop, index) => (
                      <li
                        key={`${hop.camera_id}-${hop.arrived_at}`}
                        onMouseEnter={() => setActiveHop(index)}
                        onMouseLeave={() => setActiveHop(null)}
                        className={`rounded-md border px-3 py-2 transition ${
                          hop.plausible
                            ? 'border-border bg-card'
                            : 'border-status-offline/50 bg-status-offline/5'
                        } ${activeHop === index ? 'ring-1 ring-primary' : ''}`}
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="flex items-baseline gap-2">
                            <span
                              className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${
                                hop.plausible ? 'bg-sky-500' : 'bg-red-500'
                              }`}
                            >
                              {index + 1}
                            </span>
                            <span className="font-mono text-xs font-semibold">
                              {hop.camera_code}
                            </span>
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            {api.formatIST(hop.arrived_at, spansDays)}
                          </span>
                        </div>

                        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                          {hop.camera_name}
                          {hop.city ? ` · ${hop.city}` : ''}
                        </p>

                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                          <span>
                            {hop.sightings} read{hop.sightings === 1 ? '' : 's'}
                          </span>
                          <span>conf {hop.plate_confidence.toFixed(2)}</span>
                          {hop.dwell_s > 0 && <span>dwell {duration(hop.dwell_s)}</span>}
                          {hop.distance_km !== null && (
                            <span>≥ {hop.distance_km} km from previous</span>
                          )}
                          {hop.implied_kmph !== null && (
                            <span
                              className={hop.plausible ? '' : 'font-semibold text-status-offline'}
                              title="Straight-line distance over elapsed time: a lower bound on the speed actually driven."
                            >
                              ≥ {hop.implied_kmph} km/h
                            </span>
                          )}
                        </div>

                        {hop.flags.length > 0 && (
                          <ul className="mt-1.5 space-y-0.5">
                            {hop.flags.map((flag) => (
                              <li
                                key={flag}
                                className={`text-[10px] leading-snug ${
                                  flag === 'implausible_speed' ||
                                  flag === 'impossible_simultaneous'
                                    ? 'text-status-offline'
                                    : 'text-priority-high'
                                }`}
                              >
                                {FLAG_TEXT[flag] ?? flag}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>

                {/* ── Convoys ───────────────────────────────────────── */}
                {convoys.length > 0 &&
                  (() => {
                    const real = convoys.filter((c) => !c.likely_same_vehicle)
                    const misreads = convoys.filter((c) => c.likely_same_vehicle)
                    return (
                      <div>
                        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          Seen alongside
                        </h2>
                        <p className="mt-0.5 text-[10px] text-muted-foreground">
                          Co-occurrence is evidence of association, not proof of it.
                        </p>

                        {real.length === 0 && (
                          <p className="mt-1.5 text-[11px] text-muted-foreground">
                            No other vehicle travelled with this one.
                          </p>
                        )}

                        <ul className="mt-1.5 space-y-1">
                          {real.map((convoy) => (
                            <li
                              key={convoy.with_plate}
                              className="flex items-baseline justify-between gap-2 rounded border border-border px-2 py-1"
                            >
                              <button
                                type="button"
                                onClick={() => {
                                  setQuery(convoy.with_plate)
                                  void search(convoy.with_plate, hours)
                                }}
                                className="font-mono text-xs hover:text-primary"
                              >
                                {convoy.with_plate}
                              </button>
                              <span className="text-[10px] text-muted-foreground">
                                {convoy.shared_cameras} shared cameras ·{' '}
                                {Math.round(convoy.median_gap_s)}s apart
                              </span>
                            </li>
                          ))}
                        </ul>

                        {/* Kept visible rather than filtered away: a cluster of
                            near-identical plates is a measurement of how often
                            the reader is disagreeing with itself on this
                            vehicle, which is worth an operator knowing. */}
                        {misreads.length > 0 && (
                          <details className="mt-2">
                            <summary className="cursor-pointer text-[10px] text-muted-foreground">
                              {misreads.length} near-identical plate
                              {misreads.length === 1 ? '' : 's'} — almost certainly
                              this same vehicle read differently, not a convoy
                            </summary>
                            <ul className="mt-1 flex flex-wrap gap-1">
                              {misreads.map((convoy) => (
                                <li
                                  key={convoy.with_plate}
                                  className="rounded border border-border/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                                >
                                  {convoy.with_plate}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </div>
                    )
                  })()}

                <p className="rounded border border-border/60 px-2 py-1.5 text-[10px] leading-relaxed text-muted-foreground">
                  {route.geometry_note}
                </p>
              </section>
            </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  return (
    <div title={hint}>
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="font-mono text-sm">{value}</p>
    </div>
  )
}
