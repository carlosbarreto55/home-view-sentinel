"""Event snapshots: the 1280x720 frame with the vehicle's box and the zone drawn.

Saved as ``<camera>/<UTC time>-<track>.jpg`` under the snapshots folder, e.g.
``garagem/2026-10-18T15-04-37Z-garagem-42.jpg``; events reference that relative
path. Snapshots older than the retention (14 days) are deleted once a day.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from sentinel.bus.events import SnapshotRef
from sentinel.detector.detection import Box

log = logging.getLogger(__name__)

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)-.+\.jpg$")
_TIME_FORMAT = "%Y-%m-%dT%H-%M-%SZ"
BOX_COLOR = (0, 0, 255)  # BGR red
ZONE_COLOR = (0, 215, 255)  # BGR amber


def relative_path(camera_id: str, moment: datetime, track_id: str) -> str:
    return f"{camera_id}/{moment.astimezone(UTC).strftime(_TIME_FORMAT)}-{track_id}.jpg"


def draw(image: np.ndarray, box: Box, polygon: Sequence[tuple[float, float]]) -> np.ndarray:
    """A copy of the frame with the zone (normalized polygon) and the box drawn."""
    canvas = image.copy()
    height, width = canvas.shape[:2]
    points = np.array([[round(x * width), round(y * height)] for x, y in polygon], dtype=np.int32)
    overlay = canvas.copy()
    cv2.fillPoly(overlay, [points], ZONE_COLOR)
    cv2.addWeighted(overlay, 0.15, canvas, 0.85, 0, dst=canvas)
    cv2.polylines(canvas, [points], isClosed=True, color=ZONE_COLOR, thickness=2)
    x1, y1, x2, y2 = (round(v) for v in box)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), BOX_COLOR, thickness=3)
    return canvas


def save(
    root: Path,
    camera_id: str,
    moment: datetime,
    track_id: str,
    image: np.ndarray,
    box: Box,
    polygon: Sequence[tuple[float, float]],
    quality: int = 85,
) -> SnapshotRef:
    """Write the snapshot (atomically, so readers never see half a file)."""
    relative = relative_path(camera_id, moment, track_id)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(
        ".jpg", draw(image, box, polygon), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise OSError(f"could not encode {relative}")
    temporary = path.with_suffix(".jpg.tmp")
    temporary.write_bytes(encoded.tobytes())
    os.replace(temporary, path)
    height, width = image.shape[:2]
    return SnapshotRef(file=relative, width=width, height=height)


def taken_at(path: Path) -> datetime | None:
    match = _NAME.match(path.name)
    if not match:
        return None
    return datetime.strptime(match[1], _TIME_FORMAT).replace(tzinfo=UTC)


def delete_old(root: Path, retention_days: int, now: datetime) -> list[Path]:
    """Delete snapshots taken more than ``retention_days`` before ``now``."""
    limit = now - timedelta(days=retention_days)
    deleted = []
    if not root.exists():
        return deleted
    for path in sorted(root.glob("*/*.jpg")):
        moment = taken_at(path)
        if moment is None:
            moment = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if moment < limit:
            path.unlink(missing_ok=True)
            deleted.append(path)
    if deleted:
        log.info("deleted %d snapshots older than %d days", len(deleted), retention_days)
    return deleted
