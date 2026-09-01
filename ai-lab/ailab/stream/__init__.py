"""Live-stream inference: continuous processing and structured events."""

from ailab.stream.events import EventSink, SourceIdentity, vehicle_event
from ailab.stream.reader import CapturedFrame, StreamReader, StreamUnavailable
from ailab.stream.runner import StreamRunner

__all__ = [
    "CapturedFrame",
    "EventSink",
    "SourceIdentity",
    "StreamReader",
    "StreamRunner",
    "StreamUnavailable",
    "vehicle_event",
]
