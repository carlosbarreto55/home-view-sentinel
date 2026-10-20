"""Event snapshots: the 1280x720 frame with the vehicle's box and the zone drawn,
and the event's date and time on the Mac's clock, in the configured time zone,
printed in the top right corner over the DVR's own on-screen clock (which runs
about 20 minutes off).

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
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from sentinel.bus.events import SnapshotRef
from sentinel.detector.detection import Box

log = logging.getLogger(__name__)

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)-.+\.jpg$")
_TIME_FORMAT = "%Y-%m-%dT%H-%M-%SZ"
BOX_COLOR = (0, 0, 255)  # BGR red
ZONE_COLOR = (0, 215, 255)  # BGR amber

# The DVR's clock (x1, y1, x2, y2) on both street cameras' 1280x720 frames,
# measured on 2026-10-20; the time stamp's box always covers it.
DVR_CLOCK = (1055, 25, 1265, 50)
STAMP_FONT = cv2.FONT_HERSHEY_SIMPLEX
STAMP_SCALE = 0.8
STAMP_THICKNESS = 2
STAMP_PADDING = 10
STAMP_RIGHT, STAMP_TOP = 1276, 14
STAMP_TEXT_COLOR = (255, 255, 255)
STAMP_BOX_COLOR = (32, 32, 32)


def relative_path(camera_id: str, moment: datetime, track_id: str) -> str:
    return f"{camera_id}/{moment.astimezone(UTC).strftime(_TIME_FORMAT)}-{track_id}.jpg"


def stamp_text(moment: datetime, timezone: str) -> str:
    """``18/10/2026 20:15:24``: the event's time in the configured time zone."""
    return moment.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M:%S")


def stamp_box(text: str, width: int = 1280) -> tuple[int, int, int, int]:
    """The dark box behind the time stamp, for a frame ``width`` wide
    (positions are for 1280 and scale with the width)."""
    factor = width / 1280
    scale = STAMP_SCALE * factor
    thickness = max(1, round(STAMP_THICKNESS * factor))
    (text_width, text_height), baseline = cv2.getTextSize(text, STAMP_FONT, scale, thickness)
    padding = round(STAMP_PADDING * factor)
    x2, y1 = round(STAMP_RIGHT * factor), round(STAMP_TOP * factor)
    x1 = min(x2 - text_width - 2 * padding, round(DVR_CLOCK[0] * factor) - padding)
    y2 = max(y1 + text_height + baseline + 2 * padding, round(DVR_CLOCK[3] * factor) + padding)
    return x1, y1, x2, y2


def draw_stamp(canvas: np.ndarray, text: str) -> tuple[int, int, int, int]:
    """Print ``text`` white on a dark box in the top right corner; returns the box."""
    width = canvas.shape[1]
    factor = width / 1280
    scale = STAMP_SCALE * factor
    thickness = max(1, round(STAMP_THICKNESS * factor))
    x1, y1, x2, y2 = stamp_box(text, width)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), STAMP_BOX_COLOR, thickness=cv2.FILLED)
    (text_width, text_height), baseline = cv2.getTextSize(text, STAMP_FONT, scale, thickness)
    origin = (
        x1 + (x2 - x1 - text_width) // 2,
        y1 + (y2 - y1 + text_height - baseline) // 2,
    )
    cv2.putText(canvas, text, origin, STAMP_FONT, scale, STAMP_TEXT_COLOR, thickness, cv2.LINE_AA)
    return x1, y1, x2, y2


def draw(
    image: np.ndarray,
    box: Box,
    polygon: Sequence[tuple[float, float]],
    stamp: str | None = None,
) -> np.ndarray:
    """A copy of the frame with the zone (normalized polygon), the box and the
    time stamp drawn."""
    canvas = image.copy()
    height, width = canvas.shape[:2]
    points = np.array([[round(x * width), round(y * height)] for x, y in polygon], dtype=np.int32)
    overlay = canvas.copy()
    cv2.fillPoly(overlay, [points], ZONE_COLOR)
    cv2.addWeighted(overlay, 0.15, canvas, 0.85, 0, dst=canvas)
    cv2.polylines(canvas, [points], isClosed=True, color=ZONE_COLOR, thickness=2)
    x1, y1, x2, y2 = (round(v) for v in box)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), BOX_COLOR, thickness=3)
    if stamp:
        draw_stamp(canvas, stamp)
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
    timezone: str | None = None,
) -> SnapshotRef:
    """Write the snapshot (atomically, so readers never see half a file), with
    the time stamp when a ``timezone`` is given."""
    relative = relative_path(camera_id, moment, track_id)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = stamp_text(moment, timezone) if timezone else None
    ok, encoded = cv2.imencode(
        ".jpg", draw(image, box, polygon, stamp), [cv2.IMWRITE_JPEG_QUALITY, quality]
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
