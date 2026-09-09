/**
 * Journey playback: where a vehicle was, at a given moment of its journey.
 *
 * Pure functions, deliberately separated from `RouteMap` so the arithmetic that
 * decides what the map claims can be tested. The map draws what these return;
 * if the interpolation is wrong, the map lies smoothly and convincingly, which
 * is the worst way for it to be wrong.
 *
 * ## What the interpolation does and does not claim
 *
 * Position is linear between the two cameras bracketing the requested moment.
 * That is exactly the claim the dashed legs already make — we know two
 * endpoints and two times, and nothing about the path between them. It is not
 * a guess at the roads taken.
 *
 * Progress through the journey is **proportional to real elapsed time**, not
 * one step per hop. A four-minute leg takes eight times as long to cross as a
 * thirty-second one. Stepping uniformly per hop would be simpler and would
 * quietly misrepresent the journey: a vehicle that sat at a junction for ten
 * minutes would appear to drive straight through.
 */

import type { RouteHop } from '@/lib/types'

/** Seconds from the first sighting to each hop's arrival. */
export function buildTimeline(hops: RouteHop[]): number[] {
  if (hops.length === 0) return []
  const start = new Date(hops[0]!.arrived_at).getTime()
  return hops.map((hop) => (new Date(hop.arrived_at).getTime() - start) / 1000)
}

/** Total journey length in seconds; 0 when there is nothing to play. */
export function journeySeconds(timeline: number[]): number {
  return timeline.length > 1 ? timeline[timeline.length - 1]! : 0
}

/**
 * Position `seconds` into the journey, as `[lon, lat]`.
 *
 * Returns null only when there are no hops at all. Before the first sighting
 * the vehicle is at the first camera and after the last it is at the last —
 * clamped rather than extrapolated, because inventing a position outside the
 * observed window would be asserting a sighting that never happened.
 */
export function positionAt(
  hops: RouteHop[],
  timeline: number[],
  seconds: number,
): [number, number] | null {
  if (hops.length === 0) return null
  const first = hops[0]!
  if (hops.length === 1) return [first.lon, first.lat]

  const last = hops[hops.length - 1]!
  if (seconds <= 0) return [first.lon, first.lat]
  if (seconds >= journeySeconds(timeline)) return [last.lon, last.lat]

  let index = 0
  while (index < timeline.length - 1 && timeline[index + 1]! <= seconds) index += 1
  if (index >= hops.length - 1) return [last.lon, last.lat]

  const from = hops[index]!
  const to = hops[index + 1]!
  const span = timeline[index + 1]! - timeline[index]!
  // A zero-length span means two sightings share a timestamp — the shape a
  // cloned plate makes. Sit on the earlier camera rather than dividing by zero.
  const fraction = span > 0 ? Math.min(1, Math.max(0, (seconds - timeline[index]!) / span)) : 0

  return [
    from.lon + (to.lon - from.lon) * fraction,
    from.lat + (to.lat - from.lat) * fraction,
  ]
}

/**
 * The cameras already reached at `seconds`, plus the current position — the
 * line drawn behind the moving marker.
 */
export function travelledPath(
  hops: RouteHop[],
  timeline: number[],
  seconds: number,
): [number, number][] {
  const here = positionAt(hops, timeline, seconds)
  if (!here || hops.length < 2) return []

  const path: [number, number][] = []
  for (let index = 0; index < hops.length; index += 1) {
    if (timeline[index]! <= seconds) path.push([hops[index]!.lon, hops[index]!.lat])
  }
  path.push(here)
  return path.length > 1 ? path : []
}

/** Journey clock, rendered in IST at the presentation edge (CLAUDE.md §5). */
export function istClock(iso: string, plusSeconds: number): string {
  return new Date(new Date(iso).getTime() + plusSeconds * 1000).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  })
}
