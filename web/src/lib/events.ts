/**
 * Reading the live vehicle event the way the worker actually means it.
 *
 * Two properties of the event stream are easy to get wrong, and both were got
 * wrong independently in the overlay and in the plate feed, with the same
 * visible symptom: the same car listed or drawn many times over.
 */

import type { LiveVehicleEvent } from '@/lib/types'

/**
 * A key that is stable for as long as the tracker holds one vehicle.
 *
 * **Not `vehicle_id`.** The worker mints a fresh `vehicle_id` for every event
 * it emits — `StreamRunner._as_vehicle` increments a counter per call — so a
 * vehicle watched across ten events carries ten different vehicle ids. Any
 * consumer that collapses on it is collapsing on nothing, and shows one row or
 * one box per *event* rather than per car.
 *
 * `track_ids[0]` is the tracker's own identity for the vehicle and is stable
 * until it retires. The camera code is included because track ids are only
 * unique within the camera that issued them.
 */
export function liveTrackKey(event: LiveVehicleEvent): string {
  const camera = event.source?.camera_id ?? ''
  const track = event.vehicle?.track_ids?.[0]
  return `${camera}:${track ?? `v${event.vehicle?.vehicle_id}`}`
}

/**
 * True when this event carries no new reading, only a new position.
 *
 * The worker re-emits a vehicle several times a second while it is in view so
 * that an overlay has somewhere current to draw its box. Those carry the plate
 * that was already reported, so counting them as detections inflates every
 * counter on the screen, and listing them fills the plate feed with the same
 * car over and over. They are for drawing, not for reporting.
 */
export function isPositionRefresh(event: LiveVehicleEvent): boolean {
  return event.position_refresh === true
}
