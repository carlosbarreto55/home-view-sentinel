from pathlib import Path

import cv2
import numpy as np

from sentinel import config
from sentinel.detector.detection import Detection
from sentinel.detector.inference import FakeBackend
from sentinel.detector.snapshots import ZONE_COLOR
from sentinel.detector.zone_preview import IN_ZONE, OUTSIDE, draw_preview, grab_command, preview

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"
PARKED = Detection("car", 0.91, (400.0, 460.0, 760.0, 680.0))  # in "calcada"
FAR = Detection("car", 0.66, (300.0, 200.0, 500.0, 300.0))  # far lane


def garagem():
    return config.load(EXAMPLE).camera("garagem")


def black():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def test_zones_and_detections_are_drawn_by_whether_they_count():
    canvas = draw_preview(black(), garagem(), [PARKED, FAR])
    # The zone's left edge runs from (64, 446) to (26, 698) px: x is about 41 at y 600.
    assert tuple(int(v) for v in canvas[600, 41]) == ZONE_COLOR
    assert tuple(int(v) for v in canvas[570, 760]) == IN_ZONE  # right edge of the parked car
    assert tuple(int(v) for v in canvas[250, 500]) == OUTSIDE  # right edge of the far car


def test_preview_saves_a_png_of_the_live_frame(tmp_path):
    output = tmp_path / "docs" / "zone-preview-garagem.png"
    grabbed = []

    def grab(camera_id):
        grabbed.append(camera_id)
        return black()

    assert preview(garagem(), FakeBackend([[PARKED]]), output, grab=grab) == output
    assert grabbed == ["garagem"]
    assert cv2.imread(str(output)).shape == (720, 1280, 3)


def test_grab_command_reads_one_frame_from_the_gateway():
    command = grab_command("garagem")
    assert "rtsp://127.0.0.1:8554/garagem" in command
    assert command[command.index("-frames:v") + 1] == "1"
