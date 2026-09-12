"""The pipeline.

    frames → object detection → tracking → plate detection → crop conditioning
          → OCR → per-read scoring → multi-frame consensus → vehicle result

Each stage is a component resolved from the registry by name, so a run is
defined entirely by its config and two runs differing in one line are directly
comparable. Nothing is discarded on the way through: every detection, every
plate box, every OCR attempt including the rejected ones is retained and
written out, because the failures are the point of the exercise.

Timing is recorded per stage. "The pipeline runs at 6 fps" is not actionable;
"OCR is 71% of frame time and plate detection is 14%" tells you what to change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ailab.aggregate.consensus import consensus
from ailab.aggregate.grammar import normalise, validate
from ailab.annotate import Annotator, TrackLabel, VideoWriter, transcode_to_h264
from ailab.artifacts import RunDirectory, runs_root
from ailab.config import RunConfig
from ailab.logging import get_logger
from ailab.plate.scheduler import CropGate, PlateScheduler
from ailab.preprocess import build_variants, is_legible, measure, measure_exposure
from ailab.registry import create
from ailab.sources import open_source
from ailab.sources.base import SourceInfo
from ailab.track.merge import Vehicle, fragmentation_stats, merge
from ailab.types import (
    BBox,
    Detection,
    PlateDetection,
    PlateRead,
    StageTimer,
    Track,
    TrackObservation,
)

log = get_logger(__name__)


@dataclass(slots=True)
class RunResult:
    """Everything one run produced."""

    run_dir: RunDirectory
    source: SourceInfo
    tracks: dict[int, Track]
    detections: list[Detection]
    plate_detections: list[PlateDetection]
    # Plates read from the frame that no tracked vehicle could be held
    # responsible for — the object detector missed the vehicle, or the plate
    # sits outside every box. They are evidence of a detector gap, so they are
    # reported rather than dropped.
    orphan_reads: list[PlateRead]
    timer: StageTimer
    frames_read: int
    frames_processed: int
    wall_seconds: float
    components: dict[str, Any]
    vehicles: list[Vehicle] = field(default_factory=list)
    fragmentation: dict[str, Any] = field(default_factory=dict)
    plate_ownership: dict[str, int] = field(default_factory=dict)
    schedule_stats: dict[str, Any] = field(default_factory=dict)
    crop_gate_stats: dict[str, Any] = field(default_factory=dict)
    annotated_path: Path | None = None
    annotated_codec: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Builds the components a config asks for and runs footage through them."""

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.timer = StageTimer()

        detector_weights = str(config.resolve_path(config.detector.weights))
        self.detector = create("detector", config.detector.engine, config.detector, detector_weights)

        plate_weights = (
            str(config.resolve_path(config.plate.weights)) if config.plate.weights else None
        )
        self.plate_detector = create(
            "plate_detector", config.plate.engine, config.plate, plate_weights
        )
        self.ocr = create("ocr", config.ocr.engine, config.ocr)

        self.plate_bearing = {c.lower() for c in config.detector.plate_bearing_classes}
        self._duplicate_plates = 0
        self._reattributed_plates = 0
        self._best_inset: dict[int, float] = {}
        self._tracker = None  # built per run, since it needs the source frame rate

    def reset_run_state(self) -> None:
        """Forget the previous run, keeping the loaded models.

        Constructing a Pipeline loads and graph-optimises five ONNX models, and
        each one allocates a native thread pool and memory arena that live
        until the object is collected. The worker rotates cameras through a
        fixed set of slots, so building a fresh Pipeline per rotation left the
        old pools waiting on Python's cyclic collector: measured, the worker
        grew from 29 threads and 1.4 GB to 62 threads and 3.5 GB in twelve
        minutes, on the way to the exit-137 kills this project has seen.

        Reusing the models and clearing the counters avoids that entirely.
        Tracker, scheduler and crop gate are not reset here because
        `StreamRunner.run` already rebuilds them per run — the tracker needs
        the source frame rate, which is only known once the stream is open.
        """
        self._duplicate_plates = 0
        self._reattributed_plates = 0
        self._best_inset.clear()
        self._tracker = None

    @staticmethod
    def _is_converged(track: Track | None, cfg: RunConfig) -> bool:
        """Has this vehicle's plate settled beyond useful doubt?

        Used in two places that must agree: the plate scheduler (stop looking)
        and the OCR gate (stop reading). If they disagreed, one would keep
        producing work the other discards.
        """
        if not cfg.ocr.stop_when_confident or track is None or track.result is None:
            return False
        result = track.result
        return (
            result.grammar_valid
            and not result.ambiguous
            and result.reads_agreeing >= cfg.ocr.stop_min_agreeing
            and result.confidence >= cfg.ocr.stop_min_confidence
        )

    def describe_components(self) -> dict[str, Any]:
        components: dict[str, Any] = {
            "detector": self.detector.describe(),
            "plate_detector": self.plate_detector.describe(),
            "ocr": self.ocr.describe(),
        }
        if self._tracker is not None:
            components["tracker"] = self._tracker.describe()
        return components

    # ─────────────────────────────────────────────────────────────────
    def run(self, source_path: str, run_root: Path | None = None) -> RunResult:
        cfg = self.config
        source = open_source(source_path, cfg.source)
        info = source.info
        log.info(
            "source: %s  %dx%d  %.2f fps  %s frames",
            info.name, info.width, info.height, info.fps,
            info.total_frames or "unknown",
        )

        self._tracker = create("tracker", cfg.tracker.engine, cfg.tracker, info.fps)
        self.scheduler = PlateScheduler(cfg.plate.schedule)
        self.crop_gate = CropGate(cfg.ocr.crop_gate)
        run_dir = RunDirectory(run_root or runs_root(), cfg, info.name)
        log.info("writing to %s", run_dir.path)

        annotator = Annotator(draw_trails=cfg.output.draw_trails)
        writer: VideoWriter | None = None
        if cfg.output.annotated_video:
            # Frames are stride-sampled, so the annotated video must run at the
            # sampled rate or it plays back at the wrong speed.
            out_fps = cfg.output.annotated_fps or (info.fps / cfg.source.frame_stride)
            writer = VideoWriter(run_dir.annotated_video, out_fps, info.width, info.height)

        tracks: dict[int, Track] = {}
        all_detections: list[Detection] = []
        all_plate_detections: list[PlateDetection] = []
        orphan_reads: list[PlateRead] = []
        labels: dict[int, TrackLabel] = {}
        reads_by_track: dict[int, int] = {}
        crops_saved: dict[int, int] = {}

        frames_read = 0
        frames_processed = 0
        started = time.perf_counter()
        decode_started = time.perf_counter()

        try:
            for frame in source:
                self.timer.add("decode", time.perf_counter() - decode_started)
                frames_read += 1

                # ── detect ──
                t0 = time.perf_counter()
                detections = self.detector.detect(frame.image, frame.index, frame.t_s)
                self.timer.add("detect", time.perf_counter() - t0)

                # ── track ──
                t0 = time.perf_counter()
                tracked = self._tracker.update(detections, frame.index, frame.t_s)
                self.timer.add("track", time.perf_counter() - t0)
                all_detections.extend(tracked)

                # Which tracks just had their best look at the vehicle. Only
                # those need their crop rewritten.
                best_now = {
                    d.track_id
                    for d in tracked
                    if self._record_observation(tracks, d) and d.track_id is not None
                }

                # ── plates ──
                frame_plates = self._find_plates(
                    frame.image, tracked, frame.index, frame.t_s, tracks
                )
                all_plate_detections.extend(frame_plates)

                for plate in frame_plates:
                    self._read_plate(
                        frame.image, plate, tracks, run_dir, reads_by_track,
                        crops_saved, labels, orphan_reads,
                    )

                # ── vehicle crops ──
                if cfg.output.save_vehicle_crops:
                    t0 = time.perf_counter()
                    self._save_vehicle_crops(frame.image, tracked, tracks, run_dir, best_now)
                    self.timer.add("crop_save_vehicle", time.perf_counter() - t0)

                # ── annotate ──
                if writer is not None:
                    t0 = time.perf_counter()
                    elapsed = max(1e-6, time.perf_counter() - started)
                    canvas = annotator.draw_frame(
                        frame.image, tracked, frame_plates, labels,
                        hud={
                            "frame": f"{frame.index}",
                            "time": f"{frame.t_s:6.2f}s",
                            "tracks": f"{len(tracked)} live / {len(tracks)} total",
                            "plates read": f"{sum(len(t.plate_reads) for t in tracks.values())}",
                            "proc fps": f"{frames_processed / elapsed:.1f}",
                        },
                    )
                    self.timer.add("annotate_draw", time.perf_counter() - t0)

                    t0 = time.perf_counter()
                    writer.write(canvas)
                    self.timer.add("video_encode", time.perf_counter() - t0)

                if cfg.output.keyframes and frame_plates:
                    t0 = time.perf_counter()
                    run_dir.keyframes_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(
                        str(run_dir.keyframes_dir / f"frame_{frame.index:06d}.jpg"), frame.image
                    )
                    self.timer.add("keyframe_save", time.perf_counter() - t0)

                frames_processed += 1
                if frames_processed % 100 == 0:
                    rate = frames_processed / max(1e-6, time.perf_counter() - started)
                    log.info(
                        "  %d frames · %d tracks · %d reads · %.1f fps",
                        frames_processed, len(tracks),
                        sum(len(t.plate_reads) for t in tracks.values()), rate,
                    )
                decode_started = time.perf_counter()
        finally:
            source.close()
            if writer is not None:
                writer.close()

        wall = time.perf_counter() - started

        # Convert to H.264 if we can, so the report embeds a playable video.
        annotated_codec = writer.codec if writer is not None else ""
        t0 = time.perf_counter()
        if (
            writer is not None
            and not writer.browser_playable
            and transcode_to_h264(run_dir.annotated_video)
        ):
            annotated_codec = "avc1"
            log.info("annotated video transcoded to H.264")
        self.timer.add("video_transcode", time.perf_counter() - t0)

        # ── final consensus per vehicle ──
        t0 = time.perf_counter()
        for track in tracks.values():
            track.result = consensus(track.plate_reads, cfg.consensus)
        self.timer.add("consensus_final", time.perf_counter() - t0)

        # ── group tracks into physical vehicles ──
        t0 = time.perf_counter()
        vehicles = merge(tracks, cfg)
        fragmentation = fragmentation_stats(tracks, vehicles)
        self.timer.add("track_merge", time.perf_counter() - t0)
        if fragmentation["fragments_absorbed"]:
            log.info(
                "merged %d tracker fragments into %d vehicles (%d raw tracks)",
                fragmentation["fragments_absorbed"], len(vehicles), len(tracks),
            )

        log.info(
            "done: %d frames in %.1fs (%.2f fps), %d tracks, %d plate reads, %d plates resolved",
            frames_processed, wall, frames_processed / max(1e-6, wall), len(tracks),
            sum(len(t.plate_reads) for t in tracks.values()),
            sum(1 for t in tracks.values() if t.result and t.result.text),
        )

        return RunResult(
            run_dir=run_dir,
            source=info,
            tracks=tracks,
            detections=all_detections,
            plate_detections=all_plate_detections,
            orphan_reads=orphan_reads,
            timer=self.timer,
            frames_read=frames_read,
            frames_processed=frames_processed,
            wall_seconds=wall,
            components=self.describe_components(),
            vehicles=vehicles,
            fragmentation=fragmentation,
            plate_ownership={
                "duplicate_regions_dropped": self._duplicate_plates,
                "reattributed_to_another_vehicle": self._reattributed_plates,
            },
            schedule_stats=self.scheduler.stats(),
            crop_gate_stats=self.crop_gate.stats(),
            annotated_path=run_dir.annotated_video if writer is not None else None,
            annotated_codec=annotated_codec,
        )

    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _record_observation(tracks: dict[int, Track], detection: Detection) -> bool:
        """Record one sighting. Returns True if it is this track's best so far."""
        track_id = detection.track_id
        if track_id is None:
            return False
        track = tracks.get(track_id)
        if track is None:
            track = Track(
                track_id=track_id, class_name=detection.class_name, class_id=detection.class_id
            )
            tracks[track_id] = track
        # A tracker can revise an object's class as it gets a better look;
        # the latest confident call is the one to keep.
        track.class_name = detection.class_name
        track.class_id = detection.class_id
        return track.observe(
            TrackObservation(
                frame_index=detection.frame_index,
                t_s=detection.t_s,
                bbox=detection.bbox,
                confidence=detection.confidence,
                detected=True,
            )
        )

    def _find_plates(
        self,
        image: np.ndarray,
        tracked: list[Detection],
        frame_index: int,
        t_s: float,
        tracks: dict[int, Track],
    ) -> list[PlateDetection]:
        cfg = self.config.plate
        height, width = image.shape[:2]
        found: list[PlateDetection] = []

        if cfg.mode == "full_frame":
            # One pass over the whole frame, then attribute each plate to
            # whichever tracked vehicle contains its centre. Catches plates on
            # vehicles the object detector missed entirely — those come back
            # with track_id None and are still reported.
            t0 = time.perf_counter()
            regions = self.plate_detector.detect(image)
            self.timer.add("plate_detect", time.perf_counter() - t0)
            for bbox, score in regions:
                owner = self._owning_track(bbox, tracked)
                found.append(
                    PlateDetection(
                        bbox=bbox, confidence=score, frame_index=frame_index, t_s=t_s,
                        track_id=owner, detector=self.plate_detector.engine_name,
                    )
                )
        else:
            for detection in tracked:
                if detection.class_name.lower() not in self.plate_bearing:
                    continue
                track_id = detection.track_id
                if track_id is None:
                    continue
                if frame_index % cfg.every_n_frames != 0:
                    continue

                # Selective inference: search this vehicle only when the view
                # has changed enough to yield a genuinely new observation.
                # Every skip is counted by reason and reported.
                if not self.scheduler.should_search(
                    track_id,
                    detection.bbox,
                    frame_index,
                    converged=self._is_converged(tracks.get(track_id), self.config),
                ):
                    continue

                t0 = time.perf_counter()
                region = detection.bbox.expand(cfg.vehicle_pad, width, height)
                x1, y1, x2, y2 = region.as_int()
                crop = image[y1:y2, x1:x2]
                self.timer.add("crop_extract_vehicle", time.perf_counter() - t0)
                if crop.size == 0:
                    continue

                # Timed per invocation, not per frame: the count is then the
                # number of times the model actually ran, which is the number
                # that matters when asking whether it runs too often.
                t0 = time.perf_counter()
                regions = self.plate_detector.detect(crop)
                self.timer.add("plate_detect", time.perf_counter() - t0)
                self.scheduler.record(track_id, detection.bbox, frame_index, bool(regions))

                for bbox, score in regions:
                    found.append(
                        PlateDetection(
                            bbox=bbox.shifted(x1, y1).clip(width, height),
                            confidence=score,
                            frame_index=frame_index,
                            t_s=t_s,
                            track_id=track_id,
                            bbox_in_vehicle=bbox,
                            detector=self.plate_detector.engine_name,
                        )
                    )

            found = self._resolve_plate_ownership(found, tracked)

        # Record the localisation on the vehicle, before anything tries to read
        # it. This is the earliest instant at which a plate can honestly be
        # drawn — the detector has found one and can say where — and it is
        # emphatically *not* the same fact as `Track.plate_detections`, which
        # holds only the detections that went on to produce an accepted read.
        # Keeping the two separate is what stops an overlay's needs from
        # quietly rewriting the accuracy statistics.
        #
        # Done here rather than in `_read_plate` because that is where the
        # information gets thrown away: `_read_plate` returns early on an
        # illegible crop, a redundant one, a conditioning failure and an OCR
        # result below the floors, and in every one of those cases the plate was
        # still found and its position is still the truth about this frame.
        for plate in found:
            if plate.track_id is None:
                continue
            track = tracks.get(plate.track_id)
            if track is not None:
                track.plate_location = plate
                # The vehicle box on this same frame, so the plate's position
                # *on the vehicle* is recoverable later. `_resolve_plate_
                # ownership` may have reattributed the plate to a different
                # track than the crop it was found in, so this is read from the
                # owning track rather than from the detection that produced it.
                track.plate_location_vehicle = (
                    track.observations[-1].bbox if track.observations else None
                )

        return found

    def _resolve_plate_ownership(
        self, plates: list[PlateDetection], tracked: list[Detection]
    ) -> list[PlateDetection]:
        """Deduplicate plates found in overlapping vehicle crops, and reattribute.

        Vehicle boxes overlap constantly in traffic, so searching inside vehicle
        A's crop routinely finds vehicle B's plate. Measured on a 240-frame clip:
        76 cases where two tracks were handed the *same plate pixels*, at IoU up
        to 0.91. Two things go wrong when that is left alone. The same crop is
        OCR'd once per overlapping vehicle, which is pure waste; and one physical
        plate ends up attached to several tracks, which is what made one car
        report as three vehicles.

        So each plate region is kept once, and attributed to the smallest
        tracked vehicle actually containing it — smallest because a car inside a
        bus's bounding box owns its own plate.
        """
        if len(plates) < 2:
            return plates

        # Highest confidence first, so the survivor of a duplicate group is the
        # detection we trust most.
        ordered = sorted(plates, key=lambda p: -p.confidence)
        kept: list[PlateDetection] = []
        for plate in ordered:
            if any(plate.bbox.iou(k.bbox) >= self.config.plate.duplicate_iou for k in kept):
                self._duplicate_plates += 1
                continue
            kept.append(plate)

        for plate in kept:
            owner = self._owning_track(plate.bbox, tracked)
            if owner is not None and owner != plate.track_id:
                self._reattributed_plates += 1
                plate.track_id = owner

        return kept

    @staticmethod
    def _owning_track(plate: BBox, tracked: list[Detection]) -> int | None:
        """Attribute a full-frame plate to the smallest vehicle containing it.

        Smallest, not first: a plate inside a car that is itself inside a bus's
        bounding box belongs to the car.
        """
        containing = [
            d for d in tracked
            if d.track_id is not None and d.bbox.contains_point(plate.cx, plate.cy)
        ]
        if not containing:
            return None
        return min(containing, key=lambda d: d.bbox.area).track_id

    def _read_plate(
        self,
        image: np.ndarray,
        plate: PlateDetection,
        tracks: dict[int, Track],
        run_dir: RunDirectory,
        reads_by_track: dict[int, int],
        crops_saved: dict[int, int],
        labels: dict[int, TrackLabel],
        orphan_reads: list[PlateRead],
    ) -> None:
        """Condition the crop, run OCR on each variant, keep the best read."""
        cfg = self.config
        track_id = plate.track_id
        key = track_id if track_id is not None else -1

        if reads_by_track.get(key, 0) >= cfg.ocr.max_reads_per_track:
            return

        # Early exit on a converged plate. This is the single largest saving in
        # the pipeline: OCR is ~70% of frame time, and a vehicle in view for 60
        # frames does not need 60 reads of a plate that eight frames already
        # agreed on. Turned off, a run costs several times more and reaches the
        # same answer.
        if track_id is not None and self._is_converged(tracks.get(track_id), cfg):
            return

        t0 = time.perf_counter()
        x1, y1, x2, y2 = plate.bbox.as_int()
        crop = image[y1:y2, x1:x2]
        self.timer.add("crop_extract_plate", time.perf_counter() - t0)
        if crop.size == 0:
            return

        t0 = time.perf_counter()
        quality = measure(crop)
        self.timer.add("quality_measure", time.perf_counter() - t0)
        if not is_legible(quality, min_width=cfg.ocr.min_crop_width):
            return

        # Is this crop new evidence, or one we have effectively read already?
        # The scheduler decided the vehicle was worth searching; this decides
        # whether what it found is worth reading. Costs microseconds against
        # roughly 75 ms for the OCR call it may avoid.
        crop_signature = None
        if track_id is not None:
            t0 = time.perf_counter()
            worth_reading, crop_signature = self.crop_gate.should_read(track_id, crop, quality)
            self.timer.add("crop_gate", time.perf_counter() - t0)
            if not worth_reading:
                return

        # ── conditioning ──
        t0 = time.perf_counter()
        try:
            variants = build_variants(crop, cfg.preprocess)
        except cv2.error as exc:
            log.debug("preprocessing failed on a crop: %s", exc)
            return
        self.timer.add("preprocess", time.perf_counter() - t0)
        if not variants:
            return

        # ── OCR every variant, keep the strongest ──
        best_read: PlateRead | None = None
        for variant_name, prepared in variants:
            t0 = time.perf_counter()
            result = self.ocr.read(prepared)
            self.timer.add("ocr", time.perf_counter() - t0)

            text = normalise(result.text)
            if not text or not (cfg.ocr.min_chars <= len(text) <= cfg.ocr.max_chars):
                continue
            if result.confidence < cfg.ocr.min_confidence:
                continue

            t0 = time.perf_counter()
            check = validate(text)
            variant_quality = measure_exposure(
                prepared, quality.width, quality.height, quality.sharpness
            )
            self.timer.add("quality_measure", time.perf_counter() - t0)
            read = PlateRead(
                text_raw=result.text,
                text=text,
                ocr_confidence=float(result.confidence),
                frame_index=plate.frame_index,
                t_s=plate.t_s,
                track_id=track_id,
                engine=self.ocr.engine_name,
                # Score the crop the detector produced, not the upscaled
                # variant: interpolation inflates every quality metric and
                # would make a blurry plate vote as though it were sharp.
                quality=variant_quality,
                char_confidences=list(result.char_confidences),
                grammar_valid=check.valid,
                grammar_note=check.note,
                plate_confidence=plate.confidence,
                preprocess_variant=variant_name,
                latency_ms=result.latency_ms,
            )
            # A grammar-valid read beats a higher-confidence invalid one: the
            # format constraint is stronger evidence than the engine's opinion.
            if best_read is None or _read_rank(read) > _read_rank(best_read):
                best_read = read

            if (
                cfg.preprocess.stop_on_good_read
                and read.grammar_valid
                and read.ocr_confidence >= cfg.preprocess.good_read_confidence
            ):
                # A clean, well-formed read. The remaining variants exist to
                # rescue crops this one failed on; there is nothing left to
                # rescue.
                break

        if track_id is not None:
            self.crop_gate.record(
                track_id,
                quality,
                best_read.ocr_confidence if best_read else 0.0,
                readable=best_read is not None,
                crop_signature=crop_signature,
            )

        if best_read is None:
            return

        # ── persist ──
        if cfg.output.save_plate_crops and crops_saved.get(key, 0) < cfg.output.max_crops_per_track:
            t0 = time.perf_counter()
            name = (
                f"track_{key:04d}_f{plate.frame_index:06d}"
                f"_{best_read.text}_{best_read.ocr_confidence:.2f}"
            )
            best_read.crop_path = run_dir.save_plate_crop(crop, name)
            plate.crop_path = best_read.crop_path
            crops_saved[key] = crops_saved.get(key, 0) + 1
            self.timer.add("crop_save_plate", time.perf_counter() - t0)

        reads_by_track[key] = reads_by_track.get(key, 0) + 1

        if track_id is None or track_id not in tracks:
            # No vehicle owns this plate. Keep the read — an unattributed plate
            # means the object detector missed a vehicle, which is exactly the
            # kind of gap this lab exists to surface.
            orphan_reads.append(best_read)
            return

        track = tracks[track_id]
        track.plate_reads.append(best_read)
        track.plate_detections.append(plate)

        # Refresh the running consensus so the annotated video shows the answer
        # converging as evidence accumulates, rather than the latest single read.
        # Timed separately from the final pass: this runs once per accepted read
        # and rebuilds the whole vote each time.
        t0 = time.perf_counter()
        current = consensus(track.plate_reads, cfg.consensus)
        self.timer.add("consensus_incremental", time.perf_counter() - t0)
        track.result = current
        # Keep the sharpest crop seen for this vehicle rather than the latest:
        # the inset should show the best evidence the pipeline had, which is
        # also the frame consensus weighted most heavily.
        previous = labels.get(track_id)
        inset = previous.crop if previous is not None else None
        if inset is None or best_read.quality.sharpness >= self._best_inset.get(track_id, 0.0):
            inset = crop.copy()
            self._best_inset[track_id] = best_read.quality.sharpness

        labels[track_id] = TrackLabel(
            text=current.text,
            confidence=current.confidence,
            reads=current.reads_total,
            uncertain=current.ambiguous or not current.grammar_valid,
            crop=inset,
        )

    def _save_vehicle_crops(
        self,
        image: np.ndarray,
        tracked: list[Detection],
        tracks: dict[int, Track],
        run_dir: RunDirectory,
        improved: set[int],
    ) -> None:
        """Keep the best-looking frame of each vehicle, replacing it as better ones arrive.

        Only tracks whose best sighting is *this* frame are rewritten, and that
        set is computed when the observation is recorded rather than by
        rescanning every track's history.
        """
        height, width = image.shape[:2]
        for detection in tracked:
            track_id = detection.track_id
            if track_id is None or track_id not in tracks or track_id not in improved:
                continue
            track = tracks[track_id]
            x1, y1, x2, y2 = detection.bbox.clip(width, height).as_int()
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            path = run_dir.save_vehicle_crop(crop, f"track_{track_id:04d}_best")
            if path:
                track.best_crop_path = path


def _read_rank(read: PlateRead) -> tuple[int, float]:
    """Order reads: grammar-valid first, then by weighted confidence."""
    return (1 if read.grammar_valid else 0, read.vote_weight)
