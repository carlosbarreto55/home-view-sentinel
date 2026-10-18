"""Zones: polygons in normalized coordinates of the 16:9 frame.

An object is in a zone when the bottom center of its box (where it touches the
ground) is inside the polygon.
"""

from __future__ import annotations

from collections.abc import Sequence

from sentinel.config import Zone
from sentinel.detector.detection import Box

Point = tuple[float, float]


def anchor(box: Box, width: int, height: int) -> Point:
    """Bottom center of a pixel box, normalized to 0..1."""
    x1, _, x2, y2 = box
    return ((x1 + x2) / 2 / width, y2 / height)


def contains(polygon: Sequence[Point], point: Point) -> bool:
    """Ray casting; points exactly on an edge may fall either way."""
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def zone_of(box: Box, zones: Sequence[Zone], width: int, height: int) -> Zone | None:
    """The first zone containing the box's anchor, or None."""
    point = anchor(box, width, height)
    for zone in zones:
        if contains(zone.polygon, point):
            return zone
    return None
