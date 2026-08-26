"""Configuration loading, inheritance and overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ailab.config import REPO_ROOT, RunConfig, parse_override


def test_defaults_are_valid() -> None:
    config = RunConfig()
    assert config.detector.engine == "yolo_onnx"
    assert config.ocr.engine == "rapidocr"
    assert "car" in config.detector.plate_bearing_classes


def test_shipped_configs_all_load() -> None:
    """Every config in the repo must parse — a broken one fails at run time."""
    paths = list((REPO_ROOT / "configs").rglob("*.yaml"))
    assert paths, "no configs found"
    for path in paths:
        config = RunConfig.load(path)
        assert config.name


def test_extends_merges_with_the_parent(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(
        yaml.safe_dump({"name": "base", "detector": {"confidence": 0.3, "imgsz": 640}})
    )
    (tmp_path / "child.yaml").write_text(
        yaml.safe_dump({"extends": "base.yaml", "name": "child",
                        "detector": {"confidence": 0.5}})
    )
    config = RunConfig.load(tmp_path / "child.yaml")
    assert config.name == "child"
    assert config.detector.confidence == 0.5
    assert config.detector.imgsz == 640      # inherited, not reset to the default


def test_overrides_apply() -> None:
    config = RunConfig().with_overrides({"detector.confidence": 0.42, "source.frame_stride": 3})
    assert config.detector.confidence == 0.42
    assert config.source.frame_stride == 3


def test_unknown_override_is_rejected() -> None:
    """A typo'd key must fail loudly, not be silently ignored."""
    with pytest.raises(KeyError):
        RunConfig().with_overrides({"detector.confidense": 0.4})


def test_plate_classes_must_be_detectable() -> None:
    """Config that could never produce a plate is refused up front."""
    with pytest.raises(ValueError, match="plate_bearing_classes"):
        RunConfig.model_validate(
            {"detector": {"classes": ["person"], "plate_bearing_classes": ["car"]}}
        )


@pytest.mark.parametrize(
    "text,key,value",
    [
        ("detector.confidence=0.4", "detector.confidence", 0.4),
        ("source.frame_stride=2", "source.frame_stride", 2),
        ("output.annotated_video=false", "output.annotated_video", False),
        ("ocr.engine=easyocr", "ocr.engine", "easyocr"),
    ],
)
def test_override_parsing(text: str, key: str, value: object) -> None:
    assert parse_override(text) == (key, value)
