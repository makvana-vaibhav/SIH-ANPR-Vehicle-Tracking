"""Continuous inference over a live stream.

The batch pipeline processes a file and reports at the end. A camera never ends,
so this differs in three ways that are not cosmetic:

* **Events are emitted as they happen.** A vehicle that is still in view emits a
  provisional `vehicle.observed`; when it leaves, a final `vehicle.completed`.
  Waiting for the stream to finish would mean never alerting at all.
* **Memory is bounded.** Tracks are retired and their evidence released once the
  vehicle has gone. A batch run keeps everything because it ends; a worker that
  did the same would grow until it died.
* **Falling behind is expected and measured.** The reader drops frames to stay
  current; the processor records how many, and how long capture-to-event takes.
  A worker that quietly drops 90% of its input while reporting healthy fps is
  reporting a number nobody asked for.

This runs one camera per instance. That is deliberate: it is the unit that gets
scaled horizontally, and keeping it single-camera means a worker's cost and
capacity are directly measurable rather than entangled with its neighbours.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ailab.aggregate.consensus import consensus
from ailab.artifacts import RunDirectory
from ailab.config import RunConfig
from ailab.logging import get_logger
from ailab.pipeline import Pipeline
from ailab.stream.events import (
    EventSink,
    LiveTrackBox,
    SourceIdentity,
    track_batch_event,
    vehicle_event,
)
from ailab.stream.reader import StreamReader, redact
from ailab.track.merge import Vehicle
from ailab.types import BBox, Detection, Frame, StageTimer, Track, TrackObservation

log = get_logger(__name__)


@dataclass
class StreamStats:
    """What the worker did, and how promptly."""

    frames_processed: int = 0
    events_observed: int = 0
    events_completed: int = 0
    track_batches: int = 0
    boxes_published: int = 0
    tracks_started: int = 0
    tracks_retired: int = 0
    plates_read: int = 0
    discontinuities: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def elapsed_s(self) -> float:
        return max(1e-6, time.perf_counter() - self.started_at)

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
        return ordered[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_processed": self.frames_processed,
            "processing_fps": round(self.frames_processed / self.elapsed_s, 2),
            "elapsed_s": round(self.elapsed_s, 2),
            "tracks_started": self.tracks_started,
            "tracks_retired": self.tracks_retired,
            "discontinuities": self.discontinuities,
            "plates_read": self.plates_read,
            "events_observed": self.events_observed,
            "events_completed": self.events_completed,
            # The live-boxes channel, reported separately because it is not
            # sightings: many batches describe the same vehicles, and counting
            # them among detections would inflate every figure that matters.
            "track_batches": self.track_batches,
            "boxes_published": self.boxes_published,
            "boxes_per_batch": (
                round(self.boxes_published / self.track_batches, 2)
                if self.track_batches
                else 0.0
            ),
            # Capture-to-event: the number that decides whether an alert is
            # actionable. Throughput without this says nothing about latency.
            "latency_ms": {
                "count": len(self.latencies_ms),
                "median": round(self.percentile(0.5), 1),
                "p90": round(self.percentile(0.9), 1),
                "p99": round(self.percentile(0.99), 1),
                "max": round(max(self.latencies_ms), 1) if self.latencies_ms else 0.0,
            },
        }


@dataclass(slots=True)
class _LiveTrack:
    """A track being watched right now."""

    track: Track
    last_frame_seen: int
    last_emitted_plate: str = ""
    last_emitted_confidence: float = 0.0
    emitted_observed: int = 0
    #: Wall clock of the last event emitted for this track, from
    #: `time.perf_counter`. Paces position refreshes.
    last_emit_s: float = 0.0
    #: How many of this track's events carried no new reading, only a new
    #: position. Bounded so a vehicle parked in view cannot emit forever.
    position_refreshes: int = 0


class StreamRunner:
    """Processes one live camera continuously, emitting events as it goes."""

    def __init__(
        self,
        config: RunConfig,
        source: SourceIdentity,
        sink: EventSink,
        run_dir: RunDirectory | None = None,
        pipeline: Pipeline | None = None,
    ) -> None:
        """`pipeline` lets a caller supply models that are already loaded.

        A supervisor that rotates cameras through a fixed set of slots builds a
        runner per camera but wants the five ONNX sessions to outlive it —
        loading them costs ~500 ms and, more importantly, each carries a native
        thread pool and arena that are only released when the object is
        collected. Passed one, this runner borrows it and resets its per-run
        counters; the caller owns its lifetime and must not share one between
        two runners at the same time, because inference state is not
        thread-safe.
        """
        self.config = config
        self.source = source
        self.sink = sink
        self.run_dir = run_dir
        if pipeline is None:
            self.pipeline = Pipeline(config)
        else:
            pipeline.reset_run_state()
            self.pipeline = pipeline
        self.timer: StageTimer = self.pipeline.timer
        self.stats = StreamStats()

        self._live: dict[int, _LiveTrack] = {}
        # (width, height) of the frames being analysed, learned from the first
        # one. Every bbox the runner emits is in this space, so it travels with
        # the event — a consumer drawing the boxes cannot scale them otherwise.
        self._frame_size: tuple[int, int] | None = None
        self._fps = 25.0
        self._next_vehicle_id = 0
        self._reads_by_track: dict[int, int] = {}
        self._crops_saved: dict[int, int] = {}
        self._orphans: list[Any] = []
        self._labels: dict[int, Any] = {}
        #: `time.perf_counter` of the last `camera.tracks` batch, for pacing.
        self._last_batch_s: float = 0.0
        #: Whether the last batch carried anything. A camera that empties has
        #: to say so once — the batch is authoritative, so a consumer learns a
        #: vehicle has gone by its absence, and it can only learn that from a
        #: message that actually arrives. Without this, the last box on a quiet
        #: camera would hang on screen until its own timeout.
        self._batch_had_boxes: bool = False

    # ─────────────────────────────────────────────────────────────────
    def run(
        self,
        url: str,
        max_seconds: float | None = None,
        max_frames: int | None = None,
        realtime: bool = True,
    ) -> dict[str, Any]:
        """Consume the stream until it ends or a limit is reached."""
        cfg = self.config
        reader = StreamReader(url, realtime=realtime).start()
        log.info(
            "streaming %s as camera %s (%.1f fps source)",
            # A federated grid carries its credentials in the URL, so every
            # line that prints a source has to redact — including this one.
            redact(url), self.source.camera_id, reader.fps,
        )

        self._fps = reader.fps
        self.pipeline._tracker = self.pipeline._tracker or None
        from ailab.registry import create

        self.pipeline._tracker = create(
            "tracker", cfg.tracker.engine, cfg.tracker, reader.fps
        )
        from ailab.plate.scheduler import CropGate, PlateScheduler

        self.pipeline.scheduler = PlateScheduler(cfg.plate.schedule)
        self.pipeline.crop_gate = CropGate(cfg.ocr.crop_gate)

        deadline = time.perf_counter() + max_seconds if max_seconds else None
        frame_index = 0

        try:
            while True:
                if deadline and time.perf_counter() > deadline:
                    log.info("time limit reached")
                    break
                if max_frames and self.stats.frames_processed >= max_frames:
                    log.info("frame limit reached")
                    break

                captured = reader.read(timeout=2.0)
                if captured is None:
                    if reader.finished:
                        break
                    continue

                self._process(captured, frame_index)
                frame_index += 1
                self.stats.frames_processed += 1

                if self.stats.frames_processed % 100 == 0:
                    self._log_progress(reader)
        finally:
            reader.stop()
            # The stream ended, but the vehicles still in view are real and
            # their evidence is complete. Emit them rather than discarding.
            self._retire_all()

        report = {
            "source": self.source.to_dict(),
            "stream": self.stats.to_dict(),
            "reader": reader.stats.to_dict(),
            "stages": self.timer.to_dict(),
            "plate_schedule": self.pipeline.scheduler.stats(),
            "crop_gate": self.pipeline.crop_gate.stats(),
            "events": {"written": self.sink.written, "by_kind": dict(self.sink.by_kind)},
        }
        self._log_summary(report)
        return report

    # ─────────────────────────────────────────────────────────────────
    def _process(self, captured: Any, frame_index: int) -> None:
        if self._frame_size is None and captured.image is not None:
            height, width = captured.image.shape[:2]
            self._frame_size = (int(width), int(height))
        pipeline = self.pipeline
        if captured.discontinuity:
            # The recording looped or the camera restarted. Track identities,
            # Kalman states and the scheduler's per-vehicle history all describe
            # a scene that no longer exists; carrying them across the cut would
            # associate vehicles from before it with vehicles after it. Vehicles
            # in view at the cut are completed with the evidence they have.
            self._on_discontinuity()

        frame = Frame(
            index=frame_index,
            t_s=captured.source_t_s,
            image=captured.image,
            source_name=self.source.camera_id,
            wall_time=captured.wall_time,
        )

        t0 = time.perf_counter()
        detections = pipeline.detector.detect(frame.image, frame.index, frame.t_s)
        self.timer.add("detect", time.perf_counter() - t0)

        t0 = time.perf_counter()
        tracked = pipeline._tracker.update(detections, frame.index, frame.t_s)
        self.timer.add("track", time.perf_counter() - t0)

        tracks_view: dict[int, Track] = {}
        for detection in tracked:
            live = self._observe(detection, frame_index)
            if live is not None:
                tracks_view[detection.track_id] = live.track

        plates = pipeline._find_plates(
            frame.image, tracked, frame.index, frame.t_s, tracks_view
        )
        for plate in plates:
            before = len(tracks_view.get(plate.track_id, Track(0, "", 0)).plate_reads) \
                if plate.track_id in tracks_view else 0
            pipeline._read_plate(
                frame.image, plate, tracks_view, self.run_dir or _NullRunDir(),
                self._reads_by_track, self._crops_saved, self._labels, self._orphans,
            )
            if plate.track_id in tracks_view:
                after = len(tracks_view[plate.track_id].plate_reads)
                if after > before:
                    self.stats.plates_read += 1

        self._emit_observed(tracks_view, captured)
        # After the observed events, so that a batch never describes a vehicle
        # the platform has not yet been told about.
        self._emit_track_batch(captured)
        self._retire_stale(captured)

    def _on_discontinuity(self) -> None:
        """Rebuild all long-lived state after a scene cut."""
        from ailab.plate.scheduler import CropGate, PlateScheduler
        from ailab.registry import create

        live = len(self._live)
        self._retire_all()
        self.pipeline._tracker = create(
            "tracker", self.config.tracker.engine, self.config.tracker, self._fps
        )
        self.pipeline.scheduler = PlateScheduler(self.config.plate.schedule)
        self.pipeline.crop_gate = CropGate(self.config.ocr.crop_gate)
        self._reads_by_track.clear()
        self._crops_saved.clear()
        self._labels.clear()
        self.stats.discontinuities += 1
        log.info("scene discontinuity: completed %d in-view vehicle(s), state rebuilt", live)

    def _observe(self, detection: Detection, frame_index: int) -> _LiveTrack | None:
        track_id = detection.track_id
        if track_id is None:
            return None

        live = self._live.get(track_id)
        if live is None:
            track = Track(
                track_id=track_id,
                class_name=detection.class_name,
                class_id=detection.class_id,
            )
            live = _LiveTrack(track=track, last_frame_seen=frame_index)
            self._live[track_id] = live
            self.stats.tracks_started += 1

        live.last_frame_seen = frame_index
        live.track.class_name = detection.class_name
        live.track.observe(
            TrackObservation(
                frame_index=detection.frame_index,
                t_s=detection.t_s,
                bbox=detection.bbox,
                confidence=detection.confidence,
                detected=True,
            )
        )
        return live

    def _emit_observed(self, tracks_view: dict[int, Track], captured: Any) -> None:
        """Provisional events, so the platform can act before the vehicle leaves.

        Two things make a provisional event worth sending, and they are capped
        separately because they cost different things:

        **A new reading** — a different plate, or a materially better
        confidence. This is information the platform did not have, and it is
        what an alert fires on. Bounded by `max_observed_per_track`.

        **A new position** — the same reading, from a later frame. This carries
        no new intelligence and is never persisted, but without it an overlay
        drawn on live video has nothing to redraw: the box stays pinned where
        the vehicle was when its plate first resolved and sits there while the
        vehicle drives out of shot. Paced by `observed_refresh_s` and bounded by
        `max_position_refresh_per_track`, so following a vehicle costs a couple
        of small messages a second rather than one per frame.

        Re-sending an unchanged reading *every frame* would flood the platform
        with events carrying nothing new, which is why the pacing exists rather
        than simply emitting whenever a track is alive.
        """
        stream = self.config.stream
        threshold = stream.observed_confidence_delta
        now_s = time.perf_counter()

        for track_id, track in tracks_view.items():
            live = self._live.get(track_id)
            if live is None or track.result is None or not track.result.text:
                continue

            changed = track.result.text != live.last_emitted_plate
            improved = track.result.confidence - live.last_emitted_confidence >= threshold
            new_reading = (changed or improved) and (
                live.emitted_observed < stream.max_observed_per_track
            )

            if new_reading:
                live.last_emitted_plate = track.result.text
                live.last_emitted_confidence = track.result.confidence
                live.emitted_observed += 1
                refresh_only = False
            else:
                # Nothing new to say about the plate. Say where the vehicle is
                # instead, if it is time to and this track has not had its fill.
                if stream.observed_refresh_s <= 0.0:
                    continue
                if live.position_refreshes >= stream.max_position_refresh_per_track:
                    continue
                if now_s - live.last_emit_s < stream.observed_refresh_s:
                    continue
                live.position_refreshes += 1
                refresh_only = True

            live.last_emit_s = now_s
            self.stats.events_observed += 1
            self.sink.emit(
                vehicle_event(
                    self._as_vehicle(track),
                    self.source,
                    kind="vehicle.observed",
                    latency_ms=captured.age_ms,
                    run_id=self.source.camera_id,
                    frame_size=self._frame_size,
                    live_bbox=self._latest_bbox(track),
                    captured_at=captured.wall_time,
                    position_refresh=refresh_only,
                )
            )
            self.stats.latencies_ms.append(captured.age_ms)

    def _emit_track_batch(self, captured: Any) -> None:
        """Publish every drawable vehicle on this camera, in one message.

        ## The cut point: localised, not read

        A vehicle enters the batch the moment the plate detector has **found** a
        plate on it — `Track.plate_location` — regardless of whether OCR has
        read it, or ever will. That is the earliest instant at which there is
        something true to draw over a plate, and it is considerably earlier than
        a reading: OCR needs several distinct looks to reach consensus, and
        measured on this fleet only a third of tracked vehicles ever yield a
        plate at all. Waiting for text meant two-thirds of the traffic got no
        box, and the rest got one late.

        Nothing here asserts a reading. The entry carries the plate's *position*
        and the detector's confidence in it; `plate` is present only when there
        is genuinely a reading, and a consumer that wants to distinguish
        "looking at a plate" from "read a plate" has exactly the fields to do it.

        ## Why one message per camera

        The alternative — one event per vehicle per tick — makes the traffic
        whose entire purpose is promptness scale with the number of vehicles,
        which is precisely when promptness matters. One envelope carrying N
        boxes is an order cheaper than N envelopes, and it buys a property the
        per-vehicle form cannot have: because the batch describes the whole
        camera, absence from it is *information*. A consumer knows a vehicle has
        gone because it is not in the newest batch, rather than waiting out a
        timeout or hoping a `vehicle.completed` arrives.

        That is also why an emptied camera publishes one final empty batch. A
        camera with nothing to draw has to say so, or the last box on screen
        outlives the car by the length of whatever timeout the consumer uses.

        Built from `self._live` rather than from the frame's own tracks, so a
        vehicle briefly occluded keeps its box at its last known position
        instead of flickering out and back.
        """
        stream = self.config.stream
        if stream.track_batch_s <= 0.0:
            return

        now_s = time.perf_counter()
        if now_s - self._last_batch_s < stream.track_batch_s:
            return

        boxes: list[LiveTrackBox] = []
        for track_id, live in self._live.items():
            track = live.track
            located = track.plate_location
            if located is None:
                continue
            bbox = self._latest_bbox(track)
            if bbox is None:
                continue
            result = track.result
            boxes.append(
                LiveTrackBox(
                    track_id=track_id,
                    class_name=track.class_name,
                    bbox=bbox,
                    plate_bbox=self._anchored_plate(
                        located.bbox, track.plate_location_vehicle, bbox
                    ),
                    plate_detection_confidence=located.confidence,
                    plate_text=result.text if result else "",
                    plate_confidence=result.confidence if result else 0.0,
                    grammar_valid=bool(result and result.grammar_valid),
                    ambiguous=bool(result and result.ambiguous),
                    corrected_from=result.corrected_from if result else None,
                )
            )

        # Nothing to say, and we already said so. Staying quiet is correct here:
        # a camera watching an empty road should not be publishing at 5 Hz.
        if not boxes and not self._batch_had_boxes:
            self._last_batch_s = now_s
            return

        self._last_batch_s = now_s
        self._batch_had_boxes = bool(boxes)
        self.stats.track_batches += 1
        self.stats.boxes_published += len(boxes)
        self.sink.emit(
            track_batch_event(
                self.source,
                boxes,
                frame_size=self._frame_size,
                captured_at=captured.wall_time,
                latency_ms=captured.age_ms,
                run_id=self.source.camera_id,
            )
        )

    @staticmethod
    def _anchored_plate(
        plate: BBox, vehicle_then: BBox | None, vehicle_now: Any
    ) -> BBox:
        """The localised plate, carried onto the frame this batch describes.

        A plate is localised on one frame and the vehicle keeps moving. Sent as
        the absolute box it was measured as, it drifts behind the car — and the
        drift never stops growing, because the scheduler *stops searching* a
        vehicle once its plate has converged. The firmest rectangle on screen
        would be the most stale one.

        What does not go stale is where the plate sits **on the vehicle**: a
        number plate does not move about on a car. So the position is expressed
        as a fraction of the vehicle box it was measured against and reapplied
        to the vehicle box now, which also handles the vehicle growing as it
        approaches.

        This is inference, and worth being plain about: the plate's position is
        exact on the frame it was localised on and derived from the vehicle's
        own measured box after that. It is the same class of claim as the
        overlay's velocity prediction — computed from observations, never
        invented — and it is strictly closer to the truth than repeating a
        position the car has demonstrably left.

        Falls back to the measured box when there is nothing to anchor to.
        """
        if vehicle_then is None or vehicle_now is None:
            return plate
        width, height = vehicle_then.width, vehicle_then.height
        if width <= 0.0 or height <= 0.0:
            return plate

        fx1 = (plate.x1 - vehicle_then.x1) / width
        fy1 = (plate.y1 - vehicle_then.y1) / height
        fx2 = (plate.x2 - vehicle_then.x1) / width
        fy2 = (plate.y2 - vehicle_then.y1) / height
        return BBox(
            vehicle_now.x1 + fx1 * vehicle_now.width,
            vehicle_now.y1 + fy1 * vehicle_now.height,
            vehicle_now.x1 + fx2 * vehicle_now.width,
            vehicle_now.y1 + fy2 * vehicle_now.height,
        )

    @staticmethod
    def _latest_bbox(track: Track) -> Any:
        """Where the track is *now*, not where it looked best.

        `Vehicle.bbox` is the best observation — largest and most confident,
        which is the right frame to cut a crop from and the wrong one to draw
        over live video, because for a vehicle crossing the frame it is a
        position the vehicle left seconds ago.
        """
        return track.observations[-1].bbox if track.observations else None

    def _retire_stale(self, captured: Any) -> None:
        """Complete the vehicles that have been out of view for long enough.

        Measured in source seconds, not in analysed frames. This used to count
        frames — `reader.fps * retire_after_s` of them — but the count ticked
        once per *analysed* frame, and a worker that keeps up with a camera by
        dropping most of it analyses a fraction of the frames it decodes. At
        the measured one-in-nine, the 1.5 s the config asks for became ~13 s:
        every `vehicle.completed` — the event that is persisted, that raises
        the alert, that ends the box on screen — arrived thirteen seconds after
        the vehicle had gone.
        """
        now_s = captured.source_t_s
        limit_s = self.config.stream.retire_after_s
        stale = [
            track_id
            for track_id, live in self._live.items()
            if now_s - live.track.last_seen_s > limit_s
        ]
        for track_id in stale:
            self._complete(
                self._live.pop(track_id), captured.age_ms, captured.wall_time
            )

    def _retire_all(self) -> None:
        for live in list(self._live.values()):
            self._complete(live, latency_ms=None, captured_at=None)
        self._live.clear()

    def _complete(
        self,
        live: _LiveTrack,
        latency_ms: float | None,
        captured_at: datetime | None,
    ) -> None:
        track = live.track
        track.result = consensus(track.plate_reads, self.config.consensus)
        self.stats.tracks_retired += 1
        self.stats.events_completed += 1
        self.sink.emit(
            vehicle_event(
                self._as_vehicle(track),
                self.source,
                kind="vehicle.completed",
                latency_ms=latency_ms,
                run_id=self.source.camera_id,
                frame_size=self._frame_size,
                # The last place the vehicle was seen before it was retired.
                # An overlay uses this to clear the box at the right moment
                # rather than leaving it floating where the car no longer is.
                live_bbox=self._latest_bbox(track),
                captured_at=captured_at,
            )
        )
        # Release the evidence: a long-running worker cannot keep every crop
        # and every read for every vehicle it has ever seen.
        track.observations.clear()
        track.plate_reads.clear()
        track.plate_detections.clear()
        track.plate_location = None
        track.plate_location_vehicle = None

    def _as_vehicle(self, track: Track) -> Vehicle:
        self._next_vehicle_id += 1
        confidences = [o.confidence for o in track.observations if o.detected]
        motion_dx, motion_dy, motion_px = track.net_motion() or (None, None, None)
        return Vehicle(
            vehicle_id=self._next_vehicle_id,
            track_ids=[track.track_id],
            class_name=track.class_name,
            reads=list(track.plate_reads),
            plate_detections=list(track.plate_detections),
            result=track.result,
            first_seen_s=track.first_seen_s,
            last_seen_s=track.last_seen_s,
            first_frame=track.first_frame,
            last_frame=track.last_frame,
            frames_tracked=track.frame_count,
            mean_detection_confidence=(
                sum(confidences) / len(confidences) if confidences else 0.0
            ),
            best_crop_path=track.best_crop_path,
            bbox=track.best_observation.bbox if track.best_observation else None,
            motion_dx=motion_dx,
            motion_dy=motion_dy,
            motion_px=motion_px,
        )

    # ── reporting ──
    def _log_progress(self, reader: StreamReader) -> None:
        stats = reader.stats
        log.info(
            "  %d frames · %.1f fps · %d live · %d events · dropped %.0f%% · p90 latency %.0f ms",
            self.stats.frames_processed,
            self.stats.frames_processed / self.stats.elapsed_s,
            len(self._live),
            self.sink.written,
            100.0 * stats.frames_dropped / max(1, stats.frames_decoded),
            self.stats.percentile(0.9),
        )

    def _log_summary(self, report: dict[str, Any]) -> None:
        stream, reader = report["stream"], report["reader"]
        log.info(
            "stream ended: %d frames at %.2f fps, %d events "
            "(%d observed, %d completed), dropped %.0f%% of %d decoded, "
            "median latency %.0f ms",
            stream["frames_processed"], stream["processing_fps"],
            report["events"]["written"], stream["events_observed"],
            stream["events_completed"], 100.0 * reader["drop_rate"],
            reader["frames_decoded"], stream["latency_ms"]["median"],
        )


class _NullRunDir:
    """Stand-in when streaming without an artifact directory.

    Streaming for latency measurement should not be writing JPEGs to disk; the
    crop paths simply come back as None and every other field is unaffected.
    """

    def save_plate_crop(self, image: Any, name: str) -> None:
        return None

    def save_vehicle_crop(self, image: Any, name: str) -> None:
        return None

    @property
    def path(self) -> Path:
        return Path(".")

    @property
    def started_at(self) -> datetime:
        return datetime.now(UTC)
