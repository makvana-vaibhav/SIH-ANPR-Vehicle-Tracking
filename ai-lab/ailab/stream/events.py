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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ailab.aggregate.grammar import describe_plate
from ailab.track.merge import Vehicle
from ailab.types import Track

SCHEMA_VERSION = "ailab.vehicle.event.v1"


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


def vehicle_event(
    vehicle: Vehicle,
    source: SourceIdentity,
    kind: str = "vehicle.completed",
    latency_ms: float | None = None,
    run_id: str = "",
    frame_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """One vehicle, shaped for the platform.

    `frame_size` is (width, height) of the frame the boxes were measured in.
    Every bbox in this payload is in source-frame pixels, and a consumer that
    draws them — an overlay on a video element, say — cannot scale them without
    knowing that space. Omitting it makes the coordinates unusable.
    """
    best_read = max(vehicle.reads, key=lambda r: r.vote_weight, default=None)
    last_detection = vehicle.plate_detections[-1] if vehicle.plate_detections else None

    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "event": kind,
        "event_time": datetime.now(UTC).isoformat(),
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
            "first_seen_s": round(vehicle.first_seen_s, 3),
            "last_seen_s": round(vehicle.last_seen_s, 3),
            "duration_s": round(vehicle.duration_s, 3),
            "frames_tracked": vehicle.frames_tracked,
            "merged_from_fragments": vehicle.merged_from_fragments,
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
