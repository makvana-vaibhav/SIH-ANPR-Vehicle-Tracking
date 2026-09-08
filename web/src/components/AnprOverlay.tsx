/**
 * Plate reads drawn over the live video.
 *
 * ## What this can and cannot claim
 *
 * The boxes are not frame-accurate, and the component does not pretend they
 * are. Inference happens on the worker, not in the browser: a vehicle is read
 * 0.7–5s after the frame was captured (the pipeline's own `latency_ms`), while
 * the video arrives over WebRTC or low-latency HLS in well under a second. The
 * read therefore lands *after* the car has moved on.
 *
 * So each box is labelled with its own age and fades out. It marks where the
 * vehicle was when it was read, which is a true statement, rather than being
 * drawn as though it were tracking the car in the browser, which would not be.
 * The authoritative list of what was read is the plate feed beside the video.
 *
 * Coordinates arrive in source-frame pixels with the frame size alongside
 * them, so they are mapped through the same letterboxing the video element
 * applies with `object-contain`.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import type { BBox, LiveVehicleEvent } from '@/lib/types'

/** How long a box stays on screen. Long enough to read, short enough to trust. */
const BOX_LIFETIME_MS = 6_000

interface Props {
  events: LiveVehicleEvent[]
  /** Hide the boxes without unmounting, so the toggle is instant. */
  enabled?: boolean
}

interface Placed {
  key: string
  plate: string
  confidence: number
  ambiguous: boolean
  correctedFrom: string | null
  bornAt: number
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

export default function AnprOverlay({ events, enabled = true }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  // Re-render on a timer so boxes age out. The event list alone does not
  // change when nothing is being read, and a stale box must still disappear.
  const [, setTick] = useState(0)

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

  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => setTick((t) => t + 1), 250)
    return () => window.clearInterval(timer)
  }, [enabled])

  const placed = useMemo<Placed[]>(() => {
    const now = Date.now()
    const seen = new Set<string>()
    const out: Placed[] = []

    for (const event of events) {
      const box = event.vehicle?.bbox
      const frame = event.frame
      // Without the frame the boxes belong to, the coordinates cannot be
      // scaled. Drawing them against a guessed resolution would put them on
      // the wrong vehicle, so they are dropped instead.
      if (!box || !frame?.width || !frame?.height) continue
      if (!event.plate?.text) continue

      // One box per track: `vehicle.observed` fires repeatedly for a vehicle
      // still in view, and stacking them would draw the same car many times.
      const key = `${event.source?.camera_id ?? ''}:${event.vehicle.vehicle_id}`
      if (seen.has(key)) continue
      seen.add(key)

      const bornAt = Date.parse(event.event_time)
      if (Number.isFinite(bornAt) && now - bornAt > BOX_LIFETIME_MS) continue

      out.push({
        key,
        plate: event.plate.text,
        confidence: event.plate.confidence ?? 0,
        ambiguous: Boolean(event.plate.ambiguous),
        correctedFrom: event.plate.corrected_from ?? null,
        bornAt: Number.isFinite(bornAt) ? bornAt : now,
        box,
        frame,
      })
    }
    return out
  }, [events])

  if (!enabled) return <div ref={hostRef} className="absolute inset-0" />

  return (
    <div ref={hostRef} className="pointer-events-none absolute inset-0">
      {placed.map((item) => {
        const rect = contentRect(size, item.frame)
        const scaleX = rect.width / item.frame.width
        const scaleY = rect.height / item.frame.height
        const left = rect.left + item.box.x1 * scaleX
        const top = rect.top + item.box.y1 * scaleY
        const width = (item.box.x2 - item.box.x1) * scaleX
        const height = (item.box.y2 - item.box.y1) * scaleY

        const ageMs = Date.now() - item.bornAt
        const opacity = Math.max(0.15, 1 - ageMs / BOX_LIFETIME_MS)
        // A repaired or ambiguous reading is shown in amber, so an operator can
        // see at a glance which readings the system is less sure of.
        const uncertain = item.ambiguous || item.correctedFrom !== null
        const colour = uncertain ? 'rgb(234 179 8)' : 'rgb(34 197 94)'

        if (width < 4 || height < 4) return null

        return (
          <div
            key={item.key}
            className="absolute transition-opacity duration-300"
            style={{ left, top, width, height, opacity }}
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
              <span className="font-sans font-normal opacity-60">
                {(ageMs / 1000).toFixed(1)}s ago
              </span>
            </div>
            {item.correctedFrom && (
              <div className="absolute -bottom-5 left-0 whitespace-nowrap rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                was {item.correctedFrom}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
