/**
 * A reconstructed journey, drawn.
 *
 * ## The one thing this must not do
 *
 * It must not draw a route that looks like it followed the roads. The
 * correlator has no road network: it knows a vehicle was at camera A and later
 * at camera B, and nothing whatever about the path between them. Rendering a
 * smooth road-shaped line would be the map asserting evidence that does not
 * exist, and a route map is exactly the artefact somebody would put in front of
 * a court.
 *
 * So the legs are drawn **dashed and straight**, with the caption saying why.
 * Dashes read as "inferred" to anyone who has used a mapping tool, and the
 * straight line is literally what is known: two endpoints.
 *
 * Legs the correlator could not believe are drawn in red. They are the ones
 * worth looking at — an impossible leg is what a cloned plate looks like.
 */

import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  applyBasemap,
  baseStyle,
  GUJARAT_CENTER,
  satelliteReachable,
  type Basemap,
} from '@/lib/basemap'
import {
  buildTimeline,
  istClock,
  journeySeconds,
  positionAt,
  travelledPath,
} from '@/lib/journey'
import type { RouteGeoJSON, RouteHop } from '@/lib/types'

interface Props {
  route: RouteGeoJSON | null
  hops: RouteHop[]
  basemap?: Basemap
  /** Hop the operator is hovering in the table, highlighted on the map. */
  activeSequence?: number | null
  onSelectHop?: (sequence: number) => void
}

const PLAUSIBLE = '#38bdf8'
const IMPLAUSIBLE = '#ef4444'
const TRAVELLED = '#fbbf24'

/**
 * Wall-clock seconds the whole journey takes to play back.
 *
 * Playback is **proportional to real elapsed time**, not one step per hop: a
 * four-minute leg takes eight times as long to cross as a thirty-second one.
 * Stepping uniformly per hop would be easier and would quietly misrepresent the
 * journey, making a vehicle that sat at a junction for ten minutes look like it
 * drove straight through.
 */
const PLAYBACK_SECONDS = 16

export default function RouteMap({
  route,
  hops,
  basemap = 'satellite',
  activeSequence = null,
  onSelectHop,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  const vehicle = useRef<maplibregl.Marker | null>(null)
  const [ready, setReady] = useState(false)
  const [effective, setEffective] = useState<Basemap>(basemap)

  // ── Playback ────────────────────────────────────────────────────────
  const [playing, setPlaying] = useState(false)
  /** Position along the journey, in seconds since the first sighting. */
  const [playhead, setPlayhead] = useState(0)

  const timeline = useMemo(() => buildTimeline(hops), [hops])
  const totalSeconds = journeySeconds(timeline)
  const canPlay = totalSeconds > 0

  // Reset when a different journey is loaded.
  useEffect(() => {
    setPlaying(false)
    setPlayhead(0)
  }, [hops])

  // Advance the playhead. requestAnimationFrame rather than setInterval so the
  // marker moves smoothly and stops costing anything when the tab is hidden.
  useEffect(() => {
    if (!playing || totalSeconds <= 0) return
    let frame = 0
    let previous = performance.now()
    const rate = totalSeconds / PLAYBACK_SECONDS

    const tick = (now: number) => {
      const delta = (now - previous) / 1000
      previous = now
      setPlayhead((current) => {
        const next = current + delta * rate
        if (next >= totalSeconds) {
          setPlaying(false)
          return totalSeconds
        }
        return next
      })
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing, totalSeconds])

  // ── Create the map once ─────────────────────────────────────────────
  useEffect(() => {
    if (map.current || !container.current) return
    map.current = new maplibregl.Map({
      container: container.current,
      style: baseStyle(),
      center: GUJARAT_CENTER,
      zoom: 6.2,
      attributionControl: false,
    })
    map.current.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'top-right',
    )
    map.current.addControl(new maplibregl.AttributionControl({ compact: true }))
    map.current.on('load', () => setReady(true))

    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  // Satellite is an enhancement; a venue with hostile wifi still gets a map.
  useEffect(() => {
    if (!ready || basemap !== 'satellite') {
      setEffective(basemap)
      return
    }
    let cancelled = false
    void satelliteReachable().then((ok) => {
      if (!cancelled) setEffective(ok ? 'satellite' : 'offline')
    })
    return () => {
      cancelled = true
    }
  }, [ready, basemap])

  useEffect(() => {
    if (ready && map.current) applyBasemap(map.current, effective)
  }, [ready, effective])

  // ── Draw the route ──────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return

    for (const marker of markers.current) marker.remove()
    markers.current = []

    const lineFeatures = (route?.features ?? []).filter(
      (f) => f.geometry.type === 'LineString',
    )

    // One feature per leg rather than one line for the whole route, so a leg
    // the correlator disbelieved can be coloured differently from one it did
    // not. A single line would have to be all one colour, which would either
    // hide the impossible leg or condemn the whole journey.
    const legs: GeoJSON.Feature[] = []
    for (let i = 1; i < hops.length; i += 1) {
      const from = hops[i - 1]
      const to = hops[i]
      if (!from || !to) continue
      legs.push({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [from.lon, from.lat],
            [to.lon, to.lat],
          ],
        },
        properties: {
          sequence: i,
          plausible: to.plausible,
          implied_kmph: to.implied_kmph,
          flags: to.flags.join(', '),
        },
      })
    }

    const collection: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: legs,
    }

    const existing = m.getSource('route-legs') as maplibregl.GeoJSONSource | undefined
    if (existing) {
      existing.setData(collection)
    } else {
      m.addSource('route-legs', { type: 'geojson', data: collection })
      m.addLayer({
        id: 'route-legs',
        type: 'line',
        source: 'route-legs',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': [
            'case',
            ['get', 'plausible'],
            PLAUSIBLE,
            IMPLAUSIBLE,
          ],
          'line-width': 3,
          // Dashed, always. The path between two cameras is inferred, and a
          // solid line would claim we know the roads taken.
          'line-dasharray': [2, 1.6],
          'line-opacity': 0.9,
        },
      })
    }

    // ── Hop markers: HTML, because a symbol layer would need a glyph server ──
    hops.forEach((hop, index) => {
      const element = document.createElement('button')
      element.type = 'button'
      const impossible = !hop.plausible
      const active = activeSequence === index
      element.className =
        'flex h-6 w-6 cursor-pointer items-center justify-center rounded-full ' +
        'border-2 text-[10px] font-bold shadow transition ' +
        (impossible
          ? 'border-red-400 bg-red-500 text-white '
          : 'border-sky-300 bg-sky-500 text-white ') +
        (active ? 'scale-150 ring-2 ring-white' : '')
      element.textContent = String(index + 1)
      element.title = `${hop.camera_code} — ${hop.camera_name}`
      element.addEventListener('click', () => onSelectHop?.(index))

      markers.current.push(
        new maplibregl.Marker({ element }).setLngLat([hop.lon, hop.lat]).addTo(m),
      )
    })

    // ── Frame the journey ──
    if (hops.length === 1) {
      const only = hops[0]
      if (only) m.easeTo({ center: [only.lon, only.lat], zoom: 13, duration: 600 })
    } else if (hops.length > 1) {
      const bounds = new maplibregl.LngLatBounds()
      for (const hop of hops) bounds.extend([hop.lon, hop.lat])
      m.fitBounds(bounds, { padding: 64, maxZoom: 14, duration: 600 })
    }

    void lineFeatures
  }, [ready, route, hops, activeSequence, onSelectHop])

  // ── Playback overlay: the moving vehicle, and the path behind it ─────
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return

    const here = positionAt(hops, timeline, playhead)

    // The path travelled so far, drawn solid over the dashed inferred legs.
    // Solid is defensible here for a different reason than the legs: it marks
    // how far through the *timeline* playback has reached, not which roads
    // were used.
    const travelled: GeoJSON.Feature[] = []
    const path = travelledPath(hops, timeline, playhead)
    if (path.length > 1) {
      travelled.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: path },
        properties: {},
      })
    }

    const collection: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: travelled,
    }
    const source = m.getSource('route-travelled') as maplibregl.GeoJSONSource | undefined
    if (source) {
      source.setData(collection)
    } else {
      m.addSource('route-travelled', { type: 'geojson', data: collection })
      m.addLayer({
        id: 'route-travelled',
        type: 'line',
        source: 'route-travelled',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': TRAVELLED, 'line-width': 4, 'line-opacity': 0.85 },
      })
    }

    // The vehicle itself. Only while a journey is being played back — a static
    // marker sitting on the first camera would read as a sighting.
    if (!here || !canPlay || (playhead === 0 && !playing)) {
      vehicle.current?.remove()
      vehicle.current = null
      return
    }

    if (!vehicle.current) {
      const element = document.createElement('div')
      element.className =
        'h-4 w-4 rounded-full border-2 border-white bg-amber-400 shadow-lg ' +
        'ring-4 ring-amber-400/30'
      element.title = 'Vehicle position, interpolated between sightings'
      vehicle.current = new maplibregl.Marker({ element }).setLngLat(here).addTo(m)
    } else {
      vehicle.current.setLngLat(here)
    }
  }, [ready, hops, timeline, playhead, playing, canPlay])

  // Remove the vehicle marker when the component goes away.
  useEffect(
    () => () => {
      vehicle.current?.remove()
      vehicle.current = null
    },
    [],
  )

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full rounded-md" />

      {hops.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded bg-black/70 px-3 py-2 text-xs text-muted-foreground">
            No sightings in this window.
          </p>
        </div>
      )}

      {canPlay && (
        <div className="absolute left-2 right-2 top-2 flex items-center gap-2 rounded bg-black/75 px-2 py-1.5 backdrop-blur">
          <button
            type="button"
            onClick={() => {
              // Replay from the start once it has run to the end, rather than
              // sitting on the final frame doing nothing when pressed.
              if (!playing && playhead >= totalSeconds) setPlayhead(0)
              setPlaying((p) => !p)
            }}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-amber-400 text-black transition hover:bg-amber-300"
            aria-label={playing ? 'Pause journey playback' : 'Play journey playback'}
            title={playing ? 'Pause' : 'Play the journey'}
          >
            <span className="text-[11px] leading-none">{playing ? '❚❚' : '▶'}</span>
          </button>

          <input
            type="range"
            min={0}
            max={Math.max(1, Math.round(totalSeconds))}
            step={1}
            value={Math.round(playhead)}
            onChange={(event) => {
              setPlaying(false)
              setPlayhead(Number(event.target.value))
            }}
            className="h-1 flex-1 cursor-pointer accent-amber-400"
            aria-label="Journey timeline"
          />

          <span className="shrink-0 font-mono text-[11px] tabular-nums text-amber-200">
            {hops[0] ? istClock(hops[0].arrived_at, playhead) : '--:--:--'}
          </span>
        </div>
      )}

      {hops.length > 1 && (
        <div className="pointer-events-none absolute bottom-2 left-2 max-w-md rounded bg-black/70 px-2 py-1.5 text-[10px] leading-relaxed text-white/80">
          Dashed straight legs join consecutive cameras. They are{' '}
          <strong>not the roads driven</strong> — the platform knows where the
          vehicle was seen, not how it got there, so distances and speeds are
          lower bounds.
          {hops.some((h) => !h.plausible) && (
            <>
              {' '}
              <span className="text-red-300">Red legs are impossible</span> and need
              explaining: a cloned plate looks exactly like this.
            </>
          )}
        </div>
      )}

      {effective === 'offline' && basemap === 'satellite' && (
        <span className="absolute right-2 top-2 rounded bg-black/70 px-2 py-0.5 text-[10px] text-amber-300">
          satellite unreachable — offline basemap
        </span>
      )}
    </div>
  )
}
