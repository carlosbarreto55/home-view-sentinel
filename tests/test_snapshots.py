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


def test_stamp_text_is_the_local_date_and_time():
    moment = datetime(2026, 10, 19, 0, 15, 24, tzinfo=UTC)
    assert snapshots.stamp_text(moment, "Etc/GMT+4") == "18/10/2026 20:15:24"


def test_the_stamp_box_covers_the_dvr_clock():
    x1, y1, x2, y2 = snapshots.stamp_box("18/10/2026 20:15:24")
    cx1, cy1, cx2, cy2 = snapshots.DVR_CLOCK
    assert x1 < cx1 and y1 < cy1 and x2 > cx2 and y2 > cy2
    assert x2 <= 1280 and y1 >= 0
    assert x1 > 640  # the right half only: the street is not there


def test_the_stamp_is_drawn_white_on_a_dark_box():
    canvas = np.full((720, 1280, 3), 230, dtype=np.uint8)  # a bright sky
    x1, y1, x2, y2 = snapshots.draw_stamp(canvas, "18/10/2026 20:15:24")
    inside = canvas[y1 : y2 + 1, x1 : x2 + 1]
    white = (inside.min(axis=2) > 200).sum()
    dark = (inside.max(axis=2) < 60).sum()
    assert white > 500 and dark > inside.shape[0] * inside.shape[1] * 0.6
    cx1, cy1, cx2, cy2 = snapshots.DVR_CLOCK
    clock = canvas[cy1:cy2, cx1:cx2]
    assert (clock.max(axis=2) < 60).mean() > 0.5  # covered by the box (and our text)
    assert (canvas[100:, :] == 230).all() and (canvas[:, : x1 - 1] == 230).all()


def test_save_prints_the_time_when_given_a_timezone(tmp_path):
    moment = datetime(2026, 10, 19, 0, 15, 24, tzinfo=UTC)
    ref = snapshots.save(
        tmp_path, "garagem", moment, "garagem-1", frame(), BOX, POLYGON, timezone="Etc/GMT+4"
    )
    image = cv2.imread(str(tmp_path / ref.file))
    x1, y1, x2, y2 = snapshots.stamp_box("18/10/2026 20:15:24")
    corner = image[y1 : y2 + 1, x1 : x2 + 1]
    assert (corner.min(axis=2) > 200).sum() > 500  # the text
    plain = snapshots.save(tmp_path, "rua", moment, "rua-1", frame(), BOX, POLYGON)
    untouched = cv2.imread(str(tmp_path / plain.file))[y1 : y2 + 1, x1 : x2 + 1]
    assert (untouched.min(axis=2) > 200).sum() == 0


def test_the_stamp_scales_with_a_smaller_frame():
    x1, y1, x2, y2 = snapshots.stamp_box("18/10/2026 20:15:24", width=640)
    assert 320 < x1 < x2 <= 640 and y2 - y1 < 40
