"""``make zone-preview CAMERA=<id>``: the camera's current frame with its zones
and live detections drawn, saved to ``docs/zone-preview-<id>.png`` (git-ignored),
to adjust the zone polygons in sentinel.yaml.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from sentinel import config as config_module
from sentinel.config import Camera
from sentinel.detector.detection import PERSON, Detection
from sentinel.detector.frames import HEIGHT, WIDTH
from sentinel.detector.inference import CoreMLBackend, InferenceBackend, model_path
from sentinel.detector.snapshots import ZONE_COLOR
from sentinel.detector.zones import anchor, zone_of
from sentinel.gateway.go2rtc import local_url

IN_ZONE = (80, 220, 80)  # BGR green: would be watched by the dwell rules
OUTSIDE = (170, 170, 170)  # gray: ignored
PERSON_COLOR = (230, 160, 60)  # blue


def grab_command(camera_id: str, width: int = WIDTH, height: int = HEIGHT) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
        "-i", local_url(camera_id), "-frames:v", "1", "-vf", f"scale={width}:{height}",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip


def grab_frame(camera_id: str) -> np.ndarray:
    result = subprocess.run(grab_command(camera_id), capture_output=True, timeout=20, check=False)
    size = WIDTH * HEIGHT * 3
    if result.returncode != 0 or len(result.stdout) < size:
        problem = result.stderr.decode(errors="replace").strip() or "no frame"
        raise RuntimeError(f"could not read {local_url(camera_id)}: {problem}")
    return np.frombuffer(result.stdout[:size], dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)


def _label(canvas: np.ndarray, text: str, origin: tuple[int, int], color) -> None:
    x, y = origin
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(canvas, (x, y - h - 8), (x + w + 8, y), color, thickness=-1)
    cv2.putText(canvas, text, (x + 4, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)


def draw_preview(image: np.ndarray, camera: Camera, detections: list[Detection]) -> np.ndarray:
    canvas = image.copy()
    height, width = canvas.shape[:2]
    for zone in camera.zones:
        points = np.array(
            [[round(x * width), round(y * height)] for x, y in zone.polygon], dtype=np.int32
        )
        cv2.polylines(canvas, [points], isClosed=True, color=ZONE_COLOR, thickness=3)
        top_left = points[np.argmin(points.sum(axis=1))]
        # Zone ids are ASCII; names may have accents, which OpenCV's font cannot draw.
        _label(canvas, zone.id, (int(top_left[0]), int(top_left[1])), ZONE_COLOR)
    for detection in detections:
        x1, y1, x2, y2 = (round(v) for v in detection.box)
        if detection.cls == PERSON:
            color = PERSON_COLOR
        else:
            color = IN_ZONE if zone_of(detection.box, camera.zones, width, height) else OUTSIDE
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness=2)
        ax, ay = anchor(detection.box, width, height)
        cv2.circle(canvas, (round(ax * width), round(ay * height)), 6, color, thickness=-1)
        _label(canvas, f"{detection.cls} {detection.confidence:.2f}", (x1, max(y1, 24)), color)
    return canvas


def preview(
    camera: Camera,
    backend: InferenceBackend,
    output: Path,
    grab: Callable[[str], np.ndarray] = grab_frame,
) -> Path:
    image = grab(camera.id)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), draw_preview(image, camera, backend.detect(image)))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.detector.zone_preview")
    parser.add_argument("--config", type=Path, default=config_module.DEFAULT_PATH)
    parser.add_argument("--camera", required=True)
    args = parser.parse_args(argv)
    try:
        config = config_module.load(args.config)
        camera = config.camera(args.camera)
        backend = CoreMLBackend(model_path(config.model.name), config.model.min_confidence)
        output = preview(camera, backend, Path("docs") / f"zone-preview-{camera.id}.png")
    except KeyError:
        print(f"camera {args.camera!r} is not in {args.config}", file=sys.stderr)
        return 1
    except (config_module.ConfigError, FileNotFoundError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"saved {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
