from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from sentinel import config
from sentinel.detector.detection import Detection
from sentinel.detector.frames import Frame
from sentinel.detector.inference import FakeBackend
from sentinel.detector.replay import clock, decode_command, record_command, replay

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"
CLIP_START = datetime(2026, 10, 18, 23, 30, 0, tzinfo=UTC)
FPS = 5
IMAGE = np.zeros((720, 1280, 3), dtype=np.uint8)
CAR = (400.0, 460.0, 760.0, 680.0)  # parked on the sidewalk zone
MOTO = (900.0, 520.0, 1040.0, 680.0)
RIDER = (930.0, 420.0, 1010.0, 660.0)
PASSING = lambda n: (n * 40.0, 500.0, n * 40.0 + 300, 690.0)  # noqa: E731


def clip_frames(seconds):
    for i in range(seconds * FPS):
        t = i / FPS
        yield Frame("garagem", IMAGE, t, CLIP_START + timedelta(seconds=t))


def script(n):
    """A car passes (0-6 s), a motorcyclist waits from 10 s to 55 s, a car parks from 60 s on."""
    detections = []
    if n < 30:
        detections.append(Detection("car", 0.9, PASSING(n)))
    if 50 <= n < 275:
        detections += [Detection("motorcycle", 0.8, MOTO), Detection("person", 0.7, RIDER)]
    if n >= 300:
        detections.append(Detection("car", 0.85, CAR))
    return detections


def test_replaying_scripted_detections_prints_the_expected_events(tmp_path):
    cfg = config.load(EXAMPLE)
    lines = []
    events = replay(
        cfg,
        cfg.camera("garagem"),
        clip_frames(200),
        FakeBackend(script),
        tmp_path,
        CLIP_START,
        out=lines.append,
    )
    assert [(e.type, e.object.vehicle_class) for e in events] == [
        ("dwell.started", "motorcycle"),
        ("dwell.ended", "motorcycle"),
        ("dwell.started", "car"),
        ("dwell.ended", "car"),
    ]
    assert lines[0].startswith("00:00:40  dwell.started")
    assert "motorcycle with rider" in lines[0]
    assert "Calçada  30 s" in lines[0]
    assert lines[1].startswith("00:01:04  dwell.ended")  # last seen at 54.8 s + 10 s
    assert lines[2].startswith("00:03:00  dwell.started")
    assert "car" in lines[2]
    assert lines[3].endswith("(clip ended)")
    assert lines[-1] == "4 events in 00:03:19 of video"
    assert len(list(tmp_path.glob("garagem/*.jpg"))) == 2


def test_clock():
    assert clock(0) == "00:00:00"
    assert clock(3725.9) == "01:02:05"


def test_decode_command_uses_the_sampling_rate():
    command = decode_command(Path("clips/garagem.mp4"), 5)
    assert command[command.index("-vf") + 1] == "fps=5,scale=1280:720"
    assert "clips/garagem.mp4" in command


def test_record_command_copies_the_gateway_stream():
    started = datetime(2026, 10, 18, 23, 30, tzinfo=UTC)
    command = record_command("garagem", 10, Path("clips/garagem.mp4"), started)
    assert "rtsp://127.0.0.1:8554/garagem" in command
    assert command[command.index("-t") + 1] == "600"
    assert command[command.index("-c") + 1] == "copy"
    assert "creation_time=2026-10-18T23:30:00.000000Z" in command


def test_record_command_tags_only_hevc_for_quicktime():
    started = datetime(2026, 10, 18, 23, 30, tzinfo=UTC)
    hevc = record_command("garagem", 10, Path("clips/a.mp4"), started, codec="hevc")
    assert hevc[hevc.index("-tag:v") + 1] == "hvc1"
    for codec in ("h264", None):
        assert "-tag:v" not in record_command("garagem", 10, Path("a.mp4"), started, codec)
