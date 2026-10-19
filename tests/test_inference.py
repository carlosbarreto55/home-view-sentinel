import numpy as np
import pytest

from sentinel.detector.detection import Detection
from sentinel.detector.inference import FakeBackend, Letterbox, eval_names, keep, model_path


def test_classes_are_mapped_and_others_ignored():
    raw = [
        ("car", 0.9, (0, 0, 10, 10)),
        ("truck", 0.8, (100, 0, 200, 50)),
        ("bus", 0.7, (300, 0, 500, 100)),
        ("motorcycle", 0.6, (600, 0, 650, 40)),
        ("person", 0.5, (700, 0, 720, 60)),
        ("bicycle", 0.9, (800, 0, 850, 40)),
        ("dog", 0.9, (900, 0, 950, 40)),
    ]
    assert [(d.cls, d.confidence) for d in keep(raw, 0.4)] == [
        ("car", 0.9),
        ("car", 0.8),
        ("car", 0.7),
        ("motorcycle", 0.6),
        ("person", 0.5),
    ]


def test_detections_below_the_minimum_confidence_are_ignored():
    raw = [("car", 0.39, (0, 0, 10, 10)), ("motorcycle", 0.4, (20, 0, 30, 10))]
    assert [d.cls for d in keep(raw, 0.4)] == ["motorcycle"]


def test_one_vehicle_seen_as_car_and_truck_becomes_one_car():
    raw = [("truck", 0.55, (100, 100, 400, 300)), ("car", 0.8, (105, 102, 398, 305))]
    assert keep(raw, 0.4) == [Detection("car", 0.8, (105, 102, 398, 305))]


def test_rider_and_motorcycle_overlapping_are_both_kept():
    raw = [("motorcycle", 0.8, (500, 400, 640, 560)), ("person", 0.7, (510, 380, 630, 560))]
    assert sorted(d.cls for d in keep(raw, 0.4)) == ["motorcycle", "person"]


def test_letterbox_of_a_16_9_frame_into_the_model_input():
    fit = Letterbox.fit(1280, 720, 640, 384)
    assert (fit.scale, fit.new_w, fit.new_h, fit.pad_x, fit.pad_y) == (0.5, 640, 360, 0, 12)
    # A box covering the middle of the picture area, in normalized model coordinates.
    box = fit.to_frame(0.5, 0.5, 0.25, 180 / 384)
    assert box == pytest.approx((480, 180, 800, 540))


def test_letterbox_clips_to_the_frame():
    fit = Letterbox.fit(1280, 720, 640, 384)
    assert fit.to_frame(0.0, 0.0, 0.2, 0.2) == pytest.approx(
        (0, 0, 128, 52.8)
    )  # (0.1 * 384 - 12) / 0.5


def test_fake_backend_replays_its_script():
    car = Detection("car", 0.9, (0, 0, 10, 10))
    backend = FakeBackend([[car], []])
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert [backend.detect(frame) for _ in range(3)] == [[car], [], []]
    by_call = FakeBackend(lambda n: [car] if n % 2 else [])
    assert [len(by_call.detect(frame)) for _ in range(4)] == [0, 1, 0, 1]


def test_names_metadata():
    assert eval_names("{0: 'person', 2: 'car'}") == {0: "person", 2: "car"}


@pytest.mark.skipif(
    not model_path("yolo11s").exists(), reason="run `make setup` to export the model"
)
def test_coreml_backend_runs_on_a_frame():
    from sentinel.detector.inference import CoreMLBackend

    backend = CoreMLBackend(model_path("yolo11s"))
    assert (backend.width, backend.height) == (640, 384)
    assert backend.detect(np.zeros((720, 1280, 3), dtype=np.uint8)) == []
