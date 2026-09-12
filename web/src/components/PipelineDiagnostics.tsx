/**
 * Measured pipeline latency, so optimisation can be checked rather than claimed.
 *
 * ## Why this exists
 *
 * "The overlay feels delayed" is a symptom with at least four possible causes,
 * and they need completely different fixes:
 *
 *   * the worker is falling behind and reading old frames  → AI latency
 *   * events are slow reaching the browser                 → transport latency
 *   * the *picture* is behind, so a prompt box still looks late → video latency
 *   * the browser is dropping frames rendering it          → render latency
 *
 * Guessing between them wasted real time on this project: a codec mismatch
 * pushed video onto HLS's multi-second buffer and looked exactly like an AI
 * problem. Every number here is taken from a timestamp the pipeline already
 * carries — none is estimated, and where a figure cannot be measured it is
 * shown as `—` rather than filled in with something plausible.
 *
 * ## What each number means
 *
 * **AI** — `latency_ms` on the event: capture-to-event, measured by the worker
 * across decode, detection, tracking, plate detection, OCR and consensus. This
 * is the pipeline's own figure and the one to optimise.
 *
 * **Transport** — event creation to arrival in this browser: Redis, the API's
 * consumer, the fan-out and the socket. Computed from `event_time` against
 * local receipt, so it includes any clock skew between container and host;
 * treated as indicative, not exact, and labelled as such.
 *
 * **Video** — how far behind the source the *picture* is. Only the HLS path can
 * answer: MediaMTX stamps playlists with `EXT-X-PROGRAM-DATE-TIME`, so the
 * player can say what wall-clock moment the displayed frame was captured at.
 * WebRTC carries no such clock and reports `—`, which is honest: on WebRTC this
 * is typically a few hundred milliseconds, but this panel does not print
 * numbers it did not measure.
 *
 * **End-to-end** — AI + transport. The figure that decides whether a box can
 * land on the right vehicle.
 *
 * **Time to first plate / confirmed** — seconds from a vehicle's first
 * observation to its first plate reading, and to that reading crossing the
 * same bar the overlay uses for a checkmark (`event.plate.time_to_first_
 * read_s`/`time_to_confirmed_s`, computed once on the worker in
 * `Pipeline._read_plate`). This is the primary KPI: every other row above
 * times a single frame's trip through part of the pipeline; this times how
 * much of a vehicle's whole time on screen passed before the platform had
 * anything to show for it.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import type { LiveVehicleEvent } from '@/lib/types'

interface Props {
  /** Newest-first, as the event stream delivers them. */
  events: LiveVehicleEvent[]
  /** Capture clock of the frame on screen, or null when unavailable. */
  videoClock?: (() => number | null) | null
  /** Which transport the player settled on, for context. */
  transport?: string | null
}

/** Rolling window. Long enough to be stable, short enough to react. */
const SAMPLE_LIMIT = 40

function percentile(values: number[], fraction: number): number | null {
  if (values.length === 0) return null
  const ordered = [...values].sort((a, b) => a - b)
  const index = Math.min(
    ordered.length - 1,
    Math.max(0, Math.round(fraction * (ordered.length - 1))),
  )
  return ordered[index] ?? null
}

function ms(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  return `${Math.round(value)} ms`
}

export default function PipelineDiagnostics({
  events,
  videoClock,
  transport,
}: Props) {
  // Arrival times are recorded per event key as they are seen, so transport
  // latency is measured rather than inferred from the render pass.
  const arrivalsRef = useRef(new Map<string, number>())
  const [, setTick] = useState(0)

  // A slow repaint, not per-event: this panel must not itself cost latency.
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 1_000)
    return () => window.clearInterval(timer)
  }, [])

  const stats = useMemo(() => {
    const arrivals = arrivalsRef.current
    const now = Date.now()

    const ai: number[] = []
    const transportMs: number[] = []
    const timeToFirstRead: number[] = []
    const timeToConfirmed: number[] = []
    let withPlate = 0

    for (const event of events.slice(0, SAMPLE_LIMIT)) {
      const key = `${event.event_time}:${event.vehicle?.vehicle_id ?? ''}`
      let arrived = arrivals.get(key)
      if (arrived === undefined) {
        arrived = now
        arrivals.set(key, arrived)
      }

      if (typeof event.latency_ms === 'number') ai.push(event.latency_ms)

      const created = Date.parse(event.event_time)
      if (Number.isFinite(created)) {
        const hop = arrived - created
        // Negative means the container clock is ahead of the host's. Report
        // nothing rather than a nonsense figure.
        if (hop >= 0 && hop < 60_000) transportMs.push(hop)
      }
      if (event.plate?.text) withPlate += 1
      // How much of the vehicle's time on screen passed before it had a
      // reading at all, and before that reading was confident enough to
      // show a checkmark — the worker's own measurement (see
      // Track.first_read_latency_s), not derived here. This is the number
      // the fast-path display exists to minimise; `latency_ms` above times
      // one frame's trip to the bus, not a vehicle's whole transit.
      if (typeof event.plate?.time_to_first_read_s === 'number') {
        timeToFirstRead.push(event.plate.time_to_first_read_s * 1000)
      }
      if (typeof event.plate?.time_to_confirmed_s === 'number') {
        timeToConfirmed.push(event.plate.time_to_confirmed_s * 1000)
      }
    }

    // Bound the arrival map: it is keyed per event and would otherwise grow
    // for as long as the page is open.
    if (arrivals.size > SAMPLE_LIMIT * 8) {
      const live = new Set(
        events.map((e) => `${e.event_time}:${e.vehicle?.vehicle_id ?? ''}`),
      )
      for (const key of arrivals.keys()) if (!live.has(key)) arrivals.delete(key)
    }

    const videoNow = videoClock?.() ?? null
    const videoLag = videoNow === null ? null : now - videoNow

    const aiP50 = percentile(ai, 0.5)
    const transportP50 = percentile(transportMs, 0.5)

    return {
      samples: ai.length,
      aiP50,
      aiP90: percentile(ai, 0.9),
      transportP50,
      videoLag,
      endToEnd:
        aiP50 !== null && transportP50 !== null ? aiP50 + transportP50 : null,
      readRate: events.length > 0 ? withPlate / Math.min(events.length, SAMPLE_LIMIT) : null,
      timeToFirstReadP50: percentile(timeToFirstRead, 0.5),
      timeToConfirmedP50: percentile(timeToConfirmed, 0.5),
    }
  }, [events, videoClock])

  const rows: Array<[string, string]> = [
    ['AI (capture → event) p50', ms(stats.aiP50)],
    ['AI p90', ms(stats.aiP90)],
    ['Transport (event → browser)', ms(stats.transportP50)],
    ['Video behind source', ms(stats.videoLag)],
    ['End-to-end (AI + transport)', ms(stats.endToEnd)],
    // The primary KPI: how much of a vehicle's time in frame passes before it
    // has a reading at all, and before that reading is confirmed — as
    // opposed to every row above, which times one frame's trip through the
    // pipeline, not a vehicle's whole transit.
    ['Time to first plate p50', ms(stats.timeToFirstReadP50)],
    ['Time to confirmed p50', ms(stats.timeToConfirmedP50)],
  ]

  return (
    <div className="rounded border border-border bg-card/60 p-2 font-mono text-[10px] text-muted-foreground">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-sans font-semibold text-foreground">
          Pipeline latency
        </span>
        <span>
          {transport ?? '—'} · {stats.samples} samples
        </span>
      </div>
      <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt>{label}</dt>
            <dd className="text-right text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      {stats.videoLag === null && (
        <p className="mt-1 font-sans leading-snug">
          Video lag is only measurable on HLS, which carries a capture clock.
          WebRTC does not, so it is not estimated here.
        </p>
      )}
    </div>
  )
}
