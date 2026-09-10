"""Uploads evidence crops to object storage, from the edge.

## Why the worker uploads, and not the consumer

The alternative is to put the JPEG bytes on the event bus and let the API tier
write them to MinIO. That would break the one architectural claim this whole
system rests on. A detection event is ~2 KB; a 15 KB plate crop base64-encoded
is ~20 KB, so carrying crops on the bus multiplies event traffic roughly
tenfold — and "the central tier carries events, not video" stops being true.

So crops go from the edge straight to object storage, and only the **object
key** travels on the bus. That is the same shape as the video path: bytes go
sideways, metadata goes to the centre.

## Why it duck-types the lab's run directory

`ailab.stream.StreamRunner` writes crops through whatever object it is given as
`run_dir`, calling `save_plate_crop(image, name)` and expecting a string back.
The lab passes a `RunDirectory` that writes JPEGs to disk; the worker previously
passed nothing, so the runner substituted `_NullRunDir` and **every crop was
silently discarded** — which is why `Detection.crop_key` had never been anything
but NULL.

Implementing the same two methods means the lab needs no changes at all, and the
lab keeps writing to disk where that is what you want (a diagnostic run you are
going to look through by hand).

## Why the upload is asynchronous but the key is not

`save_plate_crop` is called from inside the inference loop. A blocking S3 PUT
there would add network latency to every frame that resolved a plate, on a
worker already measured at 855% CPU. So the key is computed and returned
immediately — it is derived from the camera, the date and the crop name, so it
is knowable before the bytes land — and the upload happens on a small background
thread pool.

The consequence is honest and worth stating: for a second or so after a
detection, its `crop_key` names an object that does not exist yet, and if the
upload fails it never will. The API serves crops through a presigned URL and a
missing object is a 404, which the UI shows as "no crop" rather than a broken
image. A crop is evidence *about* a detection, not the detection itself, and
losing one must not lose the sighting.
"""

from __future__ import annotations

import contextlib
import io
import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ai_worker.config import settings

log = logging.getLogger("ai_worker.crops")

#: JPEG quality. 95 because these crops are evidence and may become training
#: data for a fine-tune, so compression artefacts would be baked in. Matches
#: what the lab's RunDirectory uses, deliberately.
JPEG_QUALITY = 95

#: Uploads waiting for a worker thread. Bounded: if object storage is slow or
#: down, the queue fills and further crops are dropped with a warning rather
#: than growing until the worker runs out of memory. Losing evidence crops is
#: recoverable; an OOM-killed worker stops all detection, which is not — and
#: this worker has already been OOM-killed once.
QUEUE_SIZE = 256

#: Threads draining that queue. Two is enough for the observed crop rate and
#: keeps the worker's footprint predictable.
UPLOAD_THREADS = 2


def encode_jpeg(image: np.ndarray) -> bytes | None:
    """JPEG bytes for a crop, or None when it cannot be encoded.

    OpenCV is imported here rather than at module scope so this module can be
    imported where it is absent. That is not hypothetical: the shared test suite
    runs the worker's tests inside the API image, which carries numpy but not
    cv2, and a module-level import made the whole file uncollectable.
    """
    import cv2

    ok, buffer = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    )
    return buffer.tobytes() if ok else None


def object_key(kind: str, name: str, now: datetime | None = None) -> str:
    """Where a crop lives in the bucket.

    Date-partitioned so a retention sweep can drop a day's evidence with a
    prefix delete rather than walking every object — retention on this data is
    a legal question, not a housekeeping one, and it has to be cheap enough to
    actually run.
    """
    day = (now or datetime.now(UTC)).strftime("%Y/%m/%d")
    return f"crops/{kind}/{day}/{name}.jpg"


class MinioCropStore:
    """A `run_dir` that uploads crops instead of writing them to disk."""

    def __init__(
        self,
        encoder: Callable[[np.ndarray], bytes | None] | None = None,
        threads: int = UPLOAD_THREADS,
    ) -> None:
        # Injectable so the store's own logic — key format, the bounded queue,
        # never returning a key for a crop that was dropped — can be tested
        # without OpenCV. That matters practically: the shared test image
        # carries numpy but not cv2, and the worker image carries no pytest, so
        # tests that needed real encoding would have run in neither.
        self._encode = encoder or encode_jpeg
        self._started_at = datetime.now(UTC)
        self._queue: queue.Queue[tuple[str, bytes] | None] = queue.Queue(
            maxsize=QUEUE_SIZE
        )
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._dropped = 0
        self._uploaded = 0

        # `threads=0` leaves the queue undrained, which is the only way to
        # test that a full queue drops crops rather than blocking or growing.
        self._threads = [
            threading.Thread(target=self._drain, name=f"crop-upload-{i}", daemon=True)
            for i in range(threads)
        ]
        for thread in self._threads:
            thread.start()

    # ── the interface StreamRunner calls ────────────────────────────────
    def save_plate_crop(self, image: np.ndarray, name: str) -> str | None:
        return self._enqueue(image, "plates", name)

    def save_vehicle_crop(self, image: np.ndarray, name: str) -> str | None:
        return self._enqueue(image, "vehicles", name)

    @property
    def path(self) -> Path:
        """Present only because the runner's protocol has it."""
        return Path(".")

    @property
    def started_at(self) -> datetime:
        return self._started_at

    # ── internals ───────────────────────────────────────────────────────
    def _enqueue(self, image: np.ndarray, kind: str, name: str) -> str | None:
        if image is None or getattr(image, "size", 0) == 0:
            return None

        key = object_key(kind, name)

        payload = self._encode(image)
        if payload is None:
            log.warning("crops.encode_failed key=%s", key)
            return None

        try:
            self._queue.put_nowait((key, payload))
        except queue.Full:
            self._dropped += 1
            if self._dropped % 50 == 1:
                log.warning(
                    "crops.queue_full dropped=%d — object storage is not keeping up",
                    self._dropped,
                )
            # No key, because no object will exist under it. Returning one
            # anyway would put a dangling reference in the database.
            return None

        return key

    def _ensure_client(self) -> Any:
        """Create the MinIO client on first use, from whichever thread gets there."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                from minio import Minio

                self._client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_root_user,
                    secret_key=settings.minio_root_password,
                    secure=settings.minio_secure,
                )
        return self._client

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            key, payload = item
            try:
                client = self._ensure_client()
                client.put_object(
                    settings.minio_bucket,
                    key,
                    io.BytesIO(payload),
                    length=len(payload),
                    content_type="image/jpeg",
                )
                self._uploaded += 1
            except Exception as exc:
                # Supervisor boundary: an upload thread that dies takes every
                # later crop with it, silently. A failed upload costs one piece
                # of evidence and is logged.
                log.warning(
                    "crops.upload_failed key=%s error=%s", key, exc, exc_info=True
                )
            finally:
                self._queue.task_done()

    def stats(self) -> dict[str, int]:
        return {
            "uploaded": self._uploaded,
            "dropped": self._dropped,
            "queued": self._queue.qsize(),
        }

    def close(self, timeout: float = 5.0) -> None:
        """Drain what is queued, then stop the threads."""
        with contextlib.suppress(Exception):
            self._queue.join()
        for _ in self._threads:
            # A full queue at shutdown just means the sentinel cannot be
            # delivered; the threads are daemons and will not hold exit.
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
        for thread in self._threads:
            thread.join(timeout=timeout)
