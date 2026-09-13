"""Structured events for the platform to consume.

The AI service's job ends at "here is what I saw, and how sure I am". Plate
lookup, watchlist matching, vehicle history, GIS and alerting all live in the
platform. Keeping that boundary sharp is what lets the inference layer be
tested, benchmarked and scaled on its own terms.

Two event kinds are emitted, and the distinction matters for a live system:

  vehicle.observed   provisional. A vehicle is in view and this is the current
                     best reading. Emitted while it is still being watched, so
                     the platform can react before the vehicle has left.
  vehicle.completed  final. The vehicle has left, all evidence is in, and
                     consensus is settled. This is the one to trust for history.

A system that only emitted the final event could not raise a real-time alert,
because the alert would arrive after the car had gone. One that only emitted
provisional events would fill the platform's history with half-formed readings.

A third kind exists for a different purpose entirely:

  camera.tracks      every drawable vehicle on one camera as of one frame, in
                     one message. Not a sighting and never persisted — it is
                     the picture, not the intelligence.

The split matters. The two vehicle events answer "what did this camera see?",
which is the product. `camera.tracks` answers "where is everything right now?",
which is what an overlay on live video needs several times a second and which
costs an order less when it is sent per camera instead of per vehicle. Mixing
the two made the drawing traffic the most expensive thing on the bus and made
every counter on an operator's screen wrong unless it remembered to filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ailab.aggregate.grammar import describe_plate
from ailab.track.merge import Vehicle
from ailab.types import Track

SCHEMA_VERSION = "ailab.vehicle.event.v1"

#: The live-boxes channel. Separate schema because it is a different kind of
#: message: one per camera per tick describing every vehicle currently drawable,
#: rather than one per vehicle describing what was read off it.
SCHEMA_TRACKS = "ailab.camera.tracks.v1"


@dataclass(slots=True)
class SourceIdentity:
    """Which camera this came from.

    The platform keys everything on `camera_id`, and cross-camera tracing is
    impossible without it, so it is required rather than inferred from a
    filename. `location` is passed through untouched when the caller knows it —
    the AI service does not own GIS.
    """

    camera_id: str
    name: str = ""
    location: dict[str, float] | None = None   # {"lat": .., "lon": ..}
    site: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"camera_id": self.camera_id}
        if self.name:
            payload["name"] = self.name
        if self.site:
            payload["site"] = self.site
        if self.location:
            payload["location"] = self.location
        return payload


#: Which region set explains a given decided format, so the parts breakdown is
#: computed under the same rules that accepted the plate in the first place.
_REGIONS_BY_FORMAT = {"uk_current": ("IN", "GB")}

#: Net displacement below this fraction of the frame's shorter side counts as
#: not having moved. Expressed as a fraction rather than in pixels because this
#: fleet mixes 720p and 1080p cameras, and a pixel threshold that is right for
#: one is wrong for the other by 50%. Judged here, where the frame size is
#: known, rather than left to a consumer that only receives the raw distance.
STATIONARY_FRACTION = 0.03


def _motion_block(vehicle: Vehicle, frame_size: tuple[int, int] | None) -> dict[str, Any]:
    """Which way the vehicle went, in image space, and how far.

    **This is not a compass bearing and must not be read as one.** The worker
    has no idea where the camera points; it knows only that the box moved down
    and right across the picture. Turning that into a direction on a map needs
    `cameras.heading_deg`, which the platform owns — so this reports what was
    actually observed and leaves the interpretation to the tier that can do it
    honestly.

    `direction` is the dominant axis of travel:

      approaching     moved down the frame — toward a forward-facing camera
      receding        moved up the frame — away from it
      crossing_left   moved left across the frame
      crossing_right  moved right across the frame
      stationary      did not materially move at all

    `stationary` is the one that earns its place for traffic work: a vehicle
    tracked for forty seconds that never moved is a queue or an obstruction,
    and that is invisible in a record holding one row per vehicle unless this
    is carried.

    `unknown` is reported rather than guessed when the movement could not be
    measured at all — a single sighting, or a frame size we were never told.
    A detector flicker must not arrive at the platform looking like a parked
    car, because the obstruction detector keys on exactly that.
    """
    dx, dy, distance = vehicle.motion_dx, vehicle.motion_dy, vehicle.motion_px
    reference = min(frame_size) if frame_size else 0

    if distance is None or dx is None or dy is None or reference <= 0:
        return {
            "direction": "unknown",
            "dx_px": None,
            "dy_px": None,
            "distance_px": None,
        }

    if distance < STATIONARY_FRACTION * reference:
        direction = "stationary"
    elif abs(dy) >= abs(dx):
        direction = "approaching" if dy > 0 else "receding"
    else:
        direction = "crossing_right" if dx > 0 else "crossing_left"

    return {
        "direction": direction,
        "dx_px": round(dx, 1),
        "dy_px": round(dy, 1),
        "distance_px": round(distance, 1),
    }


@dataclass(slots=True)
class LiveTrackBox:
    """One vehicle's current boxes, for drawing and nothing else.

    Assembled by the runner and shaped here, so the wire format has exactly one
    owner. Everything on it is a fact measured on the frame named by the batch's
    `captured_at`; nothing is derived, scored or interpreted.

    `plate_bbox` is the point of this message. A plate the detector has
    *localised* is the earliest thing that can honestly be drawn over a vehicle:
    it says "there is a plate, and it is here", which is true well before OCR
    has agreed with itself about what it says, and often true when OCR never
    manages to read it at all.
    """

    track_id: int
    class_name: str
    #: Where the vehicle is on the batch's frame, in full-frame pixels.
    bbox: Any
    #: Where its plate is, if the detector has found one. Full-frame pixels.
    plate_bbox: Any = None
    #: The plate detector's own confidence in that localisation.
    plate_detection_confidence: float = 0.0
    #: The current reading, empty while there is none. Never a placeholder.
    plate_text: str = ""
    plate_confidence: float = 0.0
    grammar_valid: bool = False
    ambiguous: bool = False
    corrected_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The compact form.

        Boxes go out as `[x1, y1, x2, y2]` rather than the `{"x1": ...}` dicts
        every other event uses, and fields that carry nothing are **omitted**
        rather than sent as null. That is a deliberate and local exception:
        this message is emitted several times a second per camera and carries
        one entry per vehicle in view, so its byte count is a design constraint
        in a way no other event's is. `BBox.to_dict()` also carries `w` and `h`,
        which are `x2 - x1` and `y2 - y1` — pure redundancy repeated per box per
        tick.

        Omission is not ambiguity here: a missing `plate` means no reading
        exists yet, which is precisely the state this channel was added to be
        able to draw.
        """
        entry: dict[str, Any] = {
            "track_id": self.track_id,
            "type": self.class_name,
            "bbox": _box(self.bbox),
        }
        if self.plate_bbox is not None:
            entry["plate_bbox"] = _box(self.plate_bbox)
            entry["plate_detection_confidence"] = round(self.plate_detection_confidence, 3)
        if self.plate_text:
            entry["plate"] = self.plate_text
            entry["confidence"] = round(self.plate_confidence, 4)
            entry["grammar_valid"] = self.grammar_valid
            entry["ambiguous"] = self.ambiguous
            if self.corrected_from:
                entry["corrected_from"] = self.corrected_from
        return entry


def _box(bbox: Any) -> list[float]:
    """A box as four numbers. See `LiveTrackBox.to_dict`."""
    return [round(value, 1) for value in bbox.as_xyxy()]


def track_batch_event(
    source: SourceIdentity,
    boxes: list[LiveTrackBox],
    frame_size: tuple[int, int] | None,
    captured_at: datetime | None,
    latency_ms: float | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Every drawable vehicle on one camera, as of one frame.

    ## Why this exists at all

    An overlay needs a box per vehicle several times a second. Sending that as
    one `vehicle.observed` per vehicle per tick makes the message whose entire
    job is to be prompt into the most expensive traffic on the bus — and it
    scales with the number of vehicles, which is exactly when latency matters
    most. One message per *camera* carries the same information: N boxes in one
    envelope instead of N envelopes.

    Measured: a 305-byte envelope, then 132 bytes for a vehicle whose plate has
    been located and 218 for one that has been read. Ten vehicles, three of them
    read, is 1,987 bytes — 9.7 KB/s per camera at five batches a second. The
    per-vehicle refresh this replaces was 793 bytes each, so ten of them at the
    same cadence is 39.6 KB/s, four times as much traffic for a third of the
    coverage: refreshes only ever described vehicles that had already been read.

    ## Why it is authoritative

    Because it describes the whole camera, a consumer can read it as complete: a
    vehicle that is not in the newest batch is no longer drawable on that
    camera, full stop. That is how a box learns to disappear promptly, without
    waiting out a timeout and without depending on a `vehicle.completed` event
    arriving. An empty `tracks` list is therefore a meaningful message — "this
    camera has nothing to draw" — and is sent once when the last vehicle leaves.

    ## What it is not

    Not a sighting, not a detection record, and not evidence of anything. It is
    never persisted, must never be counted, and must never be listed in a plate
    feed: a vehicle appears in dozens of consecutive batches, and each one is
    the same car, not a new one. The record of what was seen is
    `vehicle.completed`, exactly as before.
    """
    payload: dict[str, Any] = {
        "schema": SCHEMA_TRACKS,
        "event": "camera.tracks",
        "event_time": datetime.now(UTC).isoformat(),
        # The frame every box in this batch was measured on. An overlay places
        # boxes against this, not against the time the message arrived.
        "captured_at": captured_at.isoformat() if captured_at is not None else None,
        "source": source.to_dict(),
        "frame": (
            {"width": frame_size[0], "height": frame_size[1]}
            if frame_size is not None
            else None
        ),
        "tracks": [box.to_dict() for box in boxes],
        "run_id": run_id,
    }
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 1)
    return payload


def _plate_block(vehicle: Vehicle | Track) -> dict[str, Any]:
    result = vehicle.result
    if result is None or not result.text:
        return {"text": "", "confidence": 0.0, "readable": False}

    # Split into parts using the format consensus already settled on, so the
    # event cannot disagree with itself about whether the plate is valid.
    parts = describe_plate(result.text, _REGIONS_BY_FORMAT.get(result.grammar_format, ("IN",)))
    return {
        "text": result.text,
        "confidence": round(result.confidence, 4),
        "readable": True,
        "grammar_valid": result.grammar_valid,
        "grammar_note": result.grammar_note,
        "ambiguous": result.ambiguous,
        "corrected_from": result.corrected_from,
        "format": result.grammar_format,
        "state": parts.get("state", ""),
        "rto": parts.get("rto", ""),
        # Alternatives are carried so the platform can widen a watchlist match
        # when the top answer is uncertain, rather than only ever seeing one
        # string and having to trust it.
        "candidates": [c.to_dict() for c in result.candidates],
        "evidence": {
            "reads_total": result.reads_total,
            "reads_agreeing": result.reads_agreeing,
            "agreement": round(result.agreement, 4),
            "disagreement": round(result.disagreement, 4),
            "method": result.method,
            "char_confidences": result.char_confidences,
        },
    }


def _position_refresh_event(
    vehicle: Vehicle,
    source: SourceIdentity,
    latency_ms: float | None,
    run_id: str,
    frame_size: tuple[int, int] | None,
    live_bbox: Any,
    captured_at: datetime | None,
) -> dict[str, Any]:
    """A refresh carries a position and nothing else.

    A refresh repeats a reading the platform already has; the *only* new fact
    in it is where the vehicle has got to. The full event is a different size
    of thing: `evidence.reads` holds one entry per frame the plate was read in,
    each with its crop path, plus every candidate string and every per-character
    confidence. That grows for as long as the vehicle stays in view — so the
    message whose entire job is to move a rectangle was both the largest on the
    bus and getting larger the longer it mattered.

    Measured on one vehicle with a dozen reads behind it: 3,787 bytes of JSON
    for the full event against 793 for this one, a factor of 4.8, and the gap
    widens with every further read. Every one of those bytes is JSON the worker
    encodes, Redis stores, the API re-encodes and the browser parses before a
    box on screen can move.

    What stays is exactly what an overlay reads: the boxes, the frame they were
    measured in, the capture time to schedule against, and enough of the plate
    to label and colour the box. Nothing here is persisted — the `detections`
    table is written from `vehicle.completed` alone — so trimming it loses no
    record of anything.
    """
    result = vehicle.result
    plate: dict[str, Any] = {
        "text": result.text if result else "",
        "confidence": round(result.confidence, 4) if result else 0.0,
        "readable": bool(result and result.text),
        "grammar_valid": bool(result and result.grammar_valid),
        "ambiguous": bool(result and result.ambiguous),
        "corrected_from": result.corrected_from if result else None,
    }
    last_detection = vehicle.plate_detections[-1] if vehicle.plate_detections else None
    if last_detection is not None:
        plate["bbox"] = last_detection.bbox.to_dict()
        plate["detection_confidence"] = round(last_detection.confidence, 4)

    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "event": "vehicle.observed",
        "event_time": datetime.now(UTC).isoformat(),
        "captured_at": captured_at.isoformat() if captured_at is not None else None,
        "position_refresh": True,
        "source": source.to_dict(),
        "frame": (
            {"width": frame_size[0], "height": frame_size[1]}
            if frame_size is not None
            else None
        ),
        "vehicle": {
            "vehicle_id": vehicle.vehicle_id,
            "track_ids": vehicle.track_ids,
            "type": vehicle.class_name,
            "confidence": round(vehicle.mean_detection_confidence, 4),
            "bbox": vehicle.bbox.to_dict() if vehicle.bbox is not None else None,
            "live_bbox": live_bbox.to_dict() if live_bbox is not None else None,
        },
        "plate": plate,
        "run_id": run_id,
    }
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 1)
    return payload


def vehicle_event(
    vehicle: Vehicle,
    source: SourceIdentity,
    kind: str = "vehicle.completed",
    latency_ms: float | None = None,
    run_id: str = "",
    frame_size: tuple[int, int] | None = None,
    live_bbox: Any = None,
    captured_at: datetime | None = None,
    position_refresh: bool = False,
) -> dict[str, Any]:
    """One vehicle, shaped for the platform.

    `frame_size` is (width, height) of the frame the boxes were measured in.
    Every bbox in this payload is in source-frame pixels, and a consumer that
    draws them — an overlay on a video element, say — cannot scale them without
    knowing that space. Omitting it makes the coordinates unusable.

    ## Two boxes, because they answer two different questions

    `vehicle.bbox` is the **best** sighting: the largest, most confident look at
    the vehicle, which is the frame worth keeping as evidence and the one the
    crop was cut from. It is what the platform stores.

    `vehicle.live_bbox` is the **latest** sighting — where the vehicle was in
    the frame identified by `captured_at`. That is the only box an overlay can
    honestly draw, because it is the only one with a timestamp attached. Drawing
    the best box over live video puts the rectangle wherever the vehicle
    happened to look biggest, which for a car crossing the frame is a position
    it occupied seconds earlier and has long since left.

    `captured_at` is the wall clock of that frame, not of this event. The
    difference between the two is `latency_ms`, and a consumer that wants the
    box to land on the vehicle needs the capture time rather than the emit time.

    `position_refresh` marks an event that repeats a reading the platform has
    already been told about, purely to say where the vehicle has got to. It is
    for drawing, not for reporting: counted as a detection it inflates every
    figure on an operator's screen, and listed in a plate feed it fills the feed
    with the same car several times a second. A refresh is emitted in the lean
    shape described in `_position_refresh_event` — the boxes and the clock, no
    evidence — because it is sent several times a second per vehicle and the
    only thing a consumer can do with it is move a rectangle.
    """
    if position_refresh:
        # Nothing new to report about the plate, so nothing but the position is
        # sent. See `_position_refresh_event`.
        return _position_refresh_event(
            vehicle, source, latency_ms, run_id, frame_size, live_bbox, captured_at
        )

    best_read = max(vehicle.reads, key=lambda r: r.vote_weight, default=None)
    last_detection = vehicle.plate_detections[-1] if vehicle.plate_detections else None

    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "event": kind,
        "event_time": datetime.now(UTC).isoformat(),
        # When the frame the boxes were measured in was captured. Distinct from
        # event_time by the length of the pipeline; see the docstring.
        "captured_at": captured_at.isoformat() if captured_at is not None else None,
        # True when this repeats a known reading to update its position only.
        "position_refresh": position_refresh,
        "source": source.to_dict(),
        "frame": (
            {"width": frame_size[0], "height": frame_size[1]}
            if frame_size is not None
            else None
        ),
        "vehicle": {
            "vehicle_id": vehicle.vehicle_id,
            "track_ids": vehicle.track_ids,
            "type": vehicle.class_name,
            "confidence": round(vehicle.mean_detection_confidence, 4),
            "bbox": vehicle.bbox.to_dict() if vehicle.bbox is not None else None,
            "live_bbox": live_bbox.to_dict() if live_bbox is not None else None,
            "first_seen_s": round(vehicle.first_seen_s, 3),
            "last_seen_s": round(vehicle.last_seen_s, 3),
            "duration_s": round(vehicle.duration_s, 3),
            "frames_tracked": vehicle.frames_tracked,
            "merged_from_fragments": vehicle.merged_from_fragments,
            # How the vehicle moved through the frame. Image space, not
            # compass — see `_motion_block`.
            "motion": _motion_block(vehicle, frame_size),
        },
        "plate": _plate_block(vehicle),
        "evidence": {
            "vehicle_crop": vehicle.best_crop_path,
            "plate_crop": best_read.crop_path if best_read else None,
            "plate_crops": [r.crop_path for r in vehicle.reads if r.crop_path],
            "reads": [
                {
                    "frame_index": r.frame_index,
                    "t_s": round(r.t_s, 3),
                    "text": r.text,
                    "ocr_confidence": round(r.ocr_confidence, 4),
                    "plate_confidence": round(r.plate_confidence, 4),
                    "grammar_valid": r.grammar_valid,
                    "variant": r.preprocess_variant,
                    "crop": r.crop_path,
                }
                for r in vehicle.reads
            ],
        },
        "run_id": run_id,
    }

    if last_detection is not None:
        payload["plate"]["bbox"] = last_detection.bbox.to_dict()
        payload["plate"]["detection_confidence"] = round(last_detection.confidence, 4)

    if latency_ms is not None:
        # Capture-to-event, the number that decides whether an alert is useful.
        payload["latency_ms"] = round(latency_ms, 1)

    return payload


@dataclass
class EventSink:
    """Where events go. Deliberately dumb — no platform logic lives here."""

    path: Any = None                     # Path or None
    echo: bool = False
    on_event: Any = None                 # optional callable
    written: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    _handle: Any = field(default=None, repr=False)

    def open(self) -> EventSink:
        if self.path is not None:
            # Held open for the life of the sink and closed in close(); a
            # context manager per event would reopen the file thousands of
            # times on a live stream.
            self._handle = open(self.path, "a", encoding="utf-8")  # noqa: SIM115
        return self

    def emit(self, event: dict[str, Any]) -> None:
        import json

        self.written += 1
        kind = str(event.get("event", "unknown"))
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1

        line = json.dumps(event, default=str)
        if self._handle is not None:
            self._handle.write(line + "\n")
            # Flushed per event: a live consumer tailing this file should see an
            # alert now, not when a buffer happens to fill.
            self._handle.flush()
        if self.echo:
            print(line, flush=True)
        if self.on_event is not None:
            self.on_event(event)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> EventSink:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()
