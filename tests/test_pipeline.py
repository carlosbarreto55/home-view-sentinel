import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from sentinel import config
from sentinel.bus import client as bus
from sentinel.bus.events import DwellEvent
from sentinel.detector.detection import Detection
from sentinel.detector.frames import Frame
from sentinel.detector.inference import FakeBackend
from sentinel.detector.pipeline import CameraPipeline, Detector

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"
FPS = 5
START = datetime(2026, 10, 18, 15, 4, 0, tzinfo=UTC)
MOTO = (560.0, 520.0, 700.0, 680.0)  # bottom center (630, 680): inside "Calçada"
RIDER = (590.0, 420.0, 670.0, 660.0)
FAR = (300.0, 200.0, 500.0, 300.0)  # far lane, outside every zone
IMAGE = np.zeros((720, 1280, 3), dtype=np.uint8)


def cfg() -> config.Config:
    return config.load(EXAMPLE)


def frame(i: int, camera_id="garagem") -> Frame:
    t = i / FPS
    return Frame(camera_id, IMAGE, 1000 + t, START + timedelta(seconds=t))


def motorcyclist(n):
    """Frames 0-249 (50 s): a motorcyclist waiting; then gone."""
    if n < 250:
        return [Detection("motorcycle", 0.81, MOTO), Detection("person", 0.7, RIDER)]
    return []


def run(pipeline, frames):
    events = []
    for i in frames:
        events.extend(pipeline.process(frame(i)))
    return events


def test_a_motorcyclist_waiting_produces_a_start_and_an_end(tmp_path):
    c = cfg()
    pipeline = CameraPipeline(c, c.camera("garagem"), FakeBackend(motorcyclist), tmp_path)
    events = run(pipeline, range(350))
    assert [e.type for e in events] == ["dwell.started", "dwell.ended"]
    start, end = events
    assert start.object.vehicle_class == "motorcycle"
    assert start.object.with_rider is True
    assert start.object.track_id == "garagem-1"
    assert start.zone.name == "Calçada"
    assert start.camera.channel == 1
    assert start.seconds == 30
    assert (start.occurred_at - start.since).total_seconds() == pytest.approx(30, abs=0.3)
    assert (tmp_path / start.snapshot.file).is_file()
    assert start.snapshot.file.startswith("garagem/2026-10-18T15-04-")
    json.loads(start.to_json())  # valid against the contract
    assert end.started_event_id == start.event_id
    assert end.seconds == pytest.approx(50, abs=1)
    assert (end.occurred_at - START).total_seconds() == pytest.approx(60, abs=0.5)
    json.loads(end.to_json())


def test_rider_is_not_tracked_on_its_own(tmp_path):
    c = cfg()
    pipeline = CameraPipeline(c, c.camera("garagem"), FakeBackend(motorcyclist), tmp_path)
    run(pipeline, range(20))
    assert len(pipeline.tracker._states) == 1


def test_bicycles_dogs_and_far_traffic_produce_nothing(tmp_path):
    c = cfg()
    script = lambda n: [Detection("car", 0.9, FAR)]  # noqa: E731 - parked on the far lane
    pipeline = CameraPipeline(c, c.camera("garagem"), FakeBackend(script), tmp_path)
    assert run(pipeline, range(1000)) == []


def test_tick_ends_an_alerted_vehicle_when_the_stream_is_down(tmp_path):
    c = cfg()
    pipeline = CameraPipeline(c, c.camera("garagem"), FakeBackend(motorcyclist), tmp_path)
    run(pipeline, range(200))  # alerted at 30 s, still there at 40 s
    pipeline.reset()
    assert pipeline.tick(1000 + 40 + 5) == []
    [end] = pipeline.tick(1000 + 40 + 11)
    assert end.type == "dwell.ended"
    assert end.seconds == pytest.approx(40, abs=1)


def test_tick_does_nothing_while_frames_keep_coming(tmp_path):
    c = cfg()
    pipeline = CameraPipeline(c, c.camera("garagem"), FakeBackend(motorcyclist), tmp_path)
    run(pipeline, range(10))
    assert pipeline.tick(1000 + 1.9 + 0.5) == []
    assert pipeline.dwell.phase("garagem-1") is not None


class FakePublisher:
    def __init__(self):
        self.events: list[DwellEvent] = []
        self.statuses = []

    def publish_event(self, event):
        self.events.append(event)

    def set_status(self, state, detail=None, component=None):
        self.statuses.append((component, state, detail))


class FakeReader:
    def __init__(self, camera, on_frame, on_state):
        self.camera_id = camera.id
        self.on_frame = on_frame
        self.on_state = on_state

    def run(self, stop):
        stop.wait()


def two_camera_config():
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["cameras"].append(dict(data["cameras"][0], id="rua", name="Rua", channel=3))
    return config.from_dict(data)


def test_detector_publishes_events_and_camera_statuses(tmp_path):
    publisher = FakePublisher()
    clock = {"t": 1000.0}
    detector = Detector(
        two_camera_config(),
        FakeBackend(lambda n: motorcyclist(n // 2)),  # both cameras share the backend
        publisher,
        tmp_path,
        reader_factory=FakeReader,
        monotonic=lambda: clock["t"],
        now=lambda: START,
    )
    garagem, rua = detector.readers
    garagem.on_state("online", None)
    for i in range(200):
        clock["t"] = 1000 + i / FPS
        garagem.on_frame(frame(i, "garagem"))
        rua.on_frame(frame(i, "rua"))
        detector.step(timeout=0)
    assert ("camera-garagem", "online", None) in publisher.statuses
    assert sorted(e.camera.id for e in publisher.events) == ["garagem", "rua"]
    assert all(e.type == "dwell.started" for e in publisher.events)


def test_a_lost_camera_is_reset_without_stopping_the_other(tmp_path):
    publisher = FakePublisher()
    detector = Detector(
        two_camera_config(),
        FakeBackend(lambda n: []),
        publisher,
        tmp_path,
        reader_factory=FakeReader,
        now=lambda: START,
    )
    garagem, rua = detector.readers
    rua.on_state(bus.RECONNECTING, "no frame for 10 s; retrying in 5 s")
    garagem.on_frame(frame(0, "garagem"))
    detector.step(timeout=0)
    assert (
        "camera-rua",
        "reconnecting",
        "no frame for 10 s; retrying in 5 s",
    ) in publisher.statuses
    assert detector.pipelines["garagem"]._last_t == 1000


def test_old_snapshots_are_deleted_once_a_day(tmp_path):
    old = tmp_path / "garagem" / "2026-09-01T10-00-00Z-garagem-1.jpg"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    now = {"t": START}
    detector = Detector(
        cfg(),
        FakeBackend([]),
        FakePublisher(),
        tmp_path,
        reader_factory=FakeReader,
        now=lambda: now["t"],
    )
    detector.step(timeout=0)
    assert not old.exists()
    old.write_bytes(b"x")
    now["t"] = START + timedelta(hours=23)
    detector.step(timeout=0)
    assert old.exists()
    now["t"] = START + timedelta(hours=24)
    detector.step(timeout=0)
    assert not old.exists()
