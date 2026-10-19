"""Spike 2.4: compare YOLO models on recorded clips.

    uv run python scripts/spike-models.py --fps 1 --out <dir> clips/spike24/ch1_main/*.mp4

Decodes each clip like the detector (1280x720), runs every model on the same
frames, and prints per clip: latency per frame, and on how many frames each
model found cars, motorcycles, motorcycles with a rider and people. Frames where
any model found a vehicle are saved to ``--out`` with each model's boxes drawn
(yolo11n red, yolo11s blue), to be looked at.
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

from sentinel.detector.detection import CAR, MOTORCYCLE, PERSON
from sentinel.detector.inference import CoreMLBackend, model_path
from sentinel.detector.replay import clip_frames
from sentinel.detector.riders import attach_riders

COLORS = {"yolo11n": (0, 0, 255), "yolo11s": (255, 128, 0)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("clips", nargs="+", type=Path)
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--models", nargs="+", default=["yolo11n", "yolo11s"])
    parser.add_argument("--min-confidence", type=float, default=0.4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    backends = {m: CoreMLBackend(model_path(m), args.min_confidence) for m in args.models}
    args.out.mkdir(parents=True, exist_ok=True)

    for clip in args.clips:
        times = {m: [] for m in backends}
        found = {m: Counter() for m in backends}
        motorcycle_at = {m: [] for m in backends}
        frames = saved = 0
        for frame in clip_frames(clip, "spike", args.fps, datetime.now(UTC)):
            frames += 1
            canvas = frame.image.copy()
            any_vehicle = False
            for name, backend in backends.items():
                start = time.perf_counter()
                detections = backend.detect(frame.image)
                times[name].append((time.perf_counter() - start) * 1000)
                vehicles = attach_riders(detections)
                classes = {v.cls for v in vehicles}
                found[name]["car"] += CAR in classes
                found[name]["motorcycle"] += MOTORCYCLE in classes
                if MOTORCYCLE in classes:
                    motorcycle_at[name].append(round(frame.monotonic))
                found[name]["with rider"] += any(v.rider for v in vehicles)
                found[name]["person"] += any(d.cls == PERSON for d in detections)
                any_vehicle |= bool(vehicles)
                for d in detections:
                    x1, y1, x2, y2 = (round(c) for c in d.box)
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), COLORS.get(name, (0, 255, 0)), 2)
                    cv2.putText(
                        canvas,
                        f"{name[-1]} {d.cls} {d.confidence:.2f}",
                        (x1, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        COLORS.get(name, (0, 255, 0)),
                        1,
                    )
            if any_vehicle:
                name = f"{clip.parent.name}-{clip.stem}-{frames:05d}.jpg"
                cv2.imwrite(str(args.out / name), canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])
                saved += 1
        print(f"{clip.parent.name}/{clip.name}: {frames} frames, {saved} saved")
        for name in backends:
            ms = sorted(times[name][1:] or times[name])  # the first call warms up
            if not ms:
                continue
            counts = ", ".join(
                f"{k} {found[name][k]}" for k in ("car", "motorcycle", "with rider", "person")
            )
            print(
                f"  {name}: median {ms[len(ms) // 2]:.1f} ms, p95 {ms[int(len(ms) * 0.95)]:.1f} ms; frames with {counts}"
            )
            if motorcycle_at[name]:
                print(f"    {name} motorcycle at s: {motorcycle_at[name]}")


if __name__ == "__main__":
    main()
