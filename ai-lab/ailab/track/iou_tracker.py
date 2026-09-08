"""Plain IoU tracker — the baseline ByteTrack has to beat.

No Kalman prediction, no second association pass, no recovery of low-confidence
detections. It exists so that ByteTrack's benefit on our actual footage is a
measured difference (ID switches, track fragmentation, plates per vehicle)
rather than an assumption inherited from a paper's benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ailab.config import TrackerConfig
from ailab.registry import register
from ailab.track.base import Tracker
from ailab.track.bytetrack import ious, linear_assignment
from ailab.types import BBox, Detection


@dataclass(slots=True)
class _Simple:
    track_id: int
    bbox: BBox
    score: float
    class_id: int
    class_name: str
    last_frame: int
    hits: int = 1
    misses: int = 0
    history: list[BBox] = field(default_factory=list)


@register("tracker", "iou")
class IouTracker(Tracker):
    def __init__(self, config: TrackerConfig, frame_rate: float = 30.0) -> None:
        self.config = config
        self.frame_rate = frame_rate
        self._tracks: list[_Simple] = []
        self._next_id = 0
        self.max_misses = max(1, int(frame_rate / 30.0 * config.track_buffer))

    def reset(self) -> None:
        self._tracks = []
        self._next_id = 0

    def update(self, detections: list[Detection], frame_index: int, t_s: float) -> list[Detection]:
        usable = [
            d
            for d in detections
            if d.confidence >= self.config.track_high_thresh and d.bbox.area >= self.config.min_box_area
        ]

        cost = 1.0 - ious(
            np.asarray([t.bbox.as_xyxy() for t in self._tracks]).reshape(-1, 4),
            np.asarray([d.bbox.as_xyxy() for d in usable]).reshape(-1, 4),
        )
        matches, unmatched_tracks, unmatched_dets = linear_assignment(
            cost, 1.0 - self.config.match_thresh
        )

        for ti, di in matches:
            track, det = self._tracks[ti], usable[di]
            track.bbox = det.bbox
            track.score = det.confidence
            track.class_id = det.class_id
            track.class_name = det.class_name
            track.last_frame = frame_index
            track.hits += 1
            track.misses = 0

        for ti in unmatched_tracks:
            self._tracks[ti].misses += 1

        for di in unmatched_dets:
            det = usable[di]
            if det.confidence < self.config.new_track_thresh:
                continue
            self._next_id += 1
            self._tracks.append(
                _Simple(
                    track_id=self._next_id,
                    bbox=det.bbox,
                    score=det.confidence,
                    class_id=det.class_id,
                    class_name=det.class_name,
                    last_frame=frame_index,
                )
            )

        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]

        return [
            Detection(
                bbox=t.bbox,
                confidence=t.score,
                class_id=t.class_id,
                class_name=t.class_name,
                frame_index=frame_index,
                t_s=t_s,
                track_id=t.track_id,
            )
            for t in self._tracks
            if t.misses == 0
        ]

    def describe(self) -> dict[str, Any]:
        return {"engine": "iou", "max_misses": self.max_misses, **self.config.model_dump()}


def build(config: TrackerConfig, frame_rate: float = 30.0) -> IouTracker:
    return IouTracker(config, frame_rate)
