/**
 * What the pipeline sees, drawn over the live video.
 *
 * ## What gets a box, and when
 *
 * A box appears as soon as the plate **detector** has found a plate on a
 * vehicle — not when OCR has read it, and whether or not OCR ever does. That is
 * the earliest moment at which there is something true to draw: the detector
 * has found a plate and can say where it is. Waiting for text cost about a
 * second of consensus on the vehicles that do get read, and produced nothing at
 * all for the two-thirds of tracked vehicles that never yield a plate
 * (measured: 33.9% plate yield, docs/PERFORMANCE.md §6).
 *
 * So there are three states, and the picture distinguishes them, because a
 * rectangle that means "we are looking at a plate here" must never be mistaken
 * for one that means "we have identified this vehicle":
 *
 *   located   dashed, faint, no text. A plate is there and this is where.
 *   reading   dashed, firmer. OCR has a candidate it is not yet sure of.
 *   read      solid, with the plate and its confidence printed.
 *
 * ## The timing problem, and what is done about it
 *
 * Inference happens on the worker. A vehicle's position is measured
 * `latency_ms` before the message describing it arrives, and the worker
 * publishes a few times a second rather than once per frame. Drawn from the
 * numbers as they arrive, a box is therefore wrong twice over: placed where the
 * vehicle *was*, then held still while it keeps moving. On a car crossing the
 * frame both errors point the same way — a rectangle trailing the car and
 * jumping to catch up.
 *
 * Three things fix that, and all three are needed:
 *
 * **A clock for the picture.** `videoClock` reports which capture instant the
 * frame on screen belongs to — from the HLS programme-date tags, or from
 * WebRTC's own measured receive lag. Each box is then scheduled against the
 * picture rather than against now.
 *
 * **Prediction.** Between updates the box is advanced along the velocity
 * measured from the vehicle's own last two reported positions. Nothing is
 * invented: the velocity is observed, the advance is the measured gap between
 * the box's capture time and the instant being displayed, and it is clamped
 * (`MAX_PREDICT_MS`) so a track that stops reporting stops moving rather than
 * sailing off on a stale heading.
 *
 * **Error smoothing.** A fresh measurement rarely lands exactly where the
 * prediction had got to. Drawing it straight makes every update a visible
 * jolt, so the discrepancy is folded into an offset that decays to zero over
 * `OFFSET_DECAY_MS`. Deliberately *not* a low-pass filter on position:
 * filtering position reintroduces a lag proportional to speed, which is the
 * very thing being fixed.
 *
 * ## Two channels, and which one wins
 *
 * `camera.tracks` is the picture: one message per camera per tick carrying
 * every drawable vehicle. Because it describes the whole camera it is
 * authoritative — a vehicle absent from the newest batch is gone, which is how
 * a box learns to disappear promptly instead of waiting out a timeout.
 *
 * `vehicle.observed` / `vehicle.completed` are the intelligence, and they can
 * also drive boxes for a camera that publishes no batches (a worker predating
 * the channel, or the simulator's load mode). Once a batch has been seen the
 * batch is the only source: two sources behind one rectangle is a bug waiting
 * to happen. Both key on `camera:track_id`, so the handover collapses into the
 * same box rather than drawing two.
 */

import { useEffect, useRef, useState, type CSSProperties } from 'react'

import { liveTrackKey, trackBoxKey } from '@/lib/events'
import type { BBox, LiveTrackBatchEvent, LiveVehicleEvent } from '@/lib/types'

/**
 * How long a box survives its last update when the picture has a clock.
 *
 * The box is drawn on the very frame it was measured in, so this only has to
 * outlast the gap between updates plus some jitter. It is a safety net, not the
 * mechanism: a vehicle that has left is normally cleared by its absence from
 * the next batch, within one tick.
 */
export const HOLD_SYNCED_MS = 1_800

/**
 * How long a box survives when the transport cannot say what is on screen.
 *
 * Only WebRTC reaches this now — the HLS path always carries a programme-date
 * clock, and the player prefers hls.js over the native player precisely so
 * that it does. WebRTC's picture is a few hundred milliseconds behind at most,
 * so the box does not need the multi-second hold that HLS's buffer once
 * forced: that hold existed to keep a box alive until the *picture* caught up,
 * and it is exactly what left readings floating over the wrong cars.
 */
export const HOLD_BLIND_MS = 3_000

/**
 * How long a box lingers once the vehicle is known to be gone.
 *
 * Known, not guessed: either the batch that owns the camera stopped listing it,
 * or a `vehicle.completed` arrived. Long enough to fade rather than vanish.
 */
export const HOLD_AFTER_RETIRED_MS = 700

/** Fade over the last of the hold, so boxes leave rather than vanish. */
const FADE_MS = 500

/** Drop a box from the store this long after it stopped being drawn. */
const PRUNE_AFTER_MS = 5_000

/**
 * Never advance a box further than this beyond its last measured position.
 *
 * Prediction is an extrapolation, and an extrapolation is only honest over the
 * horizon the measurement supports. Past this the box holds still and fades on
 * its ordinary schedule: a rectangle frozen where the vehicle last actually
 * was is a fair report of what is known, whereas one still gliding on a
 * three-second-old heading is a guess presented as an observation.
 */
const MAX_PREDICT_MS = 700

/**
 * Above this the measured velocity is discarded rather than applied.
 *
 * A tracker that re-associates two vehicles reports a jump, and a jump divided
 * by a short interval is an enormous velocity. Applied, it throws the box clear
 * across the frame. Expressed in source pixels per second, against frames of
 * 1280–1920 px, so this is roughly "further than twice the frame per second".
 */
const MAX_SPEED_PX_PER_S = 4_000

/** Velocity is only measurable across a gap in this range, in ms. */
const SAMPLE_MIN_GAP_MS = 50
const SAMPLE_MAX_GAP_MS = 1_500

/** Weight of the newest velocity estimate. The rest is the previous one. */
const VELOCITY_WEIGHT = 0.6

/** How long a prediction error takes to decay out of the drawn position. */
const OFFSET_DECAY_MS = 220

/**
 * Beyond this, a correction is snapped rather than smoothed.
 *
 * Expressed as a fraction of the frame's shorter side: smoothing a jump that
 * large would drag the rectangle visibly across the picture, which reads worse
 * than the jump it is hiding.
 */
const SNAP_FRACTION = 0.2

/**
 * Recompute at 30 Hz, not on every animation frame.
 *
 * The source is 25 fps and the worker's own cadence is slower still, so 60 Hz
 * buys nothing visible and costs a React pass each time. CPU on this box is
 * shared with the inference worker, and a browser starved of it is one of the
 * failure modes this project has already paid for once.
 */
const UPDATE_INTERVAL_MS = 1000 / 30

/**
 * Reject a video clock that disagrees with the messages by more than this.
 *
 * Both clocks are wall clocks — the programme-date tags come from the gateway
 * and `captured_at` from the worker — which is sound while every container
 * shares the host's time, and is what the whole platform already assumes. If
 * it is ever untrue the difference is minutes, not milliseconds, so it is
 * cheap to notice and fall back to scheduling from arrival rather than drawing
 * boxes against a clock that means nothing.
 */
const CLOCK_SANITY_MS = 10_000

/**
 * Below this, a reading gets a box but no printed plate.
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

/** How far a reading has got. See the header. */
type Tier = 'located' | 'reading' | 'read'

interface Props {
  /**
   * The newest batch of live boxes for the camera on screen, or null. The
   * authoritative source once any batch has arrived.
   */
  batch?: LiveTrackBatchEvent | null
  /**
   * Vehicle events for the same camera. Used for cameras that publish no
   * batches; ignored for drawing once a batch has been seen.
   */
  events?: LiveVehicleEvent[]
  /**
   * Which camera is on screen. Changing it drops every box — one camera's
   * rectangles over another's picture would invent results for a feed that may
   * be reading nothing.
   */
  camera?: string | null
  /** Hide the boxes without unmounting, so the toggle is instant. */
  enabled?: boolean
  /**
   * Epoch-ms capture time of the frame currently on screen, or null when the
   * transport cannot say. Read on every update, so it must be cheap.
   */
  videoClock?: () => number | null
}

/** Corner velocities, in source pixels per millisecond. */
interface Velocity {
  x1: number
  y1: number
  x2: number
  y2: number
}

/** Everything known about one drawable vehicle. */
interface TrackBox {
  key: string
  /** Which channel last described this vehicle. */
  source: 'batch' | 'event'
  tier: Tier
  /** The reading, or '' while there is none. Never a placeholder. */
  plate: string
  confidence: number
  ambiguous: boolean
  correctedFrom: string | null
  /** Capture time of the frame this box was measured in, epoch ms. */
  capturedAt: number | null
  /** When this browser received it, epoch ms. The fallback clock. */
  arrivedAt: number
  /** Capture-to-publish, as the worker measured it. */
  latencyMs: number | null
  /** The vehicle has left: gone from the batch, or reported completed. */
  retired: boolean
  /** The vehicle box — what an operator matches to a car on screen. */
  box: BBox
  /** The localised plate box, when there is one and it is worth drawing. */
  plateBox: BBox | null
  frame: { width: number; height: number }
  /**
   * How fast the vehicle was moving across the last measured interval, or
   * null when it could not be measured. Drives prediction.
   */
  velocity: Velocity | null
  /** Bumped whenever a genuinely newer position lands, so a draw can tell. */
  revision: number
}

/** Where one track's box actually is on screen, between recomputations. */
interface DrawnBox {
  box: BBox
  /** The revision the drawn position was last reconciled against. */
  revision: number
  /** Residual between the drawn position and the prediction, and when it began. */
  offset: Velocity | null
  offsetAt: number
}

/** What either channel can say about a vehicle, before it becomes a TrackBox. */
interface Measurement {
  source: 'batch' | 'event'
  tier: Tier
  plate: string
  confidence: number
  ambiguous: boolean
  correctedFrom: string | null
  capturedAt: number | null
  latencyMs: number | null
  retired: boolean
  box: BBox
  plateBox: BBox | null
  frame: { width: number; height: number }
}

/**
 * How far a reading has got, from the facts the worker reports.
 *
 * The worker deliberately sends `grammar_valid` and `ambiguous` rather than a
 * verdict, because whether a reading is fit to print over live traffic is a
 * display policy and belongs here. Consensus already votes per character
 * across every frame a vehicle was read in; this is the gate on top of it.
 *
 * `AP05JEO1` and `KH0522431` both arrive with respectable confidence and are
 * not plates, which is why grammar and ambiguity are decisive rather than
 * advisory.
 */
function tierOf(
  plate: string | undefined,
  confidence: number | undefined,
  grammarValid: boolean | undefined,
  ambiguous: boolean | undefined,
): Tier {
  if (!plate) return 'located'
  if (!grammarValid || ambiguous) return 'reading'
  return (confidence ?? 0) >= CONFIRM_CONFIDENCE ? 'read' : 'reading'
}

/** `[x1, y1, x2, y2]` from the batch, as the box the rest of this file uses. */
function boxFromTuple(tuple: [number, number, number, number]): BBox {
  const [x1, y1, x2, y2] = tuple
  return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 }
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

/** How much a box deserves to be the one drawn, when two cover one vehicle. */
const TIER_RANK: Record<Tier, number> = { read: 0, reading: 1, located: 2 }

/**
 * One box per vehicle, not one per track.
 *
 * The tracker can hold the same car as more than one track, and each carries
 * its own OCR result — measured on live footage, `AV06HVE` and `AV08HVE` arrive
 * together, both past grammar and both above the confidence gate, because
 * `0`/`8` is the classic confusion. Drawn straight, that is two labelled
 * rectangles on one car disagreeing with each other in front of the viewer.
 *
 * Boxes covering mostly the same pixels are therefore the same vehicle, and
 * only one survives. **The further-along reading wins, before confidence is
 * considered at all**: an ambiguous 0.97 must not displace — and so unlabel —
 * the grammar-valid 0.85 sitting on the same car, and neither must displace a
 * confirmed reading with a bare localisation.
 *
 * The others are not discarded from the system: every reading is in the feed
 * beside the video with its evidence, which is where a disagreement should be
 * visible.
 */
function dedupeByVehicle<T extends { box: BBox; confidence: number; tier: Tier }>(
  items: T[],
): T[] {
  const kept: T[] = []
  const ranked = [...items].sort((a, b) => {
    const byTier = TIER_RANK[a.tier] - TIER_RANK[b.tier]
    return byTier !== 0 ? byTier : b.confidence - a.confidence
  })
  for (const item of ranked) {
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

/**
 * A stable identity for one emitted event, so it is folded in exactly once.
 *
 * The kind is part of it. A vehicle retired on the same frame it was last
 * refreshed on produces an `observed` and a `completed` sharing a timestamp,
 * and they are emphatically not the same event: the second is the one that
 * clears the box.
 */
function eventMarker(event: LiveVehicleEvent): string {
  return `${event.event}|${event.event_time}|${event.vehicle?.vehicle_id ?? ''}`
}

/**
 * Velocity across two measured positions, or null when it is not measurable.
 *
 * Rejects gaps too short to measure over (the difference there is mostly
 * detector jitter) and too long to still describe current motion, and rejects
 * speeds that can only be a track re-association rather than a vehicle.
 */
function measureVelocity(
  from: BBox,
  to: BBox,
  gapMs: number,
  frame: { width: number; height: number },
): Velocity | null {
  if (!frame.width || !frame.height) return null
  if (gapMs < SAMPLE_MIN_GAP_MS || gapMs > SAMPLE_MAX_GAP_MS) return null

  const velocity: Velocity = {
    x1: (to.x1 - from.x1) / gapMs,
    y1: (to.y1 - from.y1) / gapMs,
    x2: (to.x2 - from.x2) / gapMs,
    y2: (to.y2 - from.y2) / gapMs,
  }

  const limit = MAX_SPEED_PX_PER_S / 1000
  const fastest = Math.max(
    Math.abs(velocity.x1),
    Math.abs(velocity.y1),
    Math.abs(velocity.x2),
    Math.abs(velocity.y2),
  )
  return fastest > limit ? null : velocity
}

/** Blend a new velocity estimate with the previous one. */
function blendVelocity(previous: Velocity | null, next: Velocity | null): Velocity | null {
  if (next === null) return previous
  if (previous === null) return next
  const keep = 1 - VELOCITY_WEIGHT
  return {
    x1: next.x1 * VELOCITY_WEIGHT + previous.x1 * keep,
    y1: next.y1 * VELOCITY_WEIGHT + previous.y1 * keep,
    x2: next.x2 * VELOCITY_WEIGHT + previous.x2 * keep,
    y2: next.y2 * VELOCITY_WEIGHT + previous.y2 * keep,
  }
}

/**
 * Fold one measurement into the store, whichever channel it came from.
 *
 * Shared so the two channels cannot disagree about the parts that are easy to
 * get subtly wrong: which measurement is newer, when a velocity may be
 * computed from a pair of positions, and when a box has actually moved.
 */
function upsert(
  tracks: Map<string, TrackBox>,
  key: string,
  next: Measurement,
  now: number,
): void {
  const existing = tracks.get(key)

  // A measurement from a frame that has already been superseded has nothing to
  // say. Messages can arrive out of order, and a buffer that has evicted its
  // marker makes older ones look new again.
  if (
    existing &&
    next.capturedAt !== null &&
    existing.capturedAt !== null &&
    next.capturedAt < existing.capturedAt
  ) {
    return
  }

  // One from the *same* frame as the established position is a different
  // matter: it carries no new position but may well carry new facts, and the
  // commonest is a vehicle retired on the very frame it was last seen on. Its
  // position is kept as it stands — re-measuring velocity over a zero-length
  // interval would corrupt it, and bumping the revision would restart the
  // error smoothing for a box that has not moved — while everything it does
  // report is applied.
  const samePosition =
    existing !== undefined &&
    next.capturedAt !== null &&
    existing.capturedAt !== null &&
    next.capturedAt === existing.capturedAt
  const settled = samePosition && existing !== undefined ? existing : null

  let velocity = existing?.velocity ?? null
  if (existing && settled === null) {
    // Measured over the capture clock where both ends carry it: the interval
    // between two arrivals is the transport's jitter as much as the vehicle's
    // motion.
    const gapMs =
      next.capturedAt !== null && existing.capturedAt !== null
        ? next.capturedAt - existing.capturedAt
        : now - existing.arrivedAt
    velocity = blendVelocity(
      velocity,
      measureVelocity(existing.box, next.box, gapMs, next.frame),
    )
  }

  tracks.set(key, {
    key,
    source: next.source,
    tier: next.tier,
    plate: next.plate,
    confidence: next.confidence,
    ambiguous: next.ambiguous,
    correctedFrom: next.correctedFrom,
    capturedAt: settled ? settled.capturedAt : next.capturedAt,
    arrivedAt: settled ? settled.arrivedAt : now,
    latencyMs: next.latencyMs,
    retired: next.retired,
    box: settled ? settled.box : next.box,
    plateBox: next.plateBox,
    frame: next.frame,
    velocity,
    revision: settled ? settled.revision : (existing?.revision ?? 0) + 1,
  })
}

/**
 * Where the vehicle should be `advanceMs` after the box was measured.
 *
 * Deliberately not clamped to the frame. A vehicle leaving the picture is
 * genuinely half outside it, and the shell around the video crops the
 * rectangle for us; clamping each corner independently instead collapses the
 * box to zero width at the edge, which reads as the tracking dropping the car
 * exactly when it is driving out of shot.
 */
function predictBox(track: TrackBox, advanceMs: number): BBox {
  const velocity = track.velocity
  if (velocity === null || advanceMs <= 0) return track.box

  const t = Math.min(advanceMs, MAX_PREDICT_MS)
  const x1 = track.box.x1 + velocity.x1 * t
  const y1 = track.box.y1 + velocity.y1 * t
  const x2 = track.box.x2 + velocity.x2 * t
  const y2 = track.box.y2 + velocity.y2 * t
  return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 }
}

/** Shift a box by a decaying residual, so a correction does not jolt. */
function applyOffset(box: BBox, offset: Velocity, decay: number): BBox {
  const x1 = box.x1 + offset.x1 * decay
  const y1 = box.y1 + offset.y1 * decay
  const x2 = box.x2 + offset.x2 * decay
  const y2 = box.y2 + offset.y2 * decay
  return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 }
}

/**
 * Carry the plate box along with the vehicle box it sits on.
 *
 * The plate is measured on the same frame as the vehicle, so when the vehicle
 * box is predicted forward the plate box has to travel the same distance or it
 * detaches from the car and floats across the picture on its own.
 */
function shiftLike(plateBox: BBox, from: BBox, to: BBox): BBox {
  const dx = to.x1 - from.x1
  const dy = to.y1 - from.y1
  const x1 = plateBox.x1 + dx
  const y1 = plateBox.y1 + dy
  const x2 = plateBox.x2 + dx
  const y2 = plateBox.y2 + dy
  return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 }
}

function sameBox(a: BBox, b: BBox, tolerance = 0.4): boolean {
  return (
    Math.abs(a.x1 - b.x1) < tolerance &&
    Math.abs(a.y1 - b.y1) < tolerance &&
    Math.abs(a.x2 - b.x2) < tolerance &&
    Math.abs(a.y2 - b.y2) < tolerance
  )
}

export default function AnprOverlay({
  batch = null,
  events,
  camera = null,
  enabled = true,
  videoClock,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  // Live boxes are held in a ref, not in state. They are written by the feeds
  // and read by an animation frame, and routing every position update through
  // setState would re-render the tree several times a second per vehicle for
  // data the render loop is about to read anyway.
  const tracksRef = useRef(new Map<string, TrackBox>())
  // Where each box actually is on screen, and the residual still decaying out
  // of it. Separate from the track because it is the *drawn* state, updated on
  // the display's schedule rather than the feed's.
  const drawnRef = useRef(new Map<string, DrawnBox>())
  // The newest event already folded in. Without it, every update re-walks the
  // whole buffer.
  const markerRef = useRef<string | null>(null)
  // Whether any batch has arrived. Once one has, it is the only drawing source.
  const batchSeenRef = useRef(false)
  // The most recent capture time seen, for sanity-checking the video clock.
  const newestCaptureRef = useRef<number | null>(null)
  // What the last pass decided to draw. This *is* state, because it is what
  // React renders; it changes at most once per update interval, and only when
  // something actually moved.
  const [visible, setVisible] = useState<
    Array<TrackBox & { opacity: number; drawn: BBox; drawnPlate: BBox | null }>
  >([])

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

  // ── A change of camera drops everything ─────────────────────────────────
  //
  // Explicit rather than inferred from an empty feed. With the batch channel a
  // camera can legitimately publish boxes for minutes without a single new
  // reading, so "no events" no longer means "nothing to draw" — it means the
  // plates are still being worked on.
  useEffect(() => {
    tracksRef.current.clear()
    drawnRef.current.clear()
    markerRef.current = null
    batchSeenRef.current = false
    newestCaptureRef.current = null
    setVisible([])
  }, [camera])

  // ── The batch: every drawable vehicle on this camera ────────────────────
  useEffect(() => {
    if (!batch) return
    // Nothing validates the event on either side of the bus yet (the contracts
    // gap, ROADMAP P12), so a malformed message must not take the overlay down
    // with it — an exception in this effect unmounts the whole tree.
    if (!Array.isArray(batch.tracks)) return
    const frame = batch.frame
    // Without the frame the boxes were measured in there is no honest way to
    // place them, and guessing a resolution puts them on the wrong vehicle.
    if (!frame?.width || !frame?.height) return

    const tracks = tracksRef.current
    const now = Date.now()
    const cameraId = batch.source?.camera_id ?? ''
    const capturedAt = parseTime(batch.captured_at) ?? parseTime(batch.event_time)
    const latencyMs = typeof batch.latency_ms === 'number' ? batch.latency_ms : null
    const frameSize = { width: frame.width, height: frame.height }

    batchSeenRef.current = true
    if (capturedAt !== null) {
      const newest = newestCaptureRef.current
      newestCaptureRef.current = newest === null ? capturedAt : Math.max(newest, capturedAt)
    }

    const present = new Set<string>()
    for (const entry of batch.tracks) {
      const key = trackBoxKey(cameraId, entry.track_id)
      present.add(key)

      // Every localisation the worker sends is drawn. There is deliberately no
      // second confidence threshold here: the plate detector's own floor
      // (`plate.confidence`, 0.25) is what decides whether something is a
      // plate, and a stricter bar in the browser would mean the picture
      // disagreed with the pipeline about what it had found — silently hiding
      // the earliest boxes, which is the whole thing this channel exists to
      // show. The detector's confidence travels with the entry so it stays
      // inspectable; it is not re-litigated.
      upsert(
        tracks,
        key,
        {
          source: 'batch',
          tier: tierOf(
            entry.plate,
            entry.confidence,
            entry.grammar_valid,
            entry.ambiguous,
          ),
          plate: entry.plate ?? '',
          confidence: entry.confidence ?? 0,
          ambiguous: Boolean(entry.ambiguous),
          correctedFrom: entry.corrected_from ?? null,
          capturedAt,
          latencyMs,
          retired: false,
          box: boxFromTuple(entry.bbox),
          plateBox: entry.plate_bbox ? boxFromTuple(entry.plate_bbox) : null,
          frame: frameSize,
        },
        now,
      )
    }

    // The batch describes the whole camera, so absence from it is information:
    // a vehicle not listed is no longer drawable. This is what lets a box go
    // away when the car does, rather than when a timeout expires.
    for (const [key, item] of tracks) {
      if (item.source !== 'batch') continue
      if (!key.startsWith(`${cameraId}:`)) continue
      if (present.has(key) || item.retired) continue
      // Re-clocked to this batch so the short retirement window starts now,
      // and left at its last known position: that is where the vehicle was
      // last actually seen, and it is the honest place to fade from.
      tracks.set(key, { ...item, retired: true, capturedAt, arrivedAt: now })
    }
  }, [batch])

  // ── Vehicle events, for a camera that publishes no batches ──────────────
  useEffect(() => {
    if (!events || events.length === 0) return
    // A batch has been seen, so it owns the picture. The events still reach the
    // feed beside the video; they simply stop being a second source of boxes.
    if (batchSeenRef.current) return

    const tracks = tracksRef.current
    const now = Date.now()

    let unprocessed = events.length
    if (markerRef.current !== null) {
      const seen = events.findIndex((event) => eventMarker(event) === markerRef.current)
      if (seen !== -1) unprocessed = seen
    }
    const newest = events[0]
    if (newest) markerRef.current = eventMarker(newest)

    for (let index = unprocessed - 1; index >= 0; index -= 1) {
      const event = events[index]
      if (!event?.plate?.text) continue

      const frame = event.frame
      if (!frame?.width || !frame?.height) continue

      // The **vehicle** box, not the plate box. A plate box is ~90x22 px on a
      // 1280px-wide picture, too small to associate with a car at a glance;
      // the vehicle box is what an operator can actually match to a car.
      const box = event.vehicle?.live_bbox ?? event.vehicle?.bbox
      if (!box) continue

      const capturedAt = parseTime(event.captured_at) ?? parseTime(event.event_time)
      if (capturedAt !== null) {
        const seen = newestCaptureRef.current
        newestCaptureRef.current = seen === null ? capturedAt : Math.max(seen, capturedAt)
      }

      upsert(
        tracks,
        liveTrackKey(event),
        {
          source: 'event',
          tier: tierOf(
            event.plate.text,
            event.plate.confidence,
            event.plate.grammar_valid,
            event.plate.ambiguous,
          ),
          plate: event.plate.text,
          confidence: event.plate.confidence ?? 0,
          ambiguous: Boolean(event.plate.ambiguous),
          correctedFrom: event.plate.corrected_from ?? null,
          capturedAt,
          latencyMs: typeof event.latency_ms === 'number' ? event.latency_ms : null,
          retired: event.event === 'vehicle.completed',
          box,
          plateBox: event.plate.bbox ?? null,
          frame: { width: frame.width, height: frame.height },
        },
        now,
      )
    }
  }, [events])

  // ── Decide what is on screen, at the display's own cadence ──────────────
  useEffect(() => {
    if (!enabled) {
      setVisible([])
      drawnRef.current.clear()
      return
    }

    let raf = 0
    let lastRun = 0

    const step = () => {
      raf = window.requestAnimationFrame(step)

      const wallNow = Date.now()
      if (wallNow - lastRun < UPDATE_INTERVAL_MS) return
      lastRun = wallNow

      const tracks = tracksRef.current
      const drawn = drawnRef.current
      const reported = videoClock?.() ?? null
      const newestCapture = newestCaptureRef.current
      // A clock is only usable if it describes the same timeline the messages
      // do. See CLOCK_SANITY_MS.
      const videoNow =
        reported !== null &&
        (newestCapture === null || Math.abs(reported - newestCapture) < CLOCK_SANITY_MS)
          ? reported
          : null

      const out: Array<
        TrackBox & { opacity: number; drawn: BBox; drawnPlate: BBox | null }
      > = []

      for (const [key, item] of tracks) {
        // Which clock is authoritative for this box: with a video clock, the
        // picture's own; without one, this browser's receipt of the message.
        const synced = videoNow !== null && item.capturedAt !== null
        const age = synced
          ? (videoNow as number) - (item.capturedAt as number)
          : wallNow - item.arrivedAt

        const hold = item.retired
          ? HOLD_AFTER_RETIRED_MS
          : synced
            ? HOLD_SYNCED_MS
            : HOLD_BLIND_MS

        if (age > hold) {
          // Well past its window and no longer worth keeping. Pruned here
          // rather than on a separate timer, because this loop is the only
          // thing that knows a box has finished being drawn.
          if (age > hold + PRUNE_AFTER_MS) {
            tracks.delete(key)
            drawn.delete(key)
          }
          continue
        }
        // Synced, a frame that has not been reached yet is in the future. Wait
        // for the picture to catch up rather than drawing ahead of it.
        if (age < 0) continue

        // How far the box has to be carried forward to describe the instant
        // being displayed. Synced, that gap is measured outright. Unsynced, the
        // picture is assumed to be showing roughly now — true of WebRTC, the
        // only transport that gets here — so the gap is the age of the box plus
        // the pipeline latency the worker measured for it. A retired vehicle is
        // never predicted: it is not moving, it is gone.
        const advance = item.retired ? 0 : synced ? age : age + (item.latencyMs ?? 0)
        const predicted = predictBox(item, advance)

        // Reconcile the drawn position with the prediction. A newer
        // measurement usually lands a little away from wherever the prediction
        // had got to, and that discrepancy is smoothed out rather than drawn.
        let state = drawn.get(key)
        if (state === undefined) {
          state = {
            box: predicted,
            revision: item.revision,
            offset: null,
            offsetAt: wallNow,
          }
        } else if (state.revision !== item.revision) {
          const jump = Math.hypot(state.box.x1 - predicted.x1, state.box.y1 - predicted.y1)
          const snapAbove = SNAP_FRACTION * Math.min(item.frame.width, item.frame.height)
          state = {
            box: state.box,
            revision: item.revision,
            // A jump this large is a track re-association, not a prediction
            // error. Snapping is less misleading than dragging the rectangle
            // across the picture to hide it.
            //
            // A retired vehicle snaps for a different reason: smoothing exists
            // to interpolate between measurements, and for a vehicle that has
            // left there are no more measurements to interpolate towards. Its
            // last measured box is where the car actually was, so that is where
            // it must fade from. Smoothed instead, a box whose newest position
            // had not been drawn yet — routine when synced, because a
            // measurement arrives before the picture reaches its instant —
            // slides backwards across the frame as the car drives out of it.
            offset:
              jump > snapAbove || item.retired
                ? null
                : {
                    x1: state.box.x1 - predicted.x1,
                    y1: state.box.y1 - predicted.y1,
                    x2: state.box.x2 - predicted.x2,
                    y2: state.box.y2 - predicted.y2,
                  },
            offsetAt: wallNow,
          }
        }

        const decay =
          state.offset === null
            ? 0
            : Math.max(0, 1 - (wallNow - state.offsetAt) / OFFSET_DECAY_MS)
        const box =
          state.offset === null || decay <= 0
            ? predicted
            : applyOffset(predicted, state.offset, decay)

        drawn.set(key, {
          box,
          revision: state.revision,
          offset: decay <= 0 ? null : state.offset,
          offsetAt: state.offsetAt,
        })

        const remaining = hold - age
        const opacity = remaining >= FADE_MS ? 1 : Math.max(0, remaining / FADE_MS)
        out.push({
          ...item,
          opacity,
          drawn: box,
          // The plate travels with the vehicle it is on, or it detaches and
          // floats across the picture by itself.
          drawnPlate: item.plateBox ? shiftLike(item.plateBox, item.box, box) : null,
        })
      }

      setVisible((previous) => {
        // Cheap identity check: skip the re-render when nothing moved. Compared
        // by value, not by reference, because a predicted box is a fresh object
        // every pass and a reference check would re-render forever.
        if (previous.length === out.length) {
          let same = true
          for (let i = 0; i < out.length; i += 1) {
            const a = previous[i]
            const b = out[i]
            if (
              !a ||
              !b ||
              a.key !== b.key ||
              a.plate !== b.plate ||
              a.tier !== b.tier ||
              !sameBox(a.drawn, b.drawn) ||
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
      {dedupeByVehicle(visible.map((item) => ({ ...item, box: item.drawn }))).map(
        (item) => {
          const rect = contentRect(size, item.frame)
          const scaleX = rect.width / item.frame.width
          const scaleY = rect.height / item.frame.height

          const place = (box: BBox) => ({
            left: rect.left + box.x1 * scaleX,
            top: rect.top + box.y1 * scaleY,
            width: (box.x2 - box.x1) * scaleX,
            height: (box.y2 - box.y1) * scaleY,
          })

          const vehicle = place(item.box)
          if (vehicle.width < 4 || vehicle.height < 4) return null
          const plate = item.drawnPlate ? place(item.drawnPlate) : null

          const read = item.tier === 'read'
          // Amber for a reading the system is less sure of, green otherwise.
          const uncertain = item.ambiguous || item.correctedFrom !== null
          const colour = uncertain
            ? 'hsl(var(--priority-high))'
            : 'hsl(var(--status-online))'
          // The plate marker is red whatever the vehicle brackets say. It marks
          // where the plate *is*, which is a different statement from how sure
          // the reading is, and giving it its own colour stops the two signals
          // competing for the same rectangle.
          const plateColour = 'hsl(var(--priority-critical))'

          // Four corner brackets rather than a closed rectangle. With eight
          // vehicles in frame a continuous outline per car is the clutter, not
          // the data; brackets read as a frame while leaving the vehicle itself
          // visible. Proportional to the box, so a car in the distance does not
          // get brackets longer than it is.
          const arm = Math.max(
            5,
            Math.min(30, Math.min(vehicle.width, vehicle.height) * 0.24),
          )
          // Tier is carried by weight and opacity, not by a dash pattern: a
          // 6 px bracket cannot be dashed legibly. Thin and faint still means
          // "there is a plate here", full strength still means "this vehicle is
          // identified" — the distinction the three tiers exist to make.
          const armWidth = read ? 3 : 2
          const armOpacity = read ? 1 : item.tier === 'reading' ? 0.75 : 0.55
          const armCss = `${armWidth}px solid ${colour}`
          const corners: Array<{ key: string; style: CSSProperties }> = [
            { key: 'tl', style: { top: 0, left: 0, borderTop: armCss, borderLeft: armCss } },
            { key: 'tr', style: { top: 0, right: 0, borderTop: armCss, borderRight: armCss } },
            {
              key: 'bl',
              style: { bottom: 0, left: 0, borderBottom: armCss, borderLeft: armCss },
            },
            {
              key: 'br',
              style: { bottom: 0, right: 0, borderBottom: armCss, borderRight: armCss },
            },
          ]

          // The label goes above the plate when there is one, else above the
          // vehicle — and never *over* the plate, which is the one part of the
          // picture a viewer may want to read for themselves.
          const anchor = plate ?? vehicle
          const labelBelow = anchor.top < 30

          return (
            <div
              key={item.key}
              // Stable handles for tests and for anyone inspecting the DOM. The
              // track handle is always present; the plate one only when there
              // is a reading, because whether a plate has been read is exactly
              // the distinction these attributes exist to expose.
              data-anpr-track={item.key}
              data-anpr-tier={item.tier}
              data-anpr-box={item.plate || undefined}
              // The pipeline's own capture-to-publish figure, carried but not
              // painted. It used to be printed on every box, which is exactly
              // the clutter that made the picture unreadable — the aggregate
              // belongs in the diagnostics panel. Kept here so the number stays
              // inspectable per vehicle rather than being thrown away.
              data-latency-ms={item.latencyMs ?? undefined}
              style={{ opacity: item.opacity }}
            >
              {/* Layer 1 — the vehicle, as four corner brackets. This element
                  stays the positioned rectangle (the tests read its `left`, and
                  it is still the geometry the box describes); the brackets are
                  nested inside it so a track's direct children remain "vehicle,
                  plate, label" and nothing downstream has to learn a new
                  shape. */}
              <div
                className="absolute"
                style={{
                  left: vehicle.left,
                  top: vehicle.top,
                  width: vehicle.width,
                  height: vehicle.height,
                  opacity: armOpacity,
                }}
              >
                {corners.map((corner) => (
                  <div
                    key={corner.key}
                    className="absolute"
                    style={{ width: arm, height: arm, borderRadius: 2, ...corner.style }}
                  />
                ))}
              </div>

              {/* Layer 2 — the plate the detector localised. This is the
                  earliest honest mark on the picture: it says "there is a
                  plate, and it is here", which is true well before anything
                  has read it. Firm only once it has been read. */}
              {plate && plate.width >= 3 && (
                <div
                  className="absolute rounded-[2px]"
                  style={{
                    left: plate.left,
                    top: plate.top,
                    width: plate.width,
                    height: plate.height,
                    borderColor: plateColour,
                    borderStyle: read ? 'solid' : 'dashed',
                    borderWidth: read ? 2 : 1,
                    opacity: read ? 1 : 0.8,
                  }}
                />
              )}

              {/* The reading, only once it has settled. An unconfirmed plate is
                  still in the feed beside the video with its full evidence; it
                  just does not get text drawn over live traffic while it is
                  still moving between candidates. */}
              {read && (
                <div
                  className="absolute flex items-baseline gap-2 whitespace-nowrap rounded-[3px] bg-white px-2 py-1 font-mono text-[15px] font-bold leading-none tracking-wide text-black shadow-[0_2px_10px_rgba(0,0,0,0.45)]"
                  style={{
                    left: anchor.left,
                    top: labelBelow ? anchor.top + anchor.height + 4 : anchor.top - 28,
                    // The card is white so the plate reads like a plate. The
                    // confidence colour moves to an underline rather than the
                    // background, which keeps amber-means-unsure visible without
                    // tinting the characters themselves.
                    borderBottom: `3px solid ${colour}`,
                  }}
                >
                  <span>{item.plate}</span>
                  <span className="font-sans text-[11px] font-medium tracking-normal text-black/55">
                    {Math.round(item.confidence * 100)}%
                  </span>
                </div>
              )}
            </div>
          )
        },
      )}
    </div>
  )
}
