"""ByteTrack — association by IoU in two passes.

The insight ByteTrack is built on: low-confidence detections are usually real
objects that are occluded or blurred, not noise. Throwing them away is what
causes ID switches when a car passes behind a pole. So detections are split by
score — high-scoring boxes claim tracks first, then the leftovers get a second
pass against tracks that are still unmatched. A vehicle that dims to 0.2
confidence for four frames keeps its identity instead of becoming a new vehicle,
which matters here because a new ID means a second, fragmentary plate result for
one car.

Pure NumPy + SciPy; no torch. Reimplemented from the published algorithm
(Zhang et al., 2022).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ailab.config import TrackerConfig
from ailab.registry import register
from ailab.track.base import Tracker
from ailab.track.kalman import KalmanFilter
from ailab.types import BBox, Detection


class TrackState(IntEnum):
    NEW = 0
    TRACKED = 1
    LOST = 2
    REMOVED = 3


def ious(atlbrs: np.ndarray, btlbrs: np.ndarray) -> np.ndarray:
    """Pairwise IoU matrix between two sets of xyxy boxes."""
    if atlbrs.size == 0 or btlbrs.size == 0:
        return np.zeros((len(atlbrs), len(btlbrs)), dtype=np.float32)

    area_a = np.prod(np.maximum(0.0, atlbrs[:, 2:] - atlbrs[:, :2]), axis=1)
    area_b = np.prod(np.maximum(0.0, btlbrs[:, 2:] - btlbrs[:, :2]), axis=1)

    lt = np.maximum(atlbrs[:, None, :2], btlbrs[None, :, :2])
    rb = np.minimum(atlbrs[:, None, 2:], btlbrs[None, :, 2:])
    wh = np.maximum(0.0, rb - lt)
    inter = wh[..., 0] * wh[..., 1]

    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(union > 0, inter / union, 0.0)
    return out.astype(np.float32)


def linear_assignment(
    cost_matrix: np.ndarray, threshold: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Hungarian assignment, rejecting pairs above the cost threshold."""
    if cost_matrix.size == 0:
        return [], list(range(cost_matrix.shape[0])), list(range(cost_matrix.shape[1]))

    matches: list[tuple[int, int]] = []
    rows, cols = linear_sum_assignment(cost_matrix)
    matched_rows, matched_cols = set(), set()
    for r, c in zip(rows, cols, strict=False):
        if cost_matrix[r, c] <= threshold:
            matches.append((int(r), int(c)))
            matched_rows.add(int(r))
            matched_cols.add(int(c))

    unmatched_a = [i for i in range(cost_matrix.shape[0]) if i not in matched_rows]
    unmatched_b = [i for i in range(cost_matrix.shape[1]) if i not in matched_cols]
    return matches, unmatched_a, unmatched_b


class STrack:
    """One tracked object's Kalman state and bookkeeping."""

    shared_kalman = KalmanFilter()
    __slots__ = (
        "_tlwh",
        "class_id",
        "class_name",
        "covariance",
        "frame_id",
        "is_activated",
        "mean",
        "score",
        "start_frame",
        "state",
        "track_id",
        "tracklet_len",
    )

    def __init__(self, tlwh: np.ndarray, score: float, class_id: int, class_name: str) -> None:
        self._tlwh = np.asarray(tlwh, dtype=np.float64)
        self.mean: np.ndarray | None = None
        self.covariance: np.ndarray | None = None
        self.track_id = 0
        self.state = TrackState.NEW
        self.is_activated = False
        self.score = score
        self.class_id = class_id
        self.class_name = class_name
        self.start_frame = 0
        self.frame_id = 0
        self.tracklet_len = 0

    # ── geometry conversions ──
    @staticmethod
    def tlwh_to_xyah(tlwh: np.ndarray) -> np.ndarray:
        ret = np.asarray(tlwh, dtype=np.float64).copy()
        ret[:2] += ret[2:] / 2.0     # top-left → centre
        ret[2] /= ret[3]             # width → aspect
        return ret

    @property
    def tlwh(self) -> np.ndarray:
        if self.mean is None:
            return self._tlwh.copy()
        ret = self.mean[:4].copy()
        ret[2] *= ret[3]             # aspect → width
        ret[:2] -= ret[2:] / 2.0     # centre → top-left
        return ret

    @property
    def tlbr(self) -> np.ndarray:
        ret = self.tlwh.copy()
        ret[2:] += ret[:2]
        return ret

    @property
    def end_frame(self) -> int:
        return self.frame_id

    # ── lifecycle ──
    def activate(
        self, kalman_filter: KalmanFilter, frame_id: int, track_id: int, trusted: bool = False
    ) -> None:
        self.track_id = track_id
        self.mean, self.covariance = kalman_filter.initiate(self.tlwh_to_xyah(self._tlwh))
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        # The very first frame of a track is trusted immediately; later ones
        # must survive a second association before being reported.
        self.is_activated = trusted
        self.frame_id = frame_id
        self.start_frame = frame_id

    def re_activate(
        self, new_track: STrack, kalman_filter: KalmanFilter, frame_id: int, new_id: bool = False
    ) -> None:
        assert self.mean is not None and self.covariance is not None
        self.mean, self.covariance = kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh)
        )
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.frame_id = frame_id
        self.score = new_track.score
        self.class_id = new_track.class_id
        self.class_name = new_track.class_name

    def update(self, new_track: STrack, kalman_filter: KalmanFilter, frame_id: int) -> None:
        assert self.mean is not None and self.covariance is not None
        self.frame_id = frame_id
        self.tracklet_len += 1
        self.mean, self.covariance = kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh)
        )
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.score = new_track.score
        self.class_id = new_track.class_id
        self.class_name = new_track.class_name

    def mark_lost(self) -> None:
        self.state = TrackState.LOST

    def mark_removed(self) -> None:
        self.state = TrackState.REMOVED

    @staticmethod
    def multi_predict(tracks: list[STrack]) -> None:
        # Filter first and iterate the filtered list throughout: building the
        # arrays from a subset while indexing them by the full list's positions
        # would misalign every state without raising anything.
        live = [t for t in tracks if t.mean is not None and t.covariance is not None]
        if not live:
            return

        means = np.asarray([t.mean.copy() for t in live])
        covs = np.asarray([t.covariance for t in live])
        for i, track in enumerate(live):
            if track.state != TrackState.TRACKED:
                # A lost track is not accelerating; freeze its height velocity
                # so its box does not drift while unobserved.
                means[i][7] = 0

        means, covs = STrack.shared_kalman.multi_predict(means, covs)
        for track, mean, cov in zip(live, means, covs, strict=False):
            track.mean, track.covariance = mean, cov


@register("tracker", "bytetrack")
class ByteTracker(Tracker):
    def __init__(self, config: TrackerConfig, frame_rate: float = 30.0) -> None:
        self.config = config
        self.tracked: list[STrack] = []
        self.lost: list[STrack] = []
        self.removed: list[STrack] = []
        self.frame_id = 0
        self._next_id = 0
        self.kalman_filter = KalmanFilter()
        self.frame_rate = frame_rate
        # track_buffer is expressed in frames at 30 fps; scale it so a config
        # behaves the same on 25 fps CCTV as on a 30 fps clip.
        self.max_time_lost = max(1, int(frame_rate / 30.0 * config.track_buffer))
        # Source time of the last update, so the frame clock can advance by
        # however many source frames actually elapsed. See update().
        self._last_t_s: float | None = None
        self._updates = 0

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def reset(self) -> None:
        self.tracked, self.lost, self.removed = [], [], []
        self._last_t_s = None
        self._updates = 0
        self.frame_id = 0
        self._next_id = 0

    def update(self, detections: list[Detection], frame_index: int, t_s: float) -> list[Detection]:
        # Advance the clock by the source frames that went by, not by one.
        #
        # `max_time_lost` is a number of *source* frames — a second of lost
        # track at 30 fps. A live worker that cannot keep up analyses a
        # fraction of the frames it decodes (measured: one in nine), and if
        # the clock ticked once per analysed frame that one second became nine
        # of wall time. Nine seconds is long enough at a junction for the car
        # that was lost to leave and a different car to stop in the same spot,
        # which the tracker then matched to the old identity — and the old
        # plate rode along on the new car. Counting elapsed source frames keeps
        # the buffer meaning what the config says, whatever the analysis rate.
        #
        # A caller that passes a constant `t_s` (the batch tests do) gets the
        # old one-per-update behaviour, because the elapsed count floors at 1.
        self._updates += 1
        if self._last_t_s is None:
            self.frame_id += 1
        else:
            elapsed = int(round((t_s - self._last_t_s) * self.frame_rate))
            self.frame_id += max(1, elapsed)
        self._last_t_s = t_s
        cfg = self.config

        candidates = [
            d for d in detections if d.bbox.area >= cfg.min_box_area
        ]
        scores = np.asarray([d.confidence for d in candidates], dtype=np.float32)

        high_mask = scores >= cfg.track_high_thresh
        low_mask = (scores >= cfg.track_low_thresh) & ~high_mask

        def to_stracks(mask: np.ndarray) -> list[STrack]:
            out = []
            for det, keep in zip(candidates, mask, strict=False):
                if not keep:
                    continue
                x1, y1, x2, y2 = det.bbox.as_xyxy()
                out.append(
                    STrack(
                        np.array([x1, y1, x2 - x1, y2 - y1]),
                        det.confidence,
                        det.class_id,
                        det.class_name,
                    )
                )
            return out

        detections_high = to_stracks(high_mask) if len(candidates) else []
        detections_low = to_stracks(low_mask) if len(candidates) else []

        # Split confirmed tracks from ones still awaiting a second sighting.
        unconfirmed = [t for t in self.tracked if not t.is_activated]
        tracked = [t for t in self.tracked if t.is_activated]

        # ── Pass 1: confirmed + lost tracks against high-score detections ──
        pool = _join(tracked, self.lost)
        STrack.multi_predict(pool)
        dists = 1.0 - ious(
            np.asarray([t.tlbr for t in pool]).reshape(-1, 4),
            np.asarray([d.tlbr for d in detections_high]).reshape(-1, 4),
        )
        # match_thresh is a threshold on IoU *distance* (1 - IoU), matching the
        # published algorithm: 0.8 admits any pair overlapping by 20% or more.
        # Treating it as a floor on IoU instead demands 80% overlap, which
        # breaks the track of anything actually moving.
        matches, u_track, u_detection = linear_assignment(dists, cfg.match_thresh)

        activated: list[STrack] = []
        refound: list[STrack] = []
        for itracked, idet in matches:
            track, det = pool[itracked], detections_high[idet]
            if track.state == TrackState.TRACKED:
                track.update(det, self.kalman_filter, self.frame_id)
                activated.append(track)
            else:
                track.re_activate(det, self.kalman_filter, self.frame_id)
                refound.append(track)

        # ── Pass 2: still-unmatched tracks against low-score detections ──
        # This is the part that survives occlusion and motion blur.
        remaining = [pool[i] for i in u_track if pool[i].state == TrackState.TRACKED]
        dists = 1.0 - ious(
            np.asarray([t.tlbr for t in remaining]).reshape(-1, 4),
            np.asarray([d.tlbr for d in detections_low]).reshape(-1, 4),
        )
        matches, u_track_second, _ = linear_assignment(dists, 0.5)
        for itracked, idet in matches:
            track, det = remaining[itracked], detections_low[idet]
            if track.state == TrackState.TRACKED:
                track.update(det, self.kalman_filter, self.frame_id)
                activated.append(track)
            else:
                track.re_activate(det, self.kalman_filter, self.frame_id)
                refound.append(track)

        lost: list[STrack] = []
        for i in u_track_second:
            track = remaining[i]
            if track.state != TrackState.LOST:
                track.mark_lost()
                lost.append(track)

        # ── Unconfirmed tracks: one more chance at a high-score detection ──
        leftovers = [detections_high[i] for i in u_detection]
        dists = 1.0 - ious(
            np.asarray([t.tlbr for t in unconfirmed]).reshape(-1, 4),
            np.asarray([d.tlbr for d in leftovers]).reshape(-1, 4),
        )
        matches, u_unconfirmed, u_detection2 = linear_assignment(dists, 0.7)
        for itracked, idet in matches:
            unconfirmed[itracked].update(leftovers[idet], self.kalman_filter, self.frame_id)
            activated.append(unconfirmed[itracked])

        removed: list[STrack] = []
        for i in u_unconfirmed:
            unconfirmed[i].mark_removed()
            removed.append(unconfirmed[i])

        # ── Start new tracks from confident leftovers ──
        for i in u_detection2:
            det = leftovers[i]
            if det.score < cfg.new_track_thresh:
                continue
            det.activate(
                self.kalman_filter, self.frame_id, self._new_id(), trusted=self._updates == 1
            )
            activated.append(det)

        # ── Retire tracks lost for too long ──
        for track in self.lost:
            if self.frame_id - track.end_frame > self.max_time_lost:
                track.mark_removed()
                removed.append(track)

        self.tracked = _join(
            [t for t in self.tracked if t.state == TrackState.TRACKED], activated
        )
        self.tracked = _join(self.tracked, refound)
        # Tracks retired *this* update are subtracted too. Upstream ByteTrack
        # extends `removed` only after this line, so a track marked removed
        # stayed matchable for one more update — a one-frame slip when every
        # frame is analysed, and a whole second when the clock above advances
        # by the frames that were skipped.
        self.removed.extend(removed)
        self.lost = _subtract(_join(_subtract(self.lost, self.tracked), lost), self.removed)
        self.tracked, self.lost = _remove_duplicates(self.tracked, self.lost)

        return [
            Detection(
                bbox=BBox(*(float(v) for v in track.tlbr)),
                confidence=float(track.score),
                class_id=track.class_id,
                class_name=track.class_name,
                frame_index=frame_index,
                t_s=t_s,
                track_id=track.track_id,
            )
            for track in self.tracked
            if track.is_activated
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "bytetrack",
            "max_time_lost_frames": self.max_time_lost,
            **self.config.model_dump(),
        }


# ── small list helpers, kept out of the algorithm for readability ──
def _join(a: list[STrack], b: list[STrack]) -> list[STrack]:
    seen = {t.track_id for t in a}
    return a + [t for t in b if t.track_id not in seen]


def _subtract(a: list[STrack], b: list[STrack]) -> list[STrack]:
    drop = {t.track_id for t in b}
    return [t for t in a if t.track_id not in drop]


def _remove_duplicates(
    a: list[STrack], b: list[STrack]
) -> tuple[list[STrack], list[STrack]]:
    """Drop near-identical tracks that appear in both pools.

    When two tracks converge onto the same object the younger one is the
    mistake, so the longer-lived track keeps the identity.
    """
    pairs = np.where(
        1.0
        - ious(
            np.asarray([t.tlbr for t in a]).reshape(-1, 4),
            np.asarray([t.tlbr for t in b]).reshape(-1, 4),
        )
        < 0.15
    )
    dup_a, dup_b = set(), set()
    for p, q in zip(*pairs, strict=False):
        age_a = a[p].frame_id - a[p].start_frame
        age_b = b[q].frame_id - b[q].start_frame
        if age_a > age_b:
            dup_b.add(q)
        else:
            dup_a.add(p)
    return (
        [t for i, t in enumerate(a) if i not in dup_a],
        [t for i, t in enumerate(b) if i not in dup_b],
    )


def build(config: TrackerConfig, frame_rate: float = 30.0) -> ByteTracker:
    return ByteTracker(config, frame_rate)
