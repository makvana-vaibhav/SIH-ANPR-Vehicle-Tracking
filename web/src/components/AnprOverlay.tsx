/**
 * Plate reads drawn over the live video.
 *
 * ## The timing problem, and what is done about it
 *
 * Inference happens on the worker, not in the browser. A vehicle is read
 * `latency_ms` after the frame was captured, while the video reaches the
 * element in well under a second. Drawn naively, every box therefore lands
 * behind the vehicle — sometimes after it has left the frame entirely.
 *
 * Two clocks are available, and the overlay uses whichever it is given:
 *
 * **Synced.** When the player can say what wall-clock moment the frame on
 * screen was captured at — `videoClock` — each box is scheduled against *that*
 * clock rather than against now. A box for a frame captured at 10:31:04.2 is
 * drawn while the element is showing 10:31:04.2, so it sits on the vehicle.
 * This requires the video to be running far enough behind live to cover the
 * pipeline, which is what the player's sync delay is for.
 *
 * **Unsynced.** WebRTC offers no such clock. The box is then shown on arrival
 * and held for a fixed window, which is honest but trails the picture. It is
 * labelled with its true age either way, so the two modes never disagree about
 * the facts — only about where the rectangle can be placed.
 *
 * ## Identity
 *
 * Boxes are keyed on the **track id**, not `vehicle_id`. The worker mints a
 * fresh `vehicle_id` for every event it emits, including the position
 * refreshes that follow one vehicle across the frame, so keying on it draws
 * the same car once per event — a trail of stale rectangles that never clears.
 * `track_ids[0]` is stable for as long as the tracker holds the vehicle.
 *
 * Coordinates arrive in source-frame pixels with the frame size alongside
 * them, so they are mapped through the same letterboxing the video element
 * applies with `object-contain`.
 */

import { useEffect, useRef, useState } from 'react'

import { liveTrackKey } from '@/lib/events'
import type { BBox, LiveVehicleEvent } from '@/lib/types'

/**
 * How long a box survives its last update.
 *
 * The worker refreshes a vehicle's position several times a second while it is
 * in view, so this only has to outlast the gap between refreshes plus some
 * jitter. Longer than that and a vehicle that has left the frame keeps a
 * rectangle floating where it no longer is.
 */
// Synced, the box is drawn on the very frame it was measured in, so it needs
// only to outlast the gap between position refreshes. Longer would leave a
// rectangle behind a car the picture has already moved past — the whole point
// of syncing is that it does not have to.
const HOLD_SYNCED_MS = 1_800

/**
 * Unsynced, the box cannot track the car, so it is held long enough to read.
 *
 * Six seconds, which is what this component used before it grew a video clock,
 * and the reason that version looked right. Inference lands ~270 ms after the
 * frame, but the *picture* reaches the browser over HLS several seconds later
 * still — so a box arrives before the car it describes is even on screen. A
 * six-second hold spans that offset: the box is already up when the vehicle
 * appears, and fades out after it has passed.
 *
 * Shortening it to 2.5 s is what made readings look like they were flashing up
 * and vanishing against the wrong cars.
 */
const HOLD_UNSYNCED_MS = 6_000

/** A retired vehicle is gone. Clear it promptly rather than waiting out the hold. */
const HOLD_AFTER_COMPLETED_MS = 700

/** Fade over the last of the hold, so boxes leave rather than vanish. */
const FADE_MS = 500

/** Drop a box from the store this long after it stopped being drawn. */
const PRUNE_AFTER_MS = 5_000

/**
 * Below this, a reading is shown in the feed but not drawn on the video.
 *
 * Deliberately *not* gated on `evidence.agreement`. That field is a fraction
 * (`reads_agreeing / reads_total`), not a count, and measured on the live feed
 * it is frequently 0.0 even for readings the pipeline is otherwise sure of —
 * `EY61NBG` arrives at 0.92 confidence over four reads with agreement 0.0. An
 * earlier version of this gate required `agreement >= 2`, which is
 * unsatisfiable for a 0..1 value: it silently suppressed **every** label.
 *
 * Grammar validity and the ambiguity flag are the signals that actually
 * separate a plate from a misread, and they are what this uses.
 */
const CONFIRM_CONFIDENCE = 0.8

/** Above this shared area, two boxes are treated as the same vehicle. */
const SAME_VEHICLE_OVERLAP = 0.55

interface Props {
  events: LiveVehicleEvent[]
  /** Hide the boxes without unmounting, so the toggle is instant. */
  enabled?: boolean
  /**
   * Epoch-ms capture time of the frame currently on screen, or null when the
   * transport cannot say. Read on every animation frame, so it must be cheap.
   */
  videoClock?: () => number | null
}

interface TrackBox {
  key: string
  plate: string
  confidence: number
  ambiguous: boolean
  correctedFrom: string | null
  /** Capture time of the frame this box was measured in, epoch ms. */
  capturedAt: number | null
  /** When this browser received it, epoch ms. The fallback clock. */
  arrivedAt: number
  /** Capture-to-event, as the worker measured it. */
  latencyMs: number | null
  completed: boolean
  /** The vehicle box — what an operator matches to a car on screen. */
  box: BBox
  /** The plate box, when the pipeline localised one. Null is normal. */
  plateBox: BBox | null
  /** Whether the reading has stabilised enough to put text on the video. */
  confirmed: boolean
  frame: { width: number; height: number }
}

/**
 * Has this reading settled enough to label the video with it?
 *
 * Consensus already votes per character across every frame a vehicle was read
 * in; this is the display gate on top of it. An unconfirmed reading still
 * appears in the feed beside the video with all its evidence — it simply does
 * not get text drawn over live traffic, because a plate that is still moving
 * between candidates is worse than no label at all.
 */
function isConfirmed(event: LiveVehicleEvent): boolean {
  const plate = event.plate
  if (!plate?.text) return false
  // Grammar and ambiguity are the pipeline's own verdicts on whether the
  // string is a plate at all, and they are decisive: `AP05JEO1` and
  // `KH0522431` both arrive with respectable confidence and are not plates.
  if (!plate.grammar_valid || plate.ambiguous) return false
  return (plate.confidence ?? 0) >= CONFIRM_CONFIDENCE
}

/**
 * The rectangle the video content actually occupies inside its element.
 *
 * `object-contain` letterboxes: a 4:3 camera in a 16:9 shell leaves bars down
 * the sides, and a box placed against the element rather than the content
 * lands in the bar.
 */
function contentRect(
  container: { width: number; height: number },
  frame: { width: number; height: number },
) {
  if (!frame.width || !frame.height || !container.width || !container.height) {
    return { left: 0, top: 0, width: container.width, height: container.height }
  }
  const scale = Math.min(container.width / frame.width, container.height / frame.height)
  const width = frame.width * scale
  const height = frame.height * scale
  return {
    left: (container.width - width) / 2,
    top: (container.height - height) / 2,
    width,
    height,
  }
}


/** Fraction of the smaller box that the two boxes share. */
function overlapFraction(a: BBox, b: BBox): number {
  const left = Math.max(a.x1, b.x1)
  const right = Math.min(a.x2, b.x2)
  const top = Math.max(a.y1, b.y1)
  const bottom = Math.min(a.y2, b.y2)
  if (right <= left || bottom <= top) return 0
  const intersection = (right - left) * (bottom - top)
  const areaA = Math.max(1, (a.x2 - a.x1) * (a.y2 - a.y1))
  const areaB = Math.max(1, (b.x2 - b.x1) * (b.y2 - b.y1))
  return intersection / Math.min(areaA, areaB)
}

/**
 * One box per vehicle, not one per reading.
 *
 * The tracker can hold the same car as more than one track, and each track
 * carries its own OCR result — measured on live footage, `AV06HVE` and
 * `AV08HVE` arrive together, both past grammar and both above the confidence
 * gate, because `0`/`8` is the classic confusion. Drawn straight, that is two
 * labelled rectangles on one car disagreeing with each other in front of the
 * viewer.
 *
 * Boxes covering mostly the same pixels are therefore the same vehicle, and
 * only the most confident reading is drawn. The others are not discarded from
 * the system — every one is in the feed beside the video with its evidence,
 * which is where a disagreement should be visible.
 */
function dedupeByVehicle<T extends { box: BBox; confidence: number }>(
  items: T[],
): T[] {
  const kept: T[] = []
  for (const item of [...items].sort((a, b) => b.confidence - a.confidence)) {
    if (kept.some((k) => overlapFraction(k.box, item.box) >= SAME_VEHICLE_OVERLAP)) {
      continue
    }
    kept.push(item)
  }
  return kept
}

function parseTime(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

export default function AnprOverlay({ events, enabled = true, videoClock }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  // Live boxes are held in a ref, not in state. They are written by the event
  // feed and read by an animation frame, and routing every position refresh
  // through setState would re-render the tree several times a second per
  // vehicle for data the render loop is about to read anyway.
  const tracksRef = useRef(new Map<string, TrackBox>())
  // What the last frame decided to draw. This *is* state, because it is what
  // React renders; it changes at most once per animation frame, and only when
  // something actually moved.
  const [visible, setVisible] = useState<Array<TrackBox & { opacity: number }>>([])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) setSize({ width: rect.width, height: rect.height })
    })
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  // ── Fold the event feed into one box per track ───────────────────────────
  //
  // `events` is newest-first and bounded, so this walks it oldest-first and
  // lets later events overwrite earlier ones. A vehicle in view is therefore
  // described by its most recent position, not its first.
  useEffect(() => {
    const tracks = tracksRef.current
    const now = Date.now()

    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      if (!event?.plate?.text) continue

      const frame = event.frame
      // Without the frame the boxes belong to, the coordinates cannot be
      // scaled. Drawing them against a guessed resolution would put them on
      // the wrong vehicle, so they are dropped instead.
      if (!frame?.width || !frame?.height) continue

      // The **vehicle** box, not the plate box.
      //
      // A plate box is ~90x22 px on a 1280px-wide picture — too small to
      // associate with a car at a glance, and it jitters between frames
      // because a small box amplifies small coordinate errors. The vehicle box
      // is what an operator can actually match to a car on screen. The plate
      // box is still carried in the event for anything that wants to zoom.
      const box = event.vehicle?.live_bbox ?? event.vehicle?.bbox
      if (!box) continue

      const key = liveTrackKey(event)
      const existing = tracks.get(key)
      const capturedAt = parseTime(event.captured_at) ?? parseTime(event.event_time)

      // Events can arrive out of order. Never let an older frame overwrite the
      // position established by a newer one.
      if (
        existing &&
        capturedAt !== null &&
        existing.capturedAt !== null &&
        capturedAt < existing.capturedAt
      ) {
        continue
      }

      tracks.set(key, {
        key,
        plate: event.plate.text,
        plateBox: event.plate.bbox ?? null,
        confirmed: isConfirmed(event),
        confidence: event.plate.confidence ?? 0,
        ambiguous: Boolean(event.plate.ambiguous),
        correctedFrom: event.plate.corrected_from ?? null,
        capturedAt,
        arrivedAt: now,
        latencyMs: typeof event.latency_ms === 'number' ? event.latency_ms : null,
        completed: event.event === 'vehicle.completed',
        box,
        frame: { width: frame.width, height: frame.height },
      })
    }
  }, [events])

  // ── Decide what is on screen, once per animation frame ───────────────────
  useEffect(() => {
    if (!enabled) {
      setVisible([])
      return
    }

    let raf = 0
    const step = () => {
      raf = window.requestAnimationFrame(step)

      const tracks = tracksRef.current
      const wallNow = Date.now()
      const videoNow = videoClock?.() ?? null

      const out: Array<TrackBox & { opacity: number }> = []

      for (const [key, item] of tracks) {
        // Age of the box against whichever clock is authoritative. Synced, that
        // is the picture's own clock, so a box stays invisible until the frame
        // it belongs to is actually being shown.
        const synced = videoNow !== null && item.capturedAt !== null
        const age = synced
          ? (videoNow as number) - (item.capturedAt as number)
          : wallNow - item.arrivedAt

        const hold = item.completed
          ? HOLD_AFTER_COMPLETED_MS
          : synced
            ? HOLD_SYNCED_MS
            : HOLD_UNSYNCED_MS

        if (age > hold) {
          // Well past its window and no longer worth keeping. Pruned here
          // rather than on a separate timer, because this loop is the only
          // thing that knows a box has finished being drawn.
          if (age > hold + PRUNE_AFTER_MS) tracks.delete(key)
          continue
        }
        // Synced, a frame that has not been reached yet is in the future. Wait
        // for the picture to catch up rather than drawing ahead of it.
        if (age < 0) continue

        const remaining = hold - age
        const opacity = remaining >= FADE_MS ? 1 : Math.max(0, remaining / FADE_MS)
        out.push({ ...item, opacity })
      }

      setVisible((previous) => {
        // Cheap identity check: skip the re-render when nothing moved. Without
        // it this setState fires sixty times a second forever.
        if (previous.length === out.length) {
          let same = true
          for (let i = 0; i < out.length; i += 1) {
            const a = previous[i]
            const b = out[i]
            if (
              !a ||
              !b ||
              a.key !== b.key ||
              a.box !== b.box ||
              Math.abs(a.opacity - b.opacity) > 0.02
            ) {
              same = false
              break
            }
          }
          if (same) return previous
        }
        return out
      })
    }

    raf = window.requestAnimationFrame(step)
    return () => window.cancelAnimationFrame(raf)
  }, [enabled, videoClock])

  if (!enabled) return <div ref={hostRef} className="absolute inset-0" />

  return (
    <div ref={hostRef} className="pointer-events-none absolute inset-0">
      {dedupeByVehicle(visible.filter((v) => v.confirmed)).map((item) => {
        const rect = contentRect(size, item.frame)
        const scaleX = rect.width / item.frame.width
        const scaleY = rect.height / item.frame.height

        const place = (box: BBox) => ({
          left: rect.left + box.x1 * scaleX,
          top: rect.top + box.y1 * scaleY,
          width: (box.x2 - box.x1) * scaleX,
          height: (box.y2 - box.y1) * scaleY,
        })

        // Draw only readings the pipeline stands behind.
        //
        // Measured on one camera over 120 events: 13 "distinct" plates for
        // roughly half that many cars — `AP05JEO` alongside `AP053EOT`,
        // `XH05ZTK` alongside `XH05ZTX`. Each misread variant is a separate
        // track and so was drawn as a separate box, which is why a single car
        // carried a stack of eight overlapping rectangles and why the plates
        // on screen looked wrong: they *were* wrong, and shown anyway.
        //
        // Every reading still reaches the feed beside the video with its
        // evidence, invalid-format and ambiguous flags included. The video
        // shows the ones that survived grammar, ambiguity and confidence — one
        // box per car that was genuinely read.
        if (!item.confirmed) return null

        const vehicle = place(item.box)
        if (vehicle.width < 4 || vehicle.height < 4) return null
        const plate = item.plateBox ? place(item.plateBox) : null

        // Amber for a reading the system is less sure of, green otherwise.
        const uncertain = item.ambiguous || item.correctedFrom !== null
        const colour = uncertain ? 'hsl(var(--priority-high))' : 'hsl(var(--status-online))'

        // The label goes above the plate when there is one, else above the
        // vehicle — and never *over* the plate, which is the one part of the
        // picture a viewer may want to read for themselves.
        const anchor = plate ?? vehicle
        const labelBelow = anchor.top < 22

        return (
          <div
            key={item.key}
            // A stable handle for tests and for anyone inspecting the DOM.
            // Boxes are found by this rather than by their label, because
            // whether a reading is labelled is a display policy that changes;
            // whether a box exists for a track is the behaviour under test.
            data-anpr-box={item.plate}
            data-confirmed={item.confirmed ? 'true' : 'false'}
            // The pipeline's own capture-to-event figure, carried but not
            // painted. It used to be printed on every box, which is exactly
            // the clutter that made the picture unreadable — the aggregate
            // belongs in the diagnostics panel. Kept here so the number stays
            // inspectable per reading rather than being thrown away.
            data-latency-ms={item.latencyMs ?? undefined}
            style={{ opacity: item.opacity }}
          >
            {/* Layer 1 — the vehicle. Deliberately faint: with twenty cars in
                frame, twenty bold rectangles are the clutter, not the data. */}
            <div
              className="absolute rounded-sm border"
              style={{
                left: vehicle.left,
                top: vehicle.top,
                width: vehicle.width,
                height: vehicle.height,
                borderColor: colour,
                opacity: 0.5,
              }}
            />

            {/* No rectangle over the plate itself — the reading below is the
                thing that was actually read, and a box drawn on top of the
                characters is exactly what a viewer would want to read
                themselves. `plate` (from `item.plateBox`) is still computed
                above and used to anchor the label near the plate rather than
                the vehicle. */}

            {/* The reading, only once it has settled. An unconfirmed plate is
                still in the feed beside the video with its full evidence; it
                just does not get text drawn over live traffic while it is
                still moving between candidates. */}
            {item.confirmed && (
              <div
                className="absolute flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[13px] font-bold leading-tight text-black shadow-lg"
                style={{
                  left: anchor.left,
                  top: labelBelow
                    ? anchor.top + anchor.height + 3
                    : anchor.top - 20,
                  backgroundColor: colour,
                }}
              >
                <span>{item.plate}</span>
                <span className="font-sans font-normal opacity-75">
                  {Math.round(item.confidence * 100)}%
                </span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
