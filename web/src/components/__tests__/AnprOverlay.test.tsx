/**
 * What the overlay draws, where it draws it, and what it stops drawing.
 *
 * Every case here was a real defect that presented identically to an operator:
 * a rectangle in the wrong place, or a rectangle that would not go away. Both
 * read as "the tracking is broken" when in fact the tracking was fine and the
 * overlay was drawing the wrong thing.
 */

import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AnprOverlay, {
  HOLD_AFTER_RETIRED_MS,
  HOLD_BLIND_MS,
  HOLD_SYNCED_MS,
} from '@/components/AnprOverlay'
import type { LiveTrackBatchEvent, LiveTrackBox, LiveVehicleEvent } from '@/lib/types'

// jsdom has no ResizeObserver, and the overlay needs one to learn its own size.
// A stub that reports a fixed box is enough, and it is what makes the geometry
// assertions below possible: 1280x720 of element for a 1920x1080 frame is a
// scale of exactly 2/3 with no letterboxing.
class StubResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(): void {
    this.callback(
      [{ contentRect: { width: 1280, height: 720 } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    )
  }
  unobserve(): void {}
  disconnect(): void {}
}

const START = Date.parse('2026-09-12T10:31:00.000Z')
const CAMERA = 'CAM-DEMO-01'

/** Source pixels to element pixels, for the stubbed sizes above. */
const SCALE = 1280 / 1920

/** One step of the overlay's own update interval, with room to spare. */
const FRAME_MS = 34

/** One entry in a `camera.tracks` batch. */
function box(
  overrides: {
    trackId?: number
    x1?: number
    plate?: string
    confidence?: number
    ambiguous?: boolean
    plateConfidence?: number
    located?: boolean
  } = {},
): LiveTrackBox {
  const {
    trackId = 7,
    x1 = 100,
    plate,
    confidence = 0.94,
    ambiguous = false,
    plateConfidence = 0.88,
    located = true,
  } = overrides

  const entry: LiveTrackBox = {
    track_id: trackId,
    type: 'car',
    bbox: [x1, 300, x1 + 200, 460],
  }
  if (located) {
    entry.plate_bbox = [x1 + 60, 400, x1 + 150, 422]
    entry.plate_detection_confidence = plateConfidence
  }
  if (plate !== undefined) {
    entry.plate = plate
    entry.confidence = confidence
    entry.grammar_valid = true
    entry.ambiguous = ambiguous
  }
  return entry
}

/** A batch of live boxes for one camera, as of one frame. */
function batch(
  tracks: LiveTrackBox[],
  overrides: { capturedAt?: number; camera?: string } = {},
): LiveTrackBatchEvent {
  const { capturedAt = START, camera = CAMERA } = overrides
  return {
    event: 'camera.tracks',
    event_time: new Date(capturedAt + 300).toISOString(),
    captured_at: new Date(capturedAt).toISOString(),
    latency_ms: 300,
    source: { camera_id: camera },
    frame: { width: 1920, height: 1080 },
    tracks,
  }
}

/** A `vehicle.observed` / `vehicle.completed`, for the no-batch path. */
function event(
  overrides: {
    trackId?: number
    vehicleId?: number
    plate?: string
    capturedAt?: number
    x1?: number
    kind?: 'vehicle.observed' | 'vehicle.completed'
  } = {},
): LiveVehicleEvent {
  const {
    trackId = 7,
    vehicleId = 1,
    plate = 'GJ03AB1234',
    capturedAt = START,
    x1 = 100,
    kind = 'vehicle.observed',
  } = overrides

  return {
    event: kind,
    event_time: new Date(capturedAt + 300).toISOString(),
    captured_at: new Date(capturedAt).toISOString(),
    latency_ms: 300,
    source: { camera_id: CAMERA },
    vehicle: {
      vehicle_id: vehicleId,
      track_ids: [trackId],
      type: 'car',
      confidence: 0.9,
      bbox: { x1: 900, y1: 400, x2: 1100, y2: 560, w: 200, h: 160 },
      live_bbox: { x1, y1: 300, x2: x1 + 200, y2: 460, w: 200, h: 160 },
      first_seen_s: 1,
      last_seen_s: 3,
    },
    plate: {
      text: plate,
      confidence: 0.94,
      readable: true,
      grammar_valid: true,
      ambiguous: false,
      corrected_from: null,
      format: 'in_current',
      evidence: { reads_total: 4, agreement: 3 },
    },
    frame: { width: 1920, height: 1080 },
  }
}

/**
 * Advance both clocks together and let the overlay run.
 *
 * Several steps, not one. The overlay recomputes at its own interval rather
 * than on every animation frame, and it sets the state React renders from
 * *inside* that pass — so one frame's worth of simulated time can land inside
 * the throttle window and paint nothing. Asserting after a single step raced
 * the renderer: the same assertion passed or failed run to run. The extra
 * steps cost ~100 ms of simulated time, immaterial against the 700–3000 ms
 * hold windows these tests exercise.
 */
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
  })
  for (let step = 0; step < 3; step += 1) {
    await act(async () => {
      vi.advanceTimersByTime(FRAME_MS)
    })
  }
}

/** Where a track's vehicle rectangle actually landed, in element px. */
function vehicleLeft(container: HTMLElement, trackId = 7): number {
  const el = container.querySelector(`[data-anpr-track="${CAMERA}:${trackId}"] > div`)
  const left = (el as HTMLElement | null)?.style.left
  return left === undefined ? Number.NaN : Number.parseFloat(left)
}

function tierOf(container: HTMLElement, trackId = 7): string | null {
  const el = container.querySelector(`[data-anpr-track="${CAMERA}:${trackId}"]`)
  return el?.getAttribute('data-anpr-tier') ?? null
}

describe('AnprOverlay', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', StubResizeObserver)
    vi.useFakeTimers({ toFake: ['Date', 'requestAnimationFrame', 'setTimeout'] })
    vi.setSystemTime(START)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  describe('a box the moment a plate is located', () => {
    it('draws a box, and no plate text, for a plate nothing has read', async () => {
      // The whole point of the channel. The detector has found a plate and can
      // say where it is; OCR has not read it and may never — measured on this
      // fleet, two-thirds of tracked vehicles never yield a reading. Waiting
      // for text left those two-thirds with no box at all.
      const { container } = render(<AnprOverlay batch={batch([box()])} camera={CAMERA} />)
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()
      expect(tierOf(container)).toBe('located')
      // Two rectangles — the vehicle and the plate — and no text.
      expect(container.querySelectorAll('[data-anpr-track] > div')).toHaveLength(2)
      expect(container.querySelector('[data-anpr-box]')).toBeNull()
    })

    it('shows a candidate reading without printing it', async () => {
      // A plate that is still moving between candidates is worse than no label.
      const unsettled = box({ plate: 'GJ03AB1234', confidence: 0.42 })
      const { container } = render(
        <AnprOverlay batch={batch([unsettled])} camera={CAMERA} />,
      )
      await tick(20)

      expect(tierOf(container)).toBe('reading')
      expect(screen.queryByText('GJ03AB1234')).toBeNull()
    })

    it('prints the plate once the reading has settled', async () => {
      const { container } = render(
        <AnprOverlay batch={batch([box({ plate: 'GJ03AB1234' })])} camera={CAMERA} />,
      )
      await tick(20)

      expect(tierOf(container)).toBe('read')
      expect(screen.getByText('GJ03AB1234')).toBeDefined()
    })

    it('does not second-guess the detector about what is a plate', async () => {
      // The plate detector's own floor (`plate.confidence`, 0.25) decides
      // whether something is a plate. A stricter threshold in the browser
      // would mean the picture disagreed with the pipeline about what it had
      // found, and would silently hide the earliest boxes — which is the whole
      // thing this channel exists to show.
      const weak = box({ plateConfidence: 0.26 })
      const { container } = render(<AnprOverlay batch={batch([weak])} camera={CAMERA} />)
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track] > div')).toHaveLength(2)
    })

    it('draws the vehicle alone when no plate box came with it', async () => {
      // A worker that reports a localisation without a box, or an entry from
      // before the field existed. The vehicle is still there and still worth
      // drawing; nothing is guessed about where its plate is.
      const { container } = render(
        <AnprOverlay batch={batch([box({ located: false })])} camera={CAMERA} />,
      )
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track] > div')).toHaveLength(1)
    })
  })

  describe('the batch is authoritative', () => {
    it('drops a vehicle the newest batch no longer lists', async () => {
      // This is what lets a box go away when the car does, rather than when a
      // timeout expires. The batch describes the whole camera, so absence from
      // it is information.
      const { container, rerender } = render(
        <AnprOverlay batch={batch([box()])} camera={CAMERA} />,
      )
      await tick(20)
      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()

      rerender(<AnprOverlay batch={batch([], { capturedAt: START + 200 })} camera={CAMERA} />)
      await tick(HOLD_AFTER_RETIRED_MS + 200)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('keeps a vehicle that is still listed', async () => {
      const { container, rerender } = render(
        <AnprOverlay batch={batch([box({ x1: 100 })])} camera={CAMERA} />,
      )
      await tick(20)

      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 300 })], { capturedAt: START + 200 })}
          camera={CAMERA}
        />,
      )
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()
    })

    it('draws one box per track, not one per batch', async () => {
      const { container, rerender } = render(
        <AnprOverlay batch={batch([box({ x1: 100 })])} camera={CAMERA} />,
      )
      await tick(20)
      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 300 })], { capturedAt: START + 200 })}
          camera={CAMERA}
        />,
      )
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track]')).toHaveLength(1)
    })

    it('draws separate boxes for separate tracks', async () => {
      const { container } = render(
        <AnprOverlay
          batch={batch([
            box({ trackId: 7, x1: 100, plate: 'GJ03AB1234' }),
            box({ trackId: 9, x1: 900, plate: 'GJ01CD5678' }),
          ])}
          camera={CAMERA}
        />,
      )
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track]')).toHaveLength(2)
      expect(screen.getByText('GJ03AB1234')).toBeDefined()
      expect(screen.getByText('GJ01CD5678')).toBeDefined()
    })

    it('keeps the settled reading when two tracks cover one vehicle', async () => {
      // `0`/`8` is the classic OCR confusion and it arrives as two tracks on
      // one car. Ranking purely by confidence let an ambiguous 0.97 displace a
      // settled 0.85 — and since only settled readings are printed, the car
      // then carried a box with no plate on it while the plate had been read.
      const { container } = render(
        <AnprOverlay
          batch={batch([
            box({ trackId: 9, plate: 'GJ03AB1235', confidence: 0.97, ambiguous: true }),
            box({ trackId: 7, plate: 'GJ03AB1234', confidence: 0.85 }),
          ])}
          camera={CAMERA}
        />,
      )
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track]')).toHaveLength(1)
      expect(screen.getByText('GJ03AB1234')).toBeDefined()
    })

    it('expires a box when the batches stop arriving altogether', async () => {
      // The safety net behind the authoritative removal above: a worker that
      // dies, or a socket that drops, must not leave rectangles on screen.
      const { container } = render(<AnprOverlay batch={batch([box()])} camera={CAMERA} />)
      await tick(20)
      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()

      await tick(HOLD_BLIND_MS + 1_000)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('ignores a batch with no frame size to scale against', async () => {
      const orphan = { ...batch([box()]), frame: null }
      const { container } = render(<AnprOverlay batch={orphan} camera={CAMERA} />)
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('drops every box when the camera changes', async () => {
      // A rectangle left over from the previous feed, drawn on the new one,
      // invents a result for a camera that may be reading nothing at all.
      const { container, rerender } = render(
        <AnprOverlay batch={batch([box()])} camera={CAMERA} />,
      )
      await tick(20)
      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()

      rerender(<AnprOverlay batch={null} camera="CAM-DEMO-02" />)
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('draws nothing when disabled', async () => {
      const { container } = render(
        <AnprOverlay batch={batch([box()])} camera={CAMERA} enabled={false} />,
      )
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('carries the capture-to-publish latency rather than wall-clock age', async () => {
      const { container } = render(<AnprOverlay batch={batch([box()])} camera={CAMERA} />)
      await tick(20)

      const el = container.querySelector('[data-anpr-track]')
      expect(el?.getAttribute('data-latency-ms')).toBe('300')
    })
  })

  describe('vehicle events, for a camera with no batch channel', () => {
    it('draws from events when no batch has arrived', async () => {
      // A worker predating the channel, or the simulator's load mode.
      render(<AnprOverlay events={[event()]} camera={CAMERA} />)
      await tick(20)

      expect(screen.getByText('GJ03AB1234')).toBeDefined()
    })

    it('stops drawing from events once a batch has been seen', async () => {
      // Two sources behind one rectangle is a bug waiting to happen: the batch
      // owns the picture as soon as it exists.
      const { container, rerender } = render(
        <AnprOverlay batch={batch([box({ trackId: 7 })])} camera={CAMERA} />,
      )
      await tick(20)

      rerender(
        <AnprOverlay
          batch={batch([box({ trackId: 7 })])}
          events={[event({ trackId: 42, plate: 'GJ09ZZ9999', x1: 900 })]}
          camera={CAMERA}
        />,
      )
      await tick(20)

      expect(container.querySelectorAll('[data-anpr-track]')).toHaveLength(1)
      expect(screen.queryByText('GJ09ZZ9999')).toBeNull()
    })

    it('clears a box promptly once the vehicle has been retired', async () => {
      // The retirement event can share a frame with the last position update —
      // a vehicle is commonly retired on the very frame it was last seen on —
      // so it carries the same capture time. Treated as a duplicate it would
      // be dropped, and the box would sit out its whole hold instead of
      // clearing when the car left.
      const { rerender } = render(<AnprOverlay events={[event()]} camera={CAMERA} />)
      await tick(20)
      expect(screen.queryByText('GJ03AB1234')).not.toBeNull()

      rerender(
        <AnprOverlay events={[event({ kind: 'vehicle.completed' })]} camera={CAMERA} />,
      )
      await tick(1_000)

      expect(screen.queryByText('GJ03AB1234')).toBeNull()
    })
  })

  describe('synced to the video clock', () => {
    it('waits for the picture to reach the frame the boxes came from', async () => {
      // The video is held behind live, so a box whose frame has not been shown
      // yet must not be drawn: doing so puts it ahead of the vehicle.
      const { container } = render(
        <AnprOverlay
          batch={batch([box()])}
          camera={CAMERA}
          videoClock={() => START - 2_000}
        />,
      )
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).toBeNull()
    })

    it('draws the box once the picture reaches its frame', async () => {
      let shown = START - 2_000
      const { container } = render(
        <AnprOverlay batch={batch([box()])} camera={CAMERA} videoClock={() => shown} />,
      )
      await tick(20)
      expect(container.querySelector('[data-anpr-track]')).toBeNull()

      shown = START + 100
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()
    })

    it('falls back to arrival time when the transport has no clock', async () => {
      const { container } = render(
        <AnprOverlay batch={batch([box()])} camera={CAMERA} videoClock={() => null} />,
      )
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()
    })

    it('ignores a clock that disagrees with the messages by minutes', async () => {
      // Both clocks are wall clocks from different containers. If they ever
      // disagree it is by minutes, and scheduling against a clock like that
      // would suppress every box forever rather than degrade.
      const { container } = render(
        <AnprOverlay
          batch={batch([box()])}
          camera={CAMERA}
          videoClock={() => START - 20 * 60_000}
        />,
      )
      await tick(20)

      expect(container.querySelector('[data-anpr-track]')).not.toBeNull()
    })
  })

  describe('predicting between position updates', () => {
    // The worker publishes a vehicle's position a few times a second, not once
    // per frame, and every message describes a frame captured before it was
    // sent. Drawn as they arrive, boxes trail the car and jump to catch up —
    // which is what "the detection is late" looked like on screen.

    it('advances the box along the velocity the vehicle was measured at', async () => {
      // 200 source px over 200 ms is 1 px/ms. The newest measurement puts the
      // vehicle at x1=300, and the picture is showing a frame 200 ms later, so
      // the box belongs at 500 — not at the 300 last reported.
      let shown = START
      const { container, rerender } = render(
        <AnprOverlay
          batch={batch([box({ x1: 100 })])}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)

      shown = START + 400
      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 300 })], { capturedAt: START + 200 })}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      // Long enough for the correction smoothing to decay out, so this asserts
      // the predicted position rather than a point on the way to it.
      await tick(400)

      expect(vehicleLeft(container)).toBeCloseTo(500 * SCALE, 0)
    })

    it('does not predict beyond the horizon the measurement supports', async () => {
      // 1.5 s past the last position, the same 1 px/ms would put the box 1500
      // px further on — a whole frame width away, on nothing but a stale
      // heading. Prediction is capped.
      let shown = START
      const { container, rerender } = render(
        <AnprOverlay
          batch={batch([box({ x1: 100 })])}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)

      shown = START + 200 + HOLD_SYNCED_MS - 400
      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 300 })], { capturedAt: START + 200 })}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(400)

      const left = vehicleLeft(container)
      expect(left).toBeGreaterThan(300 * SCALE)
      expect(left).toBeLessThan(1_100 * SCALE)
    })

    it('does not predict from a single sighting', async () => {
      // One position is not a velocity. A vehicle seen once is drawn exactly
      // where it was seen.
      const { container } = render(
        <AnprOverlay
          batch={batch([box({ x1: 300 })])}
          camera={CAMERA}
          videoClock={() => START + 400}
        />,
      )
      await tick(20)

      expect(vehicleLeft(container)).toBeCloseTo(300 * SCALE, 0)
    })

    it('ignores a jump that can only be a track re-association', async () => {
      // A tracker that re-associates two vehicles reports a leap, and a leap
      // over a short interval is an impossible velocity. Applied, it throws
      // the rectangle clear across the picture.
      let shown = START
      const { container, rerender } = render(
        <AnprOverlay
          batch={batch([box({ x1: 100 })])}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)

      shown = START + 400
      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 1_500 })], { capturedAt: START + 100 })}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(400)

      expect(vehicleLeft(container)).toBeCloseTo(1_500 * SCALE, 0)
    })

    it('does not predict a vehicle that has already left', async () => {
      // It is not moving; it is gone. Predicting a retired vehicle slides its
      // rectangle away from the last place it was actually seen, which is the
      // only place worth fading from.
      let shown = START
      const { container, rerender } = render(
        <AnprOverlay
          batch={batch([box({ x1: 100 })])}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)
      rerender(
        <AnprOverlay
          batch={batch([box({ x1: 300 })], { capturedAt: START + 200 })}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)

      shown = START + 400
      rerender(
        <AnprOverlay
          batch={batch([], { capturedAt: START + 400 })}
          camera={CAMERA}
          videoClock={() => shown}
        />,
      )
      await tick(20)

      expect(vehicleLeft(container)).toBeCloseTo(300 * SCALE, 0)
    })
  })
})
