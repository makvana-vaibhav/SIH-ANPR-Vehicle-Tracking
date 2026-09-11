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
const HOLD_SYNCED_MS = 1_200

/** Unsynced, the box cannot track the car, so it is held long enough to read. */
const HOLD_UNSYNCED_MS = 2_500

/** A retired vehicle is gone. Clear it promptly rather than waiting out the hold. */
const HOLD_AFTER_COMPLETED_MS = 700

/** Fade over the last of the hold, so boxes leave rather than vanish. */
const FADE_MS = 500

/** Drop a box from the store this long after it stopped being drawn. */
const PRUNE_AFTER_MS = 5_000

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
  box: BBox
  frame: { width: number; height: number }
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

      // The timestamped box, falling back to the best-sighting box for events
      // from a worker that does not publish one.
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
      {visible.map((item) => {
        const rect = contentRect(size, item.frame)
        const scaleX = rect.width / item.frame.width
        const scaleY = rect.height / item.frame.height
        const left = rect.left + item.box.x1 * scaleX
        const top = rect.top + item.box.y1 * scaleY
        const width = (item.box.x2 - item.box.x1) * scaleX
        const height = (item.box.y2 - item.box.y1) * scaleY

        // A repaired or ambiguous reading is shown in the priority-warning
        // colour, so an operator can see at a glance which readings the
        // system is less sure of. `hsl(var(--...))` reaches the same tokens
        // Tailwind classes use elsewhere — this box is drawn with inline
        // styles because its position comes from the video's own geometry.
        const uncertain = item.ambiguous || item.correctedFrom !== null
        const colour = uncertain ? 'hsl(var(--priority-high))' : 'hsl(var(--status-online))'

        if (width < 4 || height < 4) return null

        return (
          <div
            key={item.key}
            className="absolute"
            style={{ left, top, width, height, opacity: item.opacity }}
          >
            <div
              className="h-full w-full rounded-sm border-2"
              style={{ borderColor: colour }}
            />
            <div
              className="absolute -top-6 left-0 flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] font-bold text-black shadow"
              style={{ backgroundColor: colour }}
            >
              <span>{item.plate}</span>
              <span className="font-sans font-normal opacity-80">
                {item.confidence.toFixed(2)}
              </span>
              {/* The pipeline's own capture-to-event figure. This is the number
                  that says how far behind the picture a reading really is, and
                  it stays on screen whether or not the box could be synced. */}
              {item.latencyMs !== null && (
                <span className="font-sans font-normal opacity-60">
                  +{(item.latencyMs / 1000).toFixed(1)}s
                </span>
              )}
            </div>
            {item.correctedFrom && (
              <div className="absolute -bottom-5 left-0 whitespace-nowrap rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-priority-high">
                was {item.correctedFrom}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
