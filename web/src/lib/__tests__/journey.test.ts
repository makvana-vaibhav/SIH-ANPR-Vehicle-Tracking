/**
 * Journey playback arithmetic.
 *
 * Worth testing rather than eyeballing, because a wrong interpolation makes the
 * map lie smoothly and convincingly: the marker still glides along the route,
 * it is simply in the wrong place at the wrong time. The specific thing pinned
 * here is that progress follows **real elapsed time**, so a long leg takes
 * proportionally longer than a short one.
 */

import { describe, expect, it } from 'vitest'

import {
  buildTimeline,
  istClock,
  journeySeconds,
  positionAt,
  travelledPath,
} from '@/lib/journey'
import type { RouteHop } from '@/lib/types'

const T0 = '2026-09-09T10:00:00.000Z'

function hop(code: string, lon: number, lat: number, arrivedISO: string): RouteHop {
  return {
    camera_id: `id-${code}`,
    camera_code: code,
    camera_name: `${code} camera`,
    city: 'Ahmedabad',
    district: 'Sabarmati',
    lat,
    lon,
    arrived_at: arrivedISO,
    departed_at: arrivedISO,
    dwell_s: 0,
    sightings: 1,
    plate_confidence: 0.9,
    distance_m: null,
    distance_km: null,
    elapsed_s: null,
    implied_kmph: null,
    bearing: null,
    plausible: true,
    flags: [],
  } as unknown as RouteHop
}

function at(offsetSeconds: number): string {
  return new Date(new Date(T0).getTime() + offsetSeconds * 1000).toISOString()
}

/** A 0 -> 60s -> 600s journey: one short leg, then one ten times longer. */
const UNEVEN: RouteHop[] = [
  hop('A', 72.0, 23.0, at(0)),
  hop('B', 72.1, 23.0, at(60)),
  hop('C', 72.2, 23.0, at(600)),
]

describe('buildTimeline', () => {
  it('is empty for no hops', () => {
    expect(buildTimeline([])).toEqual([])
  })

  it('measures seconds from the first sighting', () => {
    expect(buildTimeline(UNEVEN)).toEqual([0, 60, 600])
  })
})

describe('journeySeconds', () => {
  it('is zero for a single sighting, which is a position and not a journey', () => {
    expect(journeySeconds(buildTimeline([UNEVEN[0]!]))).toBe(0)
  })

  it('spans first to last', () => {
    expect(journeySeconds(buildTimeline(UNEVEN))).toBe(600)
  })
})

describe('positionAt', () => {
  const timeline = buildTimeline(UNEVEN)

  it('returns null only when there are no hops', () => {
    expect(positionAt([], [], 0)).toBeNull()
  })

  it('sits on the only camera when there is one sighting', () => {
    expect(positionAt([UNEVEN[0]!], [0], 999)).toEqual([72.0, 23.0])
  })

  it('clamps before the start and after the end rather than extrapolating', () => {
    // Inventing a position outside the observed window would assert a sighting
    // that never happened.
    expect(positionAt(UNEVEN, timeline, -50)).toEqual([72.0, 23.0])
    expect(positionAt(UNEVEN, timeline, 5000)).toEqual([72.2, 23.0])
  })

  it('lands exactly on a camera at that camera’s moment', () => {
    expect(positionAt(UNEVEN, timeline, 0)).toEqual([72.0, 23.0])
    expect(positionAt(UNEVEN, timeline, 60)?.[0]).toBeCloseTo(72.1, 6)
  })

  it('interpolates halfway along a leg at that leg’s midpoint', () => {
    // Halfway through the first leg (0 -> 60s) is 30s.
    expect(positionAt(UNEVEN, timeline, 30)?.[0]).toBeCloseTo(72.05, 6)
    // Halfway through the second leg (60 -> 600s) is 330s.
    expect(positionAt(UNEVEN, timeline, 330)?.[0]).toBeCloseTo(72.15, 6)
  })

  it('follows elapsed time, not hop count', () => {
    // This is the property the whole module exists for. At the halfway point
    // of the *journey* (300s), a hop-stepping implementation would be at
    // camera B; a time-following one is still short of it, because the second
    // leg is ten times longer than the first.
    const halfway = positionAt(UNEVEN, timeline, 300)!
    expect(halfway[0]).toBeGreaterThan(72.1)
    expect(halfway[0]).toBeLessThan(72.15)
  })

  it('does not divide by zero when two sightings share a timestamp', () => {
    // The shape a cloned plate makes: one plate at two places at once.
    const simultaneous = [hop('A', 72.0, 23.0, at(0)), hop('B', 72.5, 23.0, at(0))]
    const line = buildTimeline(simultaneous)
    const where = positionAt(simultaneous, line, 0)
    expect(where).not.toBeNull()
    expect(Number.isFinite(where![0])).toBe(true)
  })
})

describe('travelledPath', () => {
  const timeline = buildTimeline(UNEVEN)

  it('is empty before there is anything to draw', () => {
    expect(travelledPath([UNEVEN[0]!], [0], 10)).toEqual([])
  })

  it('includes only the cameras already reached, plus the live position', () => {
    const path = travelledPath(UNEVEN, timeline, 30)
    // Camera A reached, B not yet, plus the interpolated point.
    expect(path).toHaveLength(2)
    expect(path[0]).toEqual([72.0, 23.0])
    expect(path[1]![0]).toBeCloseTo(72.05, 6)
  })

  it('grows to the whole journey by the end', () => {
    const path = travelledPath(UNEVEN, timeline, 600)
    expect(path.length).toBeGreaterThanOrEqual(3)
    expect(path[path.length - 1]).toEqual([72.2, 23.0])
  })
})

describe('istClock', () => {
  it('renders IST, not UTC', () => {
    // 10:00 UTC is 15:30 IST.
    expect(istClock(T0, 0)).toBe('15:30:00')
  })

  it('advances by the playhead offset', () => {
    expect(istClock(T0, 125)).toBe('15:32:05')
  })
})
