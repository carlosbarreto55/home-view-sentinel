import os
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np

from sentinel.detector import snapshots

MOMENT = datetime(2026, 10, 18, 15, 4, 37, tzinfo=UTC)
POLYGON = ((0.05, 0.62), (0.95, 0.58), (0.98, 0.95), (0.02, 0.97))
BOX = (500.0, 400.0, 700.0, 560.0)


def frame():
    return np.full((720, 1280, 3), 60, dtype=np.uint8)


def test_name_follows_camera_time_and_track():
    assert snapshots.relative_path("garagem", MOMENT, "garagem-42") == (
        "garagem/2026-10-18T15-04-37Z-garagem-42.jpg"
    )


def test_name_uses_utc():
    from zoneinfo import ZoneInfo

    local = MOMENT.astimezone(ZoneInfo("Etc/GMT+4"))
    assert snapshots.relative_path("garagem", local, "garagem-42").startswith(
        "garagem/2026-10-18T15-04-37Z"
    )


def test_save_writes_a_jpeg_with_the_box_drawn(tmp_path):
    ref = snapshots.save(tmp_path, "garagem", MOMENT, "garagem-42", frame(), BOX, POLYGON)
    assert ref.file == "garagem/2026-10-18T15-04-37Z-garagem-42.jpg"
    assert (ref.width, ref.height) == (1280, 720)
    image = cv2.imread(str(tmp_path / ref.file))
    assert image.shape == (720, 1280, 3)
    b, g, r = (int(v) for v in image[400, 600])  # top edge of the box
    assert r > 180 and g < 90 and b < 90
    assert list(tmp_path.glob("**/*.tmp")) == []


def test_drawing_leaves_the_original_frame_untouched():
    original = frame()
    snapshots.draw(original, BOX, POLYGON)
    assert (original == 60).all()


def test_delete_old_keeps_the_retention(tmp_path):
    now = MOMENT
    for days in (0, 13, 14.5, 30):
        moment = now - timedelta(days=days)
        snapshots.save(tmp_path, "garagem", moment, "garagem-1", frame(), BOX, POLYGON)
    deleted = snapshots.delete_old(tmp_path, 14, now)
    assert sorted(p.name[:10] for p in deleted) == ["2026-09-18", "2026-10-04"]
    remaining = sorted(p.name[:10] for p in tmp_path.glob("*/*.jpg"))
    assert remaining == ["2026-10-05", "2026-10-18"]


def test_delete_old_uses_the_file_time_for_other_names(tmp_path):
    (tmp_path / "garagem").mkdir()
    old = tmp_path / "garagem" / "manual.jpg"
    old.write_bytes(b"x")
    long_ago = (MOMENT - timedelta(days=20)).timestamp()
    os.utime(old, (long_ago, long_ago))
    assert snapshots.delete_old(tmp_path, 14, MOMENT) == [old]


def test_delete_old_without_folder(tmp_path):
    assert snapshots.delete_old(tmp_path / "missing", 14, MOMENT) == []
