"""Inference backends: a frame in, detections (class, confidence, box) out.

The rest of the detector only knows ``InferenceBackend``. This module has the
CoreML backend (an Ultralytics YOLO model exported with NMS by ``make setup``)
and a fake backend that replays scripted detections for tests. Another runtime
(OpenVINO on a mini PC) is one more class here.

Only cars, motorcycles and people are kept: COCO's ``truck`` and ``bus`` count
as cars, and detections below the minimum confidence are dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from sentinel.detector.detection import CAR, MOTORCYCLE, PERSON, Box, Detection, area, intersection

# COCO class names as exported by Ultralytics -> Sentinel classes.
COCO_CLASSES = {"car": CAR, "truck": CAR, "bus": CAR, "motorcycle": MOTORCYCLE, "person": PERSON}
MERGE_IOU = 0.7  # one vehicle seen as both "car" and "truck" becomes one car
MODELS_DIR = Path(__file__).resolve().parents[3] / "models"


class InferenceBackend(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detections in a BGR frame, boxes in the frame's pixels."""
        ...


def model_path(name: str, models_dir: Path = MODELS_DIR) -> Path:
    return models_dir / f"{name}.mlpackage"


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def keep(
    raw: Iterable[tuple[str, float, Box]],
    min_confidence: float,
    merge_iou: float = MERGE_IOU,
) -> list[Detection]:
    """Map COCO names to Sentinel classes, drop the rest and weak detections, and
    merge boxes that became the same class (car, truck and bus) and overlap."""
    candidates = sorted(
        (
            Detection(COCO_CLASSES[name], confidence, box)
            for name, confidence, box in raw
            if name in COCO_CLASSES and confidence >= min_confidence
        ),
        key=lambda d: d.confidence,
        reverse=True,
    )
    kept: list[Detection] = []
    for detection in candidates:
        if not any(k.cls == detection.cls and iou(k.box, detection.box) > merge_iou for k in kept):
            kept.append(detection)
    return kept


class FakeBackend:
    """Replays scripted detections: one list per call, then empty lists.

    ``script`` may also be a function of the call number.
    """

    def __init__(
        self,
        script: Sequence[list[Detection]] | Callable[[int], list[Detection]],
    ):
        self.script = script
        self.calls = 0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        index = self.calls
        self.calls += 1
        if callable(self.script):
            return list(self.script(index))
        return list(self.script[index]) if index < len(self.script) else []


class CoreMLBackend:
    """An Ultralytics YOLO model exported to CoreML with NMS (inputs ``image``,
    ``iouThreshold``, ``confidenceThreshold``; outputs ``confidence`` N x 80 and
    ``coordinates`` N x 4, normalized center x, center y, width, height)."""

    def __init__(self, path: Path, min_confidence: float = 0.4, iou_threshold: float = 0.7):
        import coremltools as ct  # imported here so tests never need it

        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `make setup` to export the model")
        self.model = ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.ALL)
        spec = self.model.get_spec()
        image = next(i for i in spec.description.input if i.type.HasField("imageType"))
        self.input_name = image.name
        self.width = image.type.imageType.width
        self.height = image.type.imageType.height
        names = eval_names(spec.description.metadata.userDefined.get("names", "{}"))
        self.names = [names[i] for i in sorted(names)]
        self.min_confidence = min_confidence
        self.iou_threshold = iou_threshold

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import cv2
        from PIL import Image

        frame_h, frame_w = frame.shape[:2]
        fit = Letterbox.fit(frame_w, frame_h, self.width, self.height)
        canvas = np.full((self.height, self.width, 3), 114, dtype=np.uint8)
        resized = cv2.resize(frame, (fit.new_w, fit.new_h), interpolation=cv2.INTER_LINEAR)
        canvas[fit.pad_y : fit.pad_y + fit.new_h, fit.pad_x : fit.pad_x + fit.new_w] = cv2.cvtColor(
            resized, cv2.COLOR_BGR2RGB
        )
        output = self.model.predict(
            {
                self.input_name: Image.fromarray(canvas),
                "iouThreshold": self.iou_threshold,
                # Below the minimum so a weak "truck" can still be merged into a car.
                "confidenceThreshold": min(0.25, self.min_confidence),
            }
        )
        raw = []
        for scores, xywh in zip(output["confidence"], output["coordinates"], strict=True):
            index = int(np.argmax(scores))
            raw.append((self.names[index], float(scores[index]), fit.to_frame(*xywh)))
        return keep(raw, self.min_confidence)


@dataclass(frozen=True)
class Letterbox:
    """A frame scaled to fit the model input, centered, padded with gray."""

    frame_w: int
    frame_h: int
    input_w: int
    input_h: int
    scale: float
    new_w: int
    new_h: int
    pad_x: int
    pad_y: int

    @classmethod
    def fit(cls, frame_w: int, frame_h: int, input_w: int, input_h: int) -> Letterbox:
        scale = min(input_w / frame_w, input_h / frame_h)
        new_w, new_h = round(frame_w * scale), round(frame_h * scale)
        return cls(
            frame_w,
            frame_h,
            input_w,
            input_h,
            scale,
            new_w,
            new_h,
            (input_w - new_w) // 2,
            (input_h - new_h) // 2,
        )

    def to_frame(self, cx: float, cy: float, w: float, h: float) -> Box:
        """A normalized center/size box of the model input -> pixels of the frame."""

        def x(value: float) -> float:
            return float(np.clip((value * self.input_w - self.pad_x) / self.scale, 0, self.frame_w))

        def y(value: float) -> float:
            return float(np.clip((value * self.input_h - self.pad_y) / self.scale, 0, self.frame_h))

        return (x(cx - w / 2), y(cy - h / 2), x(cx + w / 2), y(cy + h / 2))


def eval_names(text: str) -> dict[int, str]:
    """Parse the ``names`` metadata Ultralytics writes (a Python dict literal)."""
    import ast

    value = ast.literal_eval(text)
    if not isinstance(value, dict):
        raise ValueError("model metadata has no class names")
    return {int(k): str(v) for k, v in value.items()}
