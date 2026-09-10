"""Evidence crop upload, from the edge.

Two properties matter more than the happy path.

A crop must never cost a detection. The upload runs on a background thread
precisely so it cannot add network latency to the inference loop, and every
failure mode — an unencodable image, a full queue, object storage being down —
has to degrade to "no crop" rather than to a lost sighting or a stalled worker.

And a key must never be returned for an object that will not exist. The key is
written into `Detection.crop_key`, so handing one back for a crop that was
dropped would put a dangling reference in the database and show an operator a
permanently broken image.
"""

from __future__ import annotations

import queue
from datetime import UTC, datetime

import numpy as np
import pytest

from ai_worker.crops import MinioCropStore, object_key


class TestObjectKey:
    def test_it_is_partitioned_by_day(self) -> None:
        """A retention sweep has to be able to drop a day with a prefix delete.

        Walking every object to find yesterday's would make retention on
        evidence — a legal obligation, not housekeeping — too expensive to run.
        """
        when = datetime(2026, 9, 10, 4, 30, tzinfo=UTC)
        assert object_key("plates", "track_1_AB12CDE", when) == (
            "crops/plates/2026/09/10/track_1_AB12CDE.jpg"
        )

    def test_plates_and_vehicles_are_separated(self) -> None:
        when = datetime(2026, 9, 10, tzinfo=UTC)
        assert object_key("plates", "x", when).startswith("crops/plates/")
        assert object_key("vehicles", "x", when).startswith("crops/vehicles/")

    def test_it_is_a_jpeg(self) -> None:
        assert object_key("plates", "x", datetime(2026, 1, 1, tzinfo=UTC)).endswith(
            ".jpg"
        )


#: Stands in for cv2.imencode.
#:
#: The real encoder needs OpenCV, which the shared test image does not carry —
#: and the worker image, which does, carries no pytest. Tests written against
#: the real encoder would therefore have run in neither image. Injecting it
#: leaves the behaviour that actually matters testable everywhere: the key
#: format, the bounded queue, and never handing back a key for a crop that was
#: dropped.
def fake_encoder(image: np.ndarray) -> bytes | None:
    if image is None or getattr(image, "size", 0) == 0:
        return None
    return b"\xff\xd8" + b"jpeg-bytes"


@pytest.fixture
def store() -> MinioCropStore:
    """A store whose upload threads are never given anything to do.

    The queue is emptied by hand in the tests that care, so nothing here
    attempts to reach object storage.
    """
    return MinioCropStore(encoder=fake_encoder)


class TestSavingCrops:
    def test_an_empty_image_yields_no_key(self, store: MinioCropStore) -> None:
        """Nothing was stored, so nothing may be referenced."""
        assert (
            store.save_plate_crop(np.zeros((0, 0, 3), dtype=np.uint8), "empty") is None
        )
        assert store.save_plate_crop(None, "none") is None  # type: ignore[arg-type]

    def test_a_real_crop_returns_a_key_immediately(self, store: MinioCropStore) -> None:
        """The key is knowable before the bytes land.

        That is what lets the upload be asynchronous: the inference loop gets
        its answer at once and never waits on the network.
        """
        image = np.full((28, 102, 3), 200, dtype=np.uint8)
        key = store.save_plate_crop(image, "track_0001_AB12CDE_0.91")

        assert key is not None
        assert key.startswith("crops/plates/")
        assert key.endswith("track_0001_AB12CDE_0.91.jpg")

    def test_the_bytes_are_queued_as_a_jpeg(self, store: MinioCropStore) -> None:
        image = np.full((28, 102, 3), 128, dtype=np.uint8)
        store.save_plate_crop(image, "queued")

        key, payload = store._queue.get_nowait()
        assert key.endswith("queued.jpg")
        # JPEG magic. Encoding happens on the calling thread, so a crop that
        # cannot be encoded is discovered immediately rather than by a thread
        # that has already returned a key for it.
        assert payload[:2] == b"\xff\xd8"

    def test_a_full_queue_drops_the_crop_and_returns_no_key(self) -> None:
        """Bounded on purpose.

        If object storage is slow, an unbounded queue grows until the worker is
        OOM-killed — which has already happened to this worker once, and takes
        every camera down with it. Losing evidence crops is recoverable; losing
        the worker is not. Crucially the caller gets None, so no dangling key
        reaches the database.
        """
        # No drain threads, so what goes in stays in.
        blocked = MinioCropStore(encoder=fake_encoder, threads=0)
        while True:
            try:
                blocked._queue.put_nowait(("filler", b"x"))
            except queue.Full:
                break

        image = np.full((28, 102, 3), 90, dtype=np.uint8)
        assert blocked.save_plate_crop(image, "dropped") is None
        assert blocked.stats()["dropped"] >= 1
