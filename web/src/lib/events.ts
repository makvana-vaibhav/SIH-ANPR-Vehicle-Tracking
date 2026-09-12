/**
 * Reading the live vehicle event the way the worker actually means it.
 *
 * Two properties of the event stream are easy to get wrong, and both were got
 * wrong independently in the overlay and in the plate feed, with the same
 * visible symptom: the same car listed or drawn many times over.
 */

import type { LiveEvent, LiveTrackBatchEvent, LiveVehicleEvent } from '@/lib/types'

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

/**
 * A batch of live boxes for one camera, rather than a sighting.
 *
 * Narrowed by name so a consumer cannot accidentally treat it as a detection.
 * That mistake is the expensive one here: a vehicle appears in dozens of
 * consecutive batches, so counting them inflates every figure on an operator's
 * screen and listing them fills a plate feed with one car over and over.
 */
export function isTrackBatch(event: LiveEvent): event is LiveTrackBatchEvent {
  return event.event === 'camera.tracks'
}

/**
 * The same key `liveTrackKey` produces, from a batch entry.
 *
 * Both channels describe the same vehicles, and a box must not be drawn twice
 * because two messages arrived about one car. Track ids are unique only within
 * the camera that issued them, hence the camera code.
 */
export function trackBoxKey(cameraId: string, trackId: number): string {
  return `${cameraId}:${trackId}`
}
