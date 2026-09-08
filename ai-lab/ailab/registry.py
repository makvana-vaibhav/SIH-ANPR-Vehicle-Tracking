"""Component registry.

Every swappable part of the pipeline — detector, tracker, plate detector, OCR —
registers itself under a name. The config names a component; the registry hands
back the class. Adding a new OCR engine is one decorator and zero edits to the
pipeline.

Imports are deferred to first use so that an optional heavy dependency
(PyTorch, EasyOCR) is only imported when a config actually asks for it. A lab
with only the core requirements installed still starts instantly.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# kind -> name -> factory
_REGISTRY: dict[str, dict[str, Callable[..., Any]]] = {}

# kind -> name -> module path to import on demand
_LAZY: dict[str, dict[str, str]] = {
    "detector": {
        "yolo_onnx": "ailab.detect.yolo_onnx",
        "ultralytics": "ailab.detect.yolo_ultralytics",
    },
    "tracker": {
        "bytetrack": "ailab.track.bytetrack",
        "iou": "ailab.track.iou_tracker",
    },
    "plate_detector": {
        "plate_yolo_onnx": "ailab.plate.yolo_onnx",
        "plate_ultralytics": "ailab.plate.yolo_ultralytics",
        "contour": "ailab.plate.contour",
    },
    "ocr": {
        "rapidocr": "ailab.ocr.rapid",
        "easyocr": "ailab.ocr.easy",
        "crnn_onnx": "ailab.ocr.crnn_onnx",
    },
}


# Third-party modules each component actually needs at construction time.
# The wrapper modules import these lazily so the registry stays cheap, which
# means importing a wrapper proves nothing about whether the engine can run.
# `doctor` must not report an engine as available when its dependency is absent.
_REQUIRES: dict[str, dict[str, tuple[str, ...]]] = {
    "detector": {
        "yolo_onnx": ("onnxruntime",),
        "ultralytics": ("ultralytics", "torch"),
    },
    "tracker": {
        "bytetrack": ("scipy",),
        "iou": ("scipy",),
    },
    "plate_detector": {
        "plate_yolo_onnx": ("onnxruntime",),
        "plate_ultralytics": ("ultralytics", "torch"),
        "contour": ("cv2",),
    },
    "ocr": {
        "rapidocr": ("rapidocr_onnxruntime",),
        "easyocr": ("easyocr", "torch"),
        "crnn_onnx": ("onnxruntime",),
    },
}


class ComponentNotFound(KeyError):
    """Raised with the list of what *is* available — a missing engine name is
    almost always a typo, and showing the alternatives saves a round trip."""


def register(kind: str, name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        _REGISTRY.setdefault(kind, {})[name] = cls
        cls.engine_name = name
        return cls

    return decorator


def create(kind: str, name: str, *args: Any, **kwargs: Any) -> Any:
    """Instantiate the named component, importing its module if needed."""
    if name not in _REGISTRY.get(kind, {}):
        module = _LAZY.get(kind, {}).get(name)
        if module:
            try:
                importlib.import_module(module)
            except ImportError as exc:
                raise ComponentNotFound(
                    f"{kind} '{name}' needs a dependency that is not installed: {exc}. "
                    f"Engines requiring PyTorch are in the optional profile — "
                    f"install with: pip install -r requirements-torch.txt"
                ) from exc
    try:
        factory = _REGISTRY[kind][name]
    except KeyError:
        raise ComponentNotFound(
            f"unknown {kind}: '{name}'. Available: {sorted(available(kind))}"
        ) from None
    return factory(*args, **kwargs)


def available(kind: str) -> list[str]:
    """Names that exist, whether or not their dependencies are importable."""
    return sorted(set(_REGISTRY.get(kind, {})) | set(_LAZY.get(kind, {})))


def kinds() -> list[str]:
    return sorted(set(_REGISTRY) | set(_LAZY))


def probe(kind: str, name: str) -> tuple[bool, str]:
    """Can this component actually be constructed here? Used by `ailab doctor`.

    Checks the third-party dependencies too. The wrapper modules defer their
    heavy imports, so importing one successfully says nothing about whether the
    engine behind it will start — reporting 'available' on that basis would send
    someone into a long run that dies at the first frame.
    """
    for dependency in _REQUIRES.get(kind, {}).get(name, ()):
        if importlib.util.find_spec(dependency) is None:
            return False, f"{dependency} not installed"

    module = _LAZY.get(kind, {}).get(name)
    if name in _REGISTRY.get(kind, {}):
        return True, "loaded"
    if not module:
        return False, "no module mapping"
    try:
        importlib.import_module(module)
    except ImportError as exc:
        return False, str(exc).split("\n")[0]
    return (name in _REGISTRY.get(kind, {})), "importable"
