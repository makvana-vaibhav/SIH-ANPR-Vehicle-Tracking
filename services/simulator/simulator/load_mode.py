"""Synthesises a statewide event rate, so the scaling claim has a number behind it.

The architecture's central argument is that the middle tier carries **events,
not video**: 80,000 cameras at one vehicle every 30 s is ~2,667 events/s of
metadata, against ~320 Gbps if the video itself were centralised. That
arithmetic is only worth stating if the platform can actually absorb 2,667
events/s, and until this existed nobody had checked.

What this does *not* do is run inference. It is the AI tier's **output** that
is being replayed, at a rate no laptop could produce honestly — a real worker
manages a few frames a second. Substituting a generator for the GPU farm is
the only way to test the platform tier on one machine, and the distinction is
stated in the report so nobody reads it as an ANPR benchmark.

Two properties matter for the events to be a fair test:

* **The payload is the real shape.** Same schema, same nesting, same candidate
  lists and per-read evidence — ~2 KB, because payload size is what the bus
  and the JSON parse actually cost. A stripped-down event would flatter the
  result.
* **The camera cardinality is real.** Events are spread over the full fleet,
  so the consumer's camera-code cache faces 80,000 distinct keys rather than
  the handful a single worker sees. Cache behaviour at that cardinality is
  part of what is being measured.

Plates are drawn from a pool that deliberately includes watchlist entries at a
realistic rate, so the alert path is exercised under load rather than bypassed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import time
from datetime import UTC, datetime
from typing import Any

import redis

# Gujarat RTO prefixes, so generated plates look like the real fleet.
RTO_CODES = (
    "GJ01", "GJ02", "GJ03", "GJ05", "GJ06", "GJ07", "GJ09",
    "GJ10", "GJ11", "GJ12", "GJ15", "GJ16", "GJ18", "GJ21", "GJ27",
)
VEHICLE_TYPES = ("car", "truck", "bus", "motorcycle", "auto")


def plate() -> str:
    """A grammar-valid Gujarat plate: GJ05 AB 1234."""
    return (
        random.choice(RTO_CODES)
        + "".join(random.choices(string.ascii_uppercase, k=2))
        + f"{random.randrange(10000):04d}"
    )


def build_event(camera_code: str, vehicle_id: int, plate_text: str) -> dict[str, Any]:
    """One `vehicle.completed` event, shaped exactly as the AI worker emits it.

    The evidence block is not padding. A real event carries its candidate list
    and per-frame reads so an operator can overrule the consensus, and that is
    most of its ~2 KB. Trimming it here would measure a payload the platform
    never actually receives.
    """
    confidence = round(random.uniform(0.62, 0.97), 4)
    now = datetime.now(UTC)
    x1, y1 = round(random.uniform(0, 1400), 2), round(random.uniform(0, 700), 2)
    w, h = round(random.uniform(120, 420), 2), round(random.uniform(110, 380), 2)

    def read(offset: int, text: str) -> dict[str, Any]:
        return {
            "frame_index": 500 + offset,
            "t_s": round(120.0 + offset * 0.7, 2),
            "text": text,
            "ocr_confidence": round(random.uniform(0.7, 0.99), 4),
            "plate_confidence": round(random.uniform(0.5, 0.95), 4),
            "grammar_valid": text == plate_text,
            "variant": "rectified",
            "crop": None,
        }

    near_miss = plate_text[:6] + f"{random.randrange(10000):04d}"
    return {
        "schema": "ailab.vehicle.event.v1",
        "event": "vehicle.completed",
        "event_time": now.isoformat(),
        "source": {"camera_id": camera_code, "name": camera_code},
        "frame": {"width": 1920, "height": 1080},
        "vehicle": {
            "vehicle_id": vehicle_id,
            "track_ids": [vehicle_id],
            "type": random.choice(VEHICLE_TYPES),
            "confidence": round(random.uniform(0.55, 0.95), 3),
            "bbox": {"x1": x1, "y1": y1, "x2": x1 + w, "y2": y1 + h, "w": w, "h": h},
            "first_seen_s": 120.0,
            "last_seen_s": 138.5,
            "duration_s": 18.5,
            "frames_tracked": random.randrange(8, 40),
            "merged_from_fragments": 1,
        },
        "plate": {
            "text": plate_text,
            "confidence": confidence,
            "readable": True,
            "grammar_valid": True,
            "grammar_note": "",
            "ambiguous": False,
            "corrected_from": None,
            "format": "in_bharat" if plate_text.startswith("GJ") else "unknown",
            "state": "GJ",
            "rto": plate_text[:4],
            "candidates": [
                {"text": plate_text, "score": confidence, "support": 4, "grammar_valid": True},
                {"text": near_miss, "score": round(confidence * 0.6, 4), "support": 1,
                 "grammar_valid": True},
            ],
            "evidence": {
                "reads_total": 5,
                "reads_agreeing": 4,
                "agreement": 0.8,
                "disagreement": 0.2,
                "method": "char_vote",
                "char_confidences": [round(random.uniform(0.6, 1.0), 4) for _ in plate_text],
            },
            "bbox": {"x1": x1 + 40, "y1": y1 + h - 60, "x2": x1 + 150, "y2": y1 + h - 30,
                     "w": 110.0, "h": 30.0},
            "detection_confidence": confidence,
        },
        "evidence": {
            "vehicle_crop": None,
            "plate_crop": None,
            "plate_crops": [],
            "reads": [read(i, plate_text if i % 4 else near_miss) for i in range(5)],
        },
        "latency_ms": round(random.uniform(180, 620), 1),
    }


class RateGenerator:
    """Emits events at a target rate, and reports honestly when it cannot.

    Written so the generator is not the thing being measured. Events go out in
    pipelined batches — one round trip per batch rather than per event, since
    at 2,667 events/s the round trips alone would dominate — and the loop
    paces itself against wall-clock elapsed time rather than sleeping a fixed
    interval, so a slow batch is caught up rather than compounding into drift.

    If the generator cannot reach the target rate it says so. A load test that
    silently under-delivers and then reports the platform "kept up" is worse
    than no load test.
    """

    def __init__(
        self,
        client: redis.Redis,
        stream_key: str,
        cameras: list[str],
        target_rate: float,
        watchlist_plates: list[str],
        watchlist_share: float = 0.001,
        batch_size: int = 250,
        maxlen: int = 200_000,
    ) -> None:
        self._client = client
        self._stream_key = stream_key
        self._cameras = cameras
        self._target = target_rate
        self._watchlist = watchlist_plates
        self._watchlist_share = watchlist_share
        self._batch = batch_size
        self._maxlen = maxlen
        self.emitted = 0
        self.watchlist_emitted = 0

    def _next_plate(self) -> str:
        # A small share of traffic is on the watchlist, so the alert path is
        # under load too. Real watchlist hit rates are far below this; the
        # point is to exercise the path, not to model it.
        if self._watchlist and random.random() < self._watchlist_share:
            self.watchlist_emitted += 1
            return random.choice(self._watchlist)
        return plate()

    def _emit_batch(self, count: int) -> None:
        pipe = self._client.pipeline(transaction=False)
        for _ in range(count):
            event = build_event(
                camera_code=random.choice(self._cameras),
                vehicle_id=random.randrange(1, 100_000),
                plate_text=self._next_plate(),
            )
            pipe.xadd(
                self._stream_key,
                {"payload": json.dumps(event)},
                maxlen=self._maxlen,
                approximate=True,
            )
        pipe.execute()
        self.emitted += count

    def run(self, duration_s: float, on_sample: Any = None) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + duration_s
        last_report = started

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - started
            # How many events *should* have gone out by now. Pacing against
            # this rather than against a sleep interval means a slow batch is
            # made up on the next one instead of permanently losing rate.
            owed = int(self._target * elapsed) - self.emitted
            if owed >= self._batch:
                self._emit_batch(min(owed, self._batch * 4))
            elif owed > 0:
                self._emit_batch(owed)
            else:
                time.sleep(0.002)

            now = time.monotonic()
            if on_sample is not None and now - last_report >= 1.0:
                on_sample(self.emitted, now - started)
                last_report = now

        elapsed = time.monotonic() - started
        achieved = self.emitted / elapsed if elapsed else 0.0
        return {
            "emitted": self.emitted,
            "elapsed_s": round(elapsed, 2),
            "target_rate": self._target,
            "achieved_rate": round(achieved, 1),
            # Stated rather than asserted: if the generator fell short, the
            # platform was never asked the question the report claims it was.
            "generator_kept_up": achieved >= self._target * 0.95,
            "watchlist_emitted": self.watchlist_emitted,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Statewide event-rate generator")
    parser.add_argument("--cameras", type=int, default=80_000,
                        help="distinct camera identities to spread events across")
    parser.add_argument("--rate", type=float, default=2667.0,
                        help="target events/s (80,000 cameras / 30 s = 2667)")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds to sustain")
    parser.add_argument("--prefix", default="CAM-LOAD", help="camera code prefix")
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://redis:6379/0"))
    parser.add_argument("--stream", default=os.getenv("EVENT_STREAM_KEY",
                                                      "sentinel:events:detections"))
    parser.add_argument("--watchlist", default="", help="comma-separated plates to inject")
    args = parser.parse_args()

    cameras = [f"{args.prefix}-{i:06d}" for i in range(args.cameras)]
    watchlist = [p.strip().upper() for p in args.watchlist.split(",") if p.strip()]
    client = redis.Redis.from_url(args.redis_url, decode_responses=True)

    generator = RateGenerator(client, args.stream, cameras, args.rate, watchlist)
    result = generator.run(args.duration)
    print(json.dumps(result))
    return 0 if result["generator_kept_up"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
