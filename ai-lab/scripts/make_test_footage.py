#!/usr/bin/env python3
"""Generate CCTV-like footage with known plates, for validating the pipeline.

Why this exists
---------------
Measuring a pipeline needs footage where the right answer is known. The sample
clips in this repository are an aerial highway view, a bicycle POV and a
pedestrian street — none has a legible plate, so none can tell us whether OCR
works. Until real labelled Gujarat footage arrives, this generates a stand-in.

How it works
------------
Real vehicle crops are cut out of real footage with the same YOLO detector the
pipeline uses, so the vehicles are genuine car pixels and the object detector
will find them. A synthetic Indian plate is then composited onto each vehicle
with perspective, blur and lighting matched to the crop, and the vehicles are
animated across a real road background.

What this does and does not prove
---------------------------------
It exercises the whole chain end to end — detection, tracking, plate detection,
OCR, consensus, evaluation — against known ground truth. That is enough to find
mechanical faults and to compare configurations against each other.

It is NOT a measure of real-world accuracy. The plates are rendered from a clean
font, not photographed: no embossing, no dirt, no motion blur from a real
shutter, no unusual regional fonts. **Accuracy measured here will be optimistic.**
Numbers from this footage must never be quoted as the system's accuracy — see
FINDINGS.md.

Usage
-----
    python scripts/make_test_footage.py \
        --background /data/videos/road_cctv.mp4 \
        --vehicles   /data/videos/road_cctv.mp4 \
        --out /lab/runs/_synthetic --vehicles-count 8 --seconds 20
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ailab.detect.onnx_backend import OnnxYoloModel

# Gujarat RTO codes actually in use, so generated plates are plausible.
GJ_RTO = [f"{n:02d}" for n in range(1, 40)]
SERIES_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # I and O omitted, as RTOs do


def random_plate(rng: random.Random) -> str:
    return (
        "GJ"
        + rng.choice(GJ_RTO)
        + "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([1, 2])))
        + f"{rng.randint(0, 9999):04d}"
    )


def render_plate(text: str, width: int, height: int) -> np.ndarray:
    """A white Indian plate with black characters, scaled to fit."""
    image = np.full((height, width, 3), 248, dtype=np.uint8)
    cv2.rectangle(image, (1, 1), (width - 2, height - 2), (25, 25, 25), max(1, height // 24))

    margin_x, margin_y = int(width * 0.06), int(height * 0.18)
    (base_w, base_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, 1.0, 2)
    scale = min(
        (width - 2 * margin_x) / max(base_w, 1), (height - 2 * margin_y) / max(base_h, 1)
    )
    thickness = max(1, int(round(scale * 1.7)))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thickness)
    cv2.putText(
        image, text, ((width - tw) // 2, (height + th) // 2),
        cv2.FONT_HERSHEY_DUPLEX, scale, (18, 18, 18), thickness, cv2.LINE_AA,
    )
    return image


def paste_plate(
    vehicle: np.ndarray, text: str, rng: random.Random, difficulty: str
) -> np.ndarray:
    """Composite a plate onto a vehicle crop, matched to its lighting."""
    out = vehicle.copy()
    vh, vw = out.shape[:2]

    # Rear plates sit low and central, about a fifth of the vehicle's width.
    plate_w = max(24, int(vw * rng.uniform(0.30, 0.42)))
    plate_h = max(8, int(plate_w / rng.uniform(4.2, 5.0)))
    if plate_w >= vw or plate_h >= vh:
        return out

    plate = render_plate(text, plate_w * 3, plate_h * 3)   # render large, then shrink

    # Perspective: a camera is never square-on to a plate.
    if difficulty in ("normal", "hard"):
        skew = rng.uniform(0.03, 0.10 if difficulty == "hard" else 0.06)
        h, w = plate.shape[:2]
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dx, dy = w * skew, h * skew
        dst = np.float32(
            [[dx * rng.random(), dy * rng.random()],
             [w - dx * rng.random(), dy * rng.random()],
             [w - dx * rng.random(), h - dy * rng.random()],
             [dx * rng.random(), h - dy * rng.random()]]
        )
        plate = cv2.warpPerspective(plate, cv2.getPerspectiveTransform(src, dst), (w, h))

    plate = cv2.resize(plate, (plate_w, plate_h), interpolation=cv2.INTER_AREA)

    # Match the vehicle's exposure so the plate does not glow against a dark car.
    vehicle_luma = float(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).mean())
    gain = np.clip(0.55 + vehicle_luma / 255.0, 0.6, 1.15)
    plate = np.clip(plate.astype(np.float32) * gain, 0, 255).astype(np.uint8)

    if difficulty == "hard":
        plate = cv2.GaussianBlur(plate, (3, 3), rng.uniform(0.4, 1.1))
        noise = rng.uniform(2.0, 6.0)
        plate = np.clip(
            plate.astype(np.float32)
            + np.random.default_rng(rng.randint(0, 10**6)).normal(0, noise, plate.shape),
            0, 255,
        ).astype(np.uint8)

    x = (vw - plate_w) // 2 + rng.randint(-vw // 20, vw // 20)
    y = int(vh * rng.uniform(0.62, 0.76))
    x, y = max(0, min(x, vw - plate_w)), max(0, min(y, vh - plate_h))
    out[y : y + plate_h, x : x + plate_w] = plate
    return out


def harvest_vehicles(video: Path, model: OnnxYoloModel, wanted: int, rng: random.Random) -> list[np.ndarray]:
    """Cut real vehicle crops out of real footage using the real detector."""
    by_name = {n.lower(): i for i, n in model.names.items()}
    vehicle_ids = {by_name[n] for n in ("car", "truck", "bus") if n in by_name}

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise SystemExit(f"could not open {video}")

    crops: list[np.ndarray] = []
    frame_index = 0
    while len(crops) < wanted * 4 and frame_index < 900:
        ok, frame = capture.read()
        if not ok:
            break
        frame_index += 1
        if frame_index % 7:
            continue

        boxes, scores, class_ids, _ = model.infer(
            frame, conf=0.55, iou=0.45, allowed_class_ids=vehicle_ids
        )
        for box, score in zip(boxes, scores, strict=False):
            x1, y1, x2, y2 = (int(v) for v in box)
            w, h = x2 - x1, y2 - y1
            # Big enough that a pasted plate is legible, and not clipped by the
            # frame edge — a half-vehicle makes a nonsense sprite.
            if w < 90 or h < 70 or score < 0.6:
                continue
            if x1 <= 2 or y1 <= 2 or x2 >= frame.shape[1] - 2 or y2 >= frame.shape[0] - 2:
                continue
            crops.append(frame[y1:y2, x1:x2].copy())
    capture.release()

    if not crops:
        raise SystemExit(
            f"no usable vehicle crops found in {video}. Try footage with larger, "
            "unclipped vehicles."
        )
    rng.shuffle(crops)
    return crops[:wanted]


def background_frame(video: Path, width: int, height: int) -> np.ndarray:
    """A real road frame, blurred so pasted sprites dominate the detector."""
    capture = cv2.VideoCapture(str(video))
    capture.set(cv2.CAP_PROP_POS_FRAMES, 5)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        return np.full((height, width, 3), 90, dtype=np.uint8)
    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    # A light blur suppresses the background's own vehicles without turning the
    # scene into an obviously synthetic flat colour.
    return cv2.GaussianBlur(frame, (0, 0), 3.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--background", required=True, type=Path)
    parser.add_argument("--vehicles", type=Path, help="footage to cut vehicle crops from (default: --background)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--weights", default="models/yolov8n.onnx")
    parser.add_argument("--vehicles-count", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--difficulty", choices=["easy", "normal", "hard"], default="normal")
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    weights = Path(args.weights)
    if not weights.is_absolute():
        weights = Path(__file__).resolve().parent.parent / weights
    model = OnnxYoloModel(weights)

    source = args.vehicles or args.background
    print(f"harvesting vehicle crops from {source}")
    crops = harvest_vehicles(source, model, args.vehicles_count, rng)
    print(f"  got {len(crops)} crops")

    background = background_frame(args.background, args.width, args.height)
    total_frames = int(args.seconds * args.fps)

    # Vehicles are placed in separated lanes, and vehicles sharing a lane are
    # staggered so their time on screen does not overlap.
    #
    # This is not cosmetic. An earlier version picked lane_y at random, so
    # sprites occluded one another and two of eight plates were never visible
    # at all — the benchmark was measuring the generator, not the pipeline.
    # Overlap belongs in footage designed to test occlusion, not in the clip
    # used to measure baseline accuracy.
    prepared = [(paste_plate(crop, plate, rng, args.difficulty), plate)
                for crop, plate in ((c, random_plate(rng)) for c in crops)]

    tallest = max(s.shape[0] for s, _ in prepared)
    max_scale = 1.30
    lane_height = int(tallest * max_scale * 1.05)
    usable_top = int(args.height * 0.12)
    usable_bottom = int(args.height * 0.92)
    lane_count = max(1, (usable_bottom - usable_top) // max(1, lane_height))
    lane_centres = [
        usable_top + lane_height // 2 + i * lane_height for i in range(lane_count)
    ]

    plan = []
    lane_free_at: dict[int, int] = {i: 0 for i in range(lane_count)}
    for index, (sprite, plate) in enumerate(prepared):
        lane = index % lane_count
        duration = max(8, int(rng.uniform(total_frames * 0.28, total_frames * 0.45)))
        # Enter only once the lane is clear, so same-lane vehicles never overlap.
        enter = lane_free_at[lane]
        if enter + duration > total_frames:
            enter = 0
            duration = min(duration, total_frames - 1)
        lane_free_at[lane] = enter + duration + 2

        plan.append(
            {
                "index": index,
                "plate": plate,
                "sprite": sprite,
                "lane_y": lane_centres[lane],
                "enter": enter,
                "duration": duration,
                "start_scale": rng.uniform(0.42, 0.62),
                "end_scale": rng.uniform(0.95, max_scale),
                "direction": rng.choice([1, -1]),
            }
        )

    video_path = args.out / "synthetic_traffic.mp4"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (args.width, args.height)
    )
    if not writer.isOpened():
        raise SystemExit(f"could not open a writer for {video_path}")

    for frame_index in range(total_frames):
        canvas = background.copy()
        for vehicle in plan:
            elapsed = frame_index - vehicle["enter"]
            if elapsed < 0 or elapsed > vehicle["duration"]:
                continue
            progress = elapsed / vehicle["duration"]

            scale = vehicle["start_scale"] + progress * (vehicle["end_scale"] - vehicle["start_scale"])
            sprite = vehicle["sprite"]
            sw, sh = int(sprite.shape[1] * scale), int(sprite.shape[0] * scale)
            if sw < 24 or sh < 24:
                continue
            sprite = cv2.resize(sprite, (sw, sh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)

            travel = args.width + sw
            x = int(-sw + progress * travel) if vehicle["direction"] > 0 else int(args.width - progress * travel)
            y = vehicle["lane_y"] - sh // 2

            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(args.width, x + sw), min(args.height, y + sh)
            if x2 <= x1 or y2 <= y1:
                continue
            canvas[y1:y2, x1:x2] = sprite[y1 - y : y2 - y, x1 - x : x2 - x]

        writer.write(canvas)
    writer.release()

    truth_path = args.out / "ground_truth.csv"
    with truth_path.open("w", newline="", encoding="utf-8") as handle:
        writer_csv = csv.writer(handle)
        writer_csv.writerow(["plate", "enters_frame", "leaves_frame", "difficulty"])
        for vehicle in sorted(plan, key=lambda v: v["enter"]):
            writer_csv.writerow(
                [vehicle["plate"], vehicle["enter"],
                 vehicle["enter"] + vehicle["duration"], args.difficulty]
            )

    print(f"\nwrote {video_path}  ({total_frames} frames, {args.seconds:.0f}s @ {args.fps:.0f}fps)")
    print(f"wrote {truth_path}  ({len(plan)} plates)")
    print("\nplates:", ", ".join(v["plate"] for v in sorted(plan, key=lambda v: v["enter"])))
    print(
        "\nNOTE: plates are rendered, not photographed. Accuracy measured on this "
        "footage is optimistic and must not be quoted as system accuracy."
    )
    print(f"\n  ailab run {video_path} --ground-truth {truth_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
