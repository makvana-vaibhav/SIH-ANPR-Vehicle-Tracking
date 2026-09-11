/**
 * What the plate overlay draws, and — more importantly — what it stops drawing.
 *
 * Every case here was a real defect that presented identically to an operator:
 * a rectangle in the wrong place, or a rectangle that would not go away. Both
 * read as "the tracking is broken" when in fact the tracking was fine and the
 * overlay was drawing the wrong thing.
 */

import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AnprOverlay from '@/components/AnprOverlay'
import type { LiveVehicleEvent } from '@/lib/types'

// jsdom has no ResizeObserver, and the overlay needs one to learn its own size.
// A stub that reports a fixed box is enough: these tests are about *which*
// boxes exist and for how long, not about pixel geometry.
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

const START = Date.parse('2026-09-11T10:31:00.000Z')

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
    source: { camera_id: 'CAM-DEMO-01' },
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
    },
    frame: { width: 1920, height: 1080 },
  }
}

/** Advance both clocks together and let the animation frame run. */
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms)
  })
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

  it('draws a box for a readable plate', async () => {
    render(<AnprOverlay events={[event()]} />)
    await tick(20)

    expect(screen.getByText('GJ03AB1234')).toBeDefined()
  })

  it('draws one box per track, not one per event', async () => {
    // The worker mints a fresh vehicle_id for every event it emits, including
    // the position refreshes that follow one vehicle across the frame. Keying
    // on it left a trail of rectangles down the road behind the car.
    const events = [
      event({ vehicleId: 3, x1: 500 }),
      event({ vehicleId: 2, x1: 300 }),
      event({ vehicleId: 1, x1: 100 }),
    ]
    render(<AnprOverlay events={events} />)
    await tick(20)

    expect(screen.getAllByText('GJ03AB1234')).toHaveLength(1)
  })

  it('draws separate boxes for separate tracks', async () => {
    const events = [
      event({ trackId: 7, plate: 'GJ03AB1234' }),
      event({ trackId: 9, plate: 'GJ01CD5678' }),
    ]
    render(<AnprOverlay events={events} />)
    await tick(20)

    expect(screen.getByText('GJ03AB1234')).toBeDefined()
    expect(screen.getByText('GJ01CD5678')).toBeDefined()
  })

  it('expires a box that stops being refreshed', async () => {
    // The bug this pins down: the box list was memoised on the event array, so
    // a re-render on a timer recomputed opacity but never re-ran the expiry
    // filter. Boxes faded to fifteen percent and then stayed on screen for as
    // long as the page was open.
    render(<AnprOverlay events={[event()]} />)
    await tick(20)
    expect(screen.queryByText('GJ03AB1234')).not.toBeNull()

    await tick(4_000)

    expect(screen.queryByText('GJ03AB1234')).toBeNull()
  })

  it('clears a box promptly once the vehicle has been retired', async () => {
    const { rerender } = render(<AnprOverlay events={[event()]} />)
    await tick(20)
    expect(screen.queryByText('GJ03AB1234')).not.toBeNull()

    rerender(<AnprOverlay events={[event({ kind: 'vehicle.completed' })]} />)
    await tick(1_000)

    expect(screen.queryByText('GJ03AB1234')).toBeNull()
  })

  it('ignores an event with no frame size to scale against', async () => {
    // Without the frame the coordinates were measured in there is no honest
    // way to place them, and guessing a resolution puts the box on the wrong
    // vehicle.
    const orphan = { ...event(), frame: null }
    render(<AnprOverlay events={[orphan]} />)
    await tick(20)

    expect(screen.queryByText('GJ03AB1234')).toBeNull()
  })

  it('draws nothing when disabled', async () => {
    render(<AnprOverlay events={[event()]} enabled={false} />)
    await tick(20)

    expect(screen.queryByText('GJ03AB1234')).toBeNull()
  })

  it('reports the capture-to-event latency rather than wall-clock age', async () => {
    render(<AnprOverlay events={[event()]} />)
    await tick(20)

    expect(screen.getByText('+0.3s')).toBeDefined()
  })

  describe('synced to the video clock', () => {
    it('waits for the picture to reach the frame the box came from', async () => {
      // The video is held behind live, so a box whose frame has not been shown
      // yet must not be drawn: doing so puts it ahead of the vehicle instead of
      // on it.
      const videoClock = () => START - 2_000

      render(<AnprOverlay events={[event()]} videoClock={videoClock} />)
      await tick(20)

      expect(screen.queryByText('GJ03AB1234')).toBeNull()
    })

    it('draws the box once the picture reaches its frame', async () => {
      let shown = START - 2_000
      const videoClock = () => shown

      render(<AnprOverlay events={[event()]} videoClock={videoClock} />)
      await tick(20)
      expect(screen.queryByText('GJ03AB1234')).toBeNull()

      shown = START + 100
      await tick(20)

      expect(screen.queryByText('GJ03AB1234')).not.toBeNull()
    })

    it('falls back to arrival time when the transport has no clock', async () => {
      render(<AnprOverlay events={[event()]} videoClock={() => null} />)
      await tick(20)

      expect(screen.queryByText('GJ03AB1234')).not.toBeNull()
    })
  })
})
