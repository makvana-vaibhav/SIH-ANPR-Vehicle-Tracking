#!/usr/bin/env bash
#
# Fetch the ONNX models the lab runs on.
#
# All three are Ultralytics exports with class names embedded in the model
# metadata, so the loader configures itself from the file. Checksums are pinned:
# a model that changes under us would silently change every number the lab
# reports, and that must fail loudly instead.
#
# Total download: ~22 MB. No API key, no login.
#
set -euo pipefail

cd "$(dirname "$0")/.."
MODELS_DIR="models"
mkdir -p "$MODELS_DIR"

HF="https://huggingface.co"

# name | url | sha256 | description
MODELS=(
  "yolov8n.onnx|${HF}/unity/inference-engine-yolo/resolve/main/models/yolov8n.onnx|341ad75c98ff88775c63e899e7cbbf497c13161e3393b95b620a6cab65052811|YOLOv8n COCO — vehicles and people (fp16, 640px)"
  "yolo11n.onnx|${HF}/unity/inference-engine-yolo/resolve/main/models/yolo11n.onnx|0690c675a942f4dc4f463b3e4e89520117aeea3fd767784df2934705c91ed11b|YOLO11n COCO — newer alternative detector, for comparison runs"
  "plate_yolo11n.onnx|${HF}/morsetechlab/yolov11-license-plate-detection/resolve/main/license-plate-finetune-v1n.onnx|693133a1db97a3ba1e90068986f80afb72c3fcddb681e57181a89a9a3dc351d6|YOLO11n fine-tuned for licence plates (fp32, dynamic shape)"
)

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

echo "Fetching models into ${MODELS_DIR}/"
for entry in "${MODELS[@]}"; do
  IFS='|' read -r name url expected description <<< "$entry"
  target="${MODELS_DIR}/${name}"

  if [[ -f "$target" ]] && [[ "$(sha256_of "$target")" == "$expected" ]]; then
    echo "  ✓ ${name} (already present)"
    continue
  fi

  echo "  ↓ ${name} — ${description}"
  if ! curl -fsSL --retry 3 --retry-delay 2 -o "${target}.part" "$url"; then
    rm -f "${target}.part"
    echo "    ✗ download failed: ${url}" >&2
    echo "      The lab still runs without this file if you select an engine that" >&2
    echo "      does not need it (e.g. --set plate.engine=contour)." >&2
    exit 1
  fi

  actual="$(sha256_of "${target}.part")"
  if [[ "$actual" != "$expected" ]]; then
    rm -f "${target}.part"
    echo "    ✗ checksum mismatch for ${name}" >&2
    echo "      expected ${expected}" >&2
    echo "      actual   ${actual}" >&2
    echo "      The upstream file changed. Verify it before updating the pin." >&2
    exit 1
  fi
  mv "${target}.part" "$target"
  echo "    ✓ verified"
done

# RapidOCR ships PP-OCRv4 inside its wheel, so there is nothing to fetch for OCR.
echo
echo "Models ready. OCR models ship inside the rapidocr-onnxruntime wheel (no download)."
echo "Verify everything with:  ailab doctor"
