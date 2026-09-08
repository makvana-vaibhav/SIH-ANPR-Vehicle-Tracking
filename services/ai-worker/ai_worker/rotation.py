"""Deciding which cameras get the inference slots, and for how long.

## The problem this solves

A worker can analyse roughly as many cameras at once as it has cores to spare —
measured at 4.4 fps per 720p stream on this hardware (`ai-lab/PERFORMANCE.md`),
which is about one camera per core. A fleet has far more cameras than that.

The obvious answer, and the one this replaces, is to take the first N cameras
and ignore the rest. That is worse than it looks: the ignored cameras are
ignored *permanently*, so a vehicle passing camera 20 is never seen, and the
system quietly under-reports rather than visibly falling behind.

## The answer

Rotate. Each camera holds a slot for a bounded slice, then yields it to the
next camera waiting. Every camera is covered every `sweep_seconds`, and no
camera is starved. Coverage is partial in time rather than partial in space,
and — unlike a truncated list — the gap is a number you can state: with S slots
and N cameras at T seconds each, a camera is watched T seconds in every
`N/S * T`.

This is also what the real deployment does. At 80,000 cameras nobody buys
80,000 GPU pipelines; they buy enough for the coverage the policy requires and
rotate. Building it now means the scaling story is the same code, not a
different one.

## Pinning

Some cameras should never yield: the one an operator is watching, a junction
under active investigation, the demonstration feed. Those are pinned and hold a
slot permanently. Pinning more cameras than there are slots is a configuration
error and is reported as one, because the alternative is silently starving
every rotating camera.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ai_worker.discovery import CameraStream

log = logging.getLogger(__name__)


@dataclass
class Rotation:
    """Which cameras hold the inference slots right now.

    Deliberately has no threads and no I/O: it is a scheduling decision, and
    keeping it pure means it can be tested without a camera, a network or a
    model.
    """

    slots: int
    slice_seconds: float
    pinned: frozenset[str] = frozenset()

    #: Every camera this worker owns, whether or not it currently holds a slot.
    assigned: dict[str, CameraStream] = field(default_factory=dict)
    #: When each currently-running camera took its slot.
    started_at: dict[str, float] = field(default_factory=dict)
    #: How many slices each camera has had, so rotation stays fair after
    #: cameras are added, removed, or crash and restart.
    served: dict[str, int] = field(default_factory=dict)

    def update_assignment(self, streams: list[CameraStream]) -> None:
        """Replace what this worker owns, keeping rotation state for survivors."""
        self.assigned = {s.camera_code: s for s in streams}
        for code in list(self.started_at):
            if code not in self.assigned:
                del self.started_at[code]
        for code in list(self.served):
            if code not in self.assigned:
                del self.served[code]

        over_pinned = [c for c in self.pinned if c in self.assigned]
        if len(over_pinned) > self.slots:
            log.error(
                "%d cameras are pinned but there are only %d slots; "
                "rotating cameras will never run. Unpin some, or add workers.",
                len(over_pinned),
                self.slots,
            )

    def expired(self, now: float | None = None) -> list[str]:
        """Running cameras whose slice is up and which should yield.

        A camera only yields if somebody is actually waiting. Rotating a camera
        out to replace it with nothing would cost a reconnect and buy nothing.
        """
        if self.slice_seconds <= 0:
            return []
        now = time.monotonic() if now is None else now

        waiting = [
            code
            for code in self.assigned
            if code not in self.started_at and code not in self.pinned
        ]
        if not waiting:
            return []

        candidates = [
            (started, code)
            for code, started in self.started_at.items()
            if code not in self.pinned and now - started >= self.slice_seconds
        ]
        candidates.sort()
        # Yield no more slots than there are cameras to fill them, oldest
        # first, so the longest-running camera is the first to step aside.
        return [code for _started, code in candidates[: len(waiting)]]

    def next_up(self) -> list[CameraStream]:
        """Cameras that should be started now, to fill free slots.

        Pinned cameras first — they are pinned because they matter — then the
        cameras that have had the fewest slices, so a fleet larger than the
        slot count is swept evenly rather than favouring whichever codes sort
        first.
        """
        free = self.slots - len(self.started_at)
        if free <= 0:
            return []

        idle = [
            stream
            for code, stream in self.assigned.items()
            if code not in self.started_at
        ]
        idle.sort(
            key=lambda s: (
                0 if s.camera_code in self.pinned else 1,
                self.served.get(s.camera_code, 0),
                s.camera_code,
            )
        )
        return idle[:free]

    def take_slot(self, code: str, now: float | None = None) -> None:
        self.started_at[code] = time.monotonic() if now is None else now
        self.served[code] = self.served.get(code, 0) + 1

    def release_slot(self, code: str) -> None:
        self.started_at.pop(code, None)

    def slice_for(self, code: str) -> float | None:
        """How long this camera may hold its slot. None means indefinitely."""
        if code in self.pinned or self.slice_seconds <= 0:
            return None
        # Rotation is pointless when the whole fleet fits: cycling a camera out
        # only to start it again costs a reconnect and loses the vehicles in
        # flight during it.
        if len(self.assigned) <= self.slots:
            return None
        return self.slice_seconds

    @property
    def sweep_seconds(self) -> float:
        """How long before every assigned camera has had a slot.

        The honest coverage number, and the one worth logging: it is what an
        operator needs to answer "could we have missed a vehicle?".
        """
        rotating = len([c for c in self.assigned if c not in self.pinned])
        rotating_slots = max(1, self.slots - len(self.pinned & set(self.assigned)))
        if rotating <= rotating_slots or self.slice_seconds <= 0:
            return 0.0
        return (rotating / rotating_slots) * self.slice_seconds
