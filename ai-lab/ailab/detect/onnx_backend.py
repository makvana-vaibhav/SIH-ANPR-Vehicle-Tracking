"""Thin ONNX Runtime wrapper for Ultralytics-exported YOLO models.

Handles the three things that differ between exports and silently break naive
loaders: fp16 vs fp32 tensors, fixed vs dynamic input shapes, and where the
class names live. Ultralytics embeds `names`, `imgsz` and `task` in the model
metadata, so the class list is read from the file rather than hardcoded — point
this at a plate model or a COCO model and it configures itself.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

from ailab import runtime
from ailab.detect.postprocess import batched_nms, decode_yolo_output, letterbox, undo_letterbox
from ailab.logging import get_logger

log = get_logger(__name__)


# Measured on this host (10 vCPU, Docker Desktop on Apple Silicon), YOLOv8n at
# 640px: 1 thread 275ms, 2 172ms, 3 141ms, 4 126ms, 6 194ms, 8 331ms, and ONNX
# Runtime's own default 452ms. Past four threads the small convolutions spend
# more time synchronising than computing, and the library's default of
# one-thread-per-core is the worst setting available — 3.6x slower than the
# best. Left alone, this silently dominates every timing the lab reports.
DEFAULT_INTRA_THREADS = runtime.MAX_THREADS_PER_MODEL


def resolve_threads(requested: int) -> int:
    """Intra-op thread count. 0 means 'choose sensibly', not 'let ORT decide'.

    Delegated to `ailab.runtime`, which divides one process-wide budget across
    however many pipelines are running. Capping per model but not per process
    was the original bug: four threads is right for one camera and three times
    too many when three cameras each believe they are the only one.
    """
    return runtime.threads_per_model(requested)


def select_providers(device: str = "auto") -> list[str]:
    """Choose execution providers, preferring an accelerator when one is real.

    ## What is actually available where

    **Docker on Apple Silicon: nothing.** Virtualisation.framework passes no
    GPU to a Linux guest, so a container on this Mac reports exactly
    `['AzureExecutionProvider', 'CPUExecutionProvider']` — verified, not
    assumed. There is no configuration that changes this and no point looking
    for one; `docs/GPU.md` records the whole matrix.

    **macOS natively: CoreML**, which reaches the GPU and the Neural Engine.
    Available only when the worker runs outside Docker on the host, because
    that is where the Apple frameworks are.

    **amd64 with an NVIDIA card: CUDA**, needing `onnxruntime-gpu` and the
    NVIDIA container toolkit. This is the deployment path in
    `scripts/capacity_model.py --gpu-speedup`.

    An explicit `cuda` or `coreml` raises when the provider is missing rather
    than quietly running on CPU: a GPU deployment that silently falls back is a
    capacity plan that is wrong by an order of magnitude and says nothing.
    `auto` takes the best present and is what the demo runs.
    """
    installed = list(ort.get_available_providers())
    if device == "cpu":
        return ["CPUExecutionProvider"]

    explicit = {"cuda": "CUDAExecutionProvider", "coreml": "CoreMLExecutionProvider"}
    if device in explicit:
        wanted = explicit[device]
        if wanted not in installed:
            raise RuntimeError(
                f"device={device!r} requested but onnxruntime has no {wanted}. "
                f"Installed: {installed}. See docs/GPU.md — inside Docker on Apple "
                f"Silicon no accelerator exists, so use device='auto'."
            )
        return [wanted, "CPUExecutionProvider"]

    preferred = ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in installed] or ["CPUExecutionProvider"]


class OnnxYoloModel:
    """One loaded YOLO ONNX graph, ready to run on BGR images."""

    def __init__(
        self,
        weights: str | Path,
        imgsz: int = 640,
        device: str = "auto",
        intra_threads: int = 0,
    ) -> None:
        path = Path(weights)
        if not path.exists():
            raise FileNotFoundError(
                f"model weights not found: {path}\n"
                f"Run  ./scripts/fetch_models.sh  to download them."
            )

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = resolve_threads(intra_threads)
        # One inter-op thread. That pool parallelises independent *branches* of
        # a graph, and a YOLO backbone is a chain — so the extra threads find
        # nothing to run and are pure scheduler load on a box already carrying
        # one pipeline per camera.
        opts.inter_op_num_threads = 1

        self.providers = select_providers(device)
        self.session = ort.InferenceSession(str(path), opts, providers=self.providers)
        self.weights_path = str(path)

        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_dtype = np.float16 if "float16" in inp.type else np.float32

        # A fixed-shape export dictates its own input size; honour it rather
        # than failing at inference with a shape mismatch.
        static_h = inp.shape[2] if isinstance(inp.shape[2], int) else None
        static_w = inp.shape[3] if isinstance(inp.shape[3], int) else None
        self.dynamic = static_h is None or static_w is None
        if not self.dynamic:
            if static_h != static_w:
                raise ValueError(f"non-square fixed input {inp.shape} is not supported")
            if imgsz != static_h:
                # Warning, not debug. A configured value being ignored is the
                # single most expensive kind of silence: `--set
                # detector.imgsz=1280` appears to work, changes nothing, and
                # sends you looking for the answer in the model instead of the
                # export. Say so where it will actually be read.
                log.warning(
                    "%s has a fixed %dpx input, so imgsz=%d is ignored. "
                    "Re-export the model at %d to change it "
                    "(scripts/fetch_models.sh).",
                    path.name, static_h, imgsz, imgsz,
                )
            self.imgsz = int(static_h)
        else:
            self.imgsz = int(imgsz)

        self.metadata: dict[str, str] = dict(self.session.get_modelmeta().custom_metadata_map)
        self.names: dict[int, str] = self._parse_names(self.metadata.get("names"))
        self.num_classes = len(self.names) if self.names else None
        self.task = self.metadata.get("task", "detect")
        try:
            self.stride = int(float(self.metadata.get("stride", 32)))
        except ValueError:
            self.stride = 32

        log.debug(
            "loaded %s (%s, %dpx, %d classes, %s)",
            path.name, self.input_dtype.__name__, self.imgsz,
            len(self.names), self.providers[0],
        )

    @staticmethod
    def _parse_names(raw: str | None) -> dict[int, str]:
        if not raw:
            return {}
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            log.warning("could not parse class names from model metadata")
            return {}
        if isinstance(parsed, dict):
            return {int(k): str(v) for k, v in parsed.items()}
        if isinstance(parsed, list | tuple):
            return {i: str(v) for i, v in enumerate(parsed)}
        return {}

    def class_name(self, class_id: int) -> str:
        return self.names.get(class_id, f"class_{class_id}")

    def input_size_for(
        self, image: np.ndarray, min_size: int = 192, strategy: str = "fixed"
    ) -> int:
        """Choose an input resolution for this image.

        A fixed-shape export dictates its own size and there is nothing to
        decide. For a dynamic one:

        "fixed" returns `imgsz` unchanged. For searching a plate inside a
        vehicle crop this is correct, because the plate is a roughly constant
        *fraction* of the crop: any vehicle letterboxed to 320px shows its plate
        at roughly 70px, whether the crop is 90px wide or 900px. Cost is then
        constant per search and independent of source resolution.

        "proportional" scales the input with the crop, capped at `imgsz`. This
        looks sensible and is a trap at high resolution: 4K vehicle crops are
        large enough to hit the cap every time, so it degenerates to always
        using the maximum while still paying to compute the ratio.
        """
        if not self.dynamic:
            return self.imgsz
        if strategy == "fixed":
            return self.imgsz

        longest = max(image.shape[:2])
        target = max(min_size, int(longest * 2.0))
        rounded = int(np.ceil(target / self.stride) * self.stride)
        return int(min(self.imgsz, max(self.stride * 2, rounded)))

    def infer(
        self,
        image: np.ndarray,
        conf: float = 0.25,
        iou: float = 0.45,
        max_detections: int = 300,
        allowed_class_ids: set[int] | None = None,
        imgsz: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """Run one image. Returns (boxes_xyxy, scores, class_ids, latency_s).

        Boxes are in the coordinate space of the image passed in.
        """
        if image is None or image.size == 0:
            empty = np.empty((0, 4), np.float32)
            return empty, np.empty((0,), np.float32), np.empty((0,), np.int64), 0.0

        height, width = image.shape[:2]
        size = self.imgsz if not self.dynamic else (imgsz or self.imgsz)
        padded, scale, pad = letterbox(image, size)

        # BGR→RGB, HWC→CHW, 0..1
        blob = padded[:, :, ::-1].transpose(2, 0, 1)[None]
        blob = np.ascontiguousarray(blob, dtype=self.input_dtype) / self.input_dtype(255.0)

        started = time.perf_counter()
        outputs = self.session.run(None, {self.input_name: blob})
        latency = time.perf_counter() - started

        boxes, scores, class_ids = decode_yolo_output(outputs[0], conf, self.num_classes)
        if boxes.size == 0:
            return boxes, scores, class_ids, latency

        if allowed_class_ids is not None:
            mask = np.isin(class_ids, list(allowed_class_ids))
            boxes, scores, class_ids = boxes[mask], scores[mask], class_ids[mask]
            if boxes.size == 0:
                return boxes, scores, class_ids, latency

        keep = batched_nms(boxes, scores, class_ids, iou)[:max_detections]
        boxes = undo_letterbox(boxes[keep], scale, pad, width, height)
        return boxes, scores[keep], class_ids[keep], latency

    def describe(self) -> dict[str, Any]:
        """Provenance for the run record — which file, which graph, which EP."""
        return {
            "weights": self.weights_path,
            "imgsz": self.imgsz,
            "dynamic": self.dynamic,
            "precision": self.input_dtype.__name__,
            "providers": self.providers,
            "intra_op_threads": self.session.get_session_options().intra_op_num_threads,
            "classes": len(self.names),
            "task": self.task,
            "trained_on": self.metadata.get("description", ""),
            "exported": self.metadata.get("date", ""),
            "ultralytics_version": self.metadata.get("version", ""),
            "license": self.metadata.get("license", ""),
        }
