"""Attach riders to motorcycles.

A person whose box's lower half overlaps a motorcycle's box by at least 30% of
the person box's area rides that motorcycle (the one it overlaps most). Riders
are not tracked on their own. A tracked motorcycle is "with rider" when a rider
was attached in at least half of its last 10 frames.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sentinel.detector.detection import (
    MOTORCYCLE,
    PERSON,
    VEHICLES,
    Box,
    Detection,
    area,
    intersection,
)

MIN_OVERLAP = 0.30
HISTORY_FRAMES = 10


@dataclass(frozen=True)
class Vehicle:
    """A car or motorcycle in one frame; ``rider`` is whether a person sits on it now."""

    cls: str
    confidence: float
    box: Box
    rider: bool = False


def lower_half(box: Box) -> Box:
    x1, y1, x2, y2 = box
    return (x1, (y1 + y2) / 2, x2, y2)


def rider_overlap(person: Box, motorcycle: Box) -> float:
    """Overlap of the person's lower half with the motorcycle, as a fraction of the person box."""
    person_area = area(person)
    if person_area == 0:
        return 0.0
    return intersection(lower_half(person), motorcycle) / person_area


def attach_riders(detections: list[Detection], min_overlap: float = MIN_OVERLAP) -> list[Vehicle]:
    """The frame's vehicles, motorcycles marked with a rider; persons are dropped."""
    motorcycles = [i for i, d in enumerate(detections) if d.cls == MOTORCYCLE]
    ridden: set[int] = set()
    for person in (d for d in detections if d.cls == PERSON):
        best, best_overlap = None, min_overlap
        for i in motorcycles:
            overlap = rider_overlap(person.box, detections[i].box)
            if overlap >= best_overlap:
                best, best_overlap = i, overlap
        if best is not None:
            ridden.add(best)
    return [
        Vehicle(d.cls, d.confidence, d.box, rider=i in ridden)
        for i, d in enumerate(detections)
        if d.cls in VEHICLES
    ]


class RiderHistory:
    """Whether a tracked motorcycle had a rider in at least half of its last frames."""

    def __init__(self, frames: int = HISTORY_FRAMES):
        self._seen: deque[bool] = deque(maxlen=frames)

    def add(self, rider: bool) -> None:
        self._seen.append(rider)

    @property
    def with_rider(self) -> bool:
        return bool(self._seen) and 2 * sum(self._seen) >= len(self._seen)
