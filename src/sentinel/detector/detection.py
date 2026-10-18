"""What the inference backend finds in one frame."""

from __future__ import annotations

from dataclasses import dataclass

CAR = "car"
MOTORCYCLE = "motorcycle"
PERSON = "person"
CLASSES = (CAR, MOTORCYCLE, PERSON)
VEHICLES = (CAR, MOTORCYCLE)

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels


@dataclass(frozen=True)
class Detection:
    cls: str  # car, motorcycle or person
    confidence: float
    box: Box


def area(box: Box) -> float:
    x1, y1, x2, y2 = box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def intersection(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
