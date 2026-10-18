import pytest

from sentinel.config import Zone
from sentinel.detector.riders import Vehicle
from sentinel.detector.tracking import VehicleTracker
from sentinel.detector.zones import anchor, contains, zone_of

FPS = 5
CALCADA = Zone("calcada", "Calçada", ((0.05, 0.62), (0.95, 0.58), (0.98, 0.95), (0.02, 0.97)))
RUA = Zone("rua", "Rua", ((0.0, 0.3), (1.0, 0.3), (1.0, 0.55), (0.0, 0.55)))
CAR_BOX = (400.0, 380.0, 700.0, 600.0)  # bottom center (550, 600) -> (0.43, 0.83)


def car(box=CAR_BOX, confidence=0.8):
    return Vehicle("car", confidence, box)


def feed(tracker, frames, start=0.0):
    """Feed a list of per-frame vehicle lists at FPS; return per-frame track lists."""
    return [tracker.update(vehicles, start + i / FPS) for i, vehicles in enumerate(frames)]


# --- zones ----------------------------------------------------------------------


def test_anchor_is_bottom_center_normalized():
    assert anchor(CAR_BOX, 1280, 720) == pytest.approx((550 / 1280, 600 / 720))


@pytest.mark.parametrize(
    ("point", "inside"),
    [
        ((0.5, 0.8), True),
        ((0.5, 0.5), False),
        ((0.01, 0.8), False),
        ((0.97, 0.9), True),
        ((0.5, 0.99), False),
    ],
)
def test_contains(point, inside):
    assert contains(CALCADA.polygon, point) is inside


def test_concave_polygon():
    # An L shape: the notch at the top right is outside.
    shape = ((0, 0), (0.5, 0), (0.5, 0.5), (1, 0.5), (1, 1), (0, 1))
    assert contains(shape, (0.25, 0.25))
    assert not contains(shape, (0.75, 0.25))
    assert contains(shape, (0.75, 0.75))


def test_object_is_placed_by_where_it_touches_the_ground():
    # The box reaches up into the far lane, but its bottom is on the sidewalk.
    tall = (500.0, 200.0, 600.0, 620.0)
    assert zone_of(tall, [RUA, CALCADA], 1280, 720) == CALCADA


def test_traffic_on_the_far_lane_is_outside_the_sidewalk_zone():
    far = (300.0, 200.0, 500.0, 300.0)  # bottom at y=0.42
    assert zone_of(far, [CALCADA], 1280, 720) is None
    assert zone_of(far, [CALCADA, RUA], 1280, 720) == RUA


# --- tracking -------------------------------------------------------------------


def test_track_ids_are_named_after_the_camera():
    tracker = VehicleTracker("garagem", FPS)
    tracks = feed(tracker, [[car()]] * 5)
    ids = {t.track_id for frame in tracks for t in frame}
    assert ids == {"garagem-1"}


def test_parked_car_keeps_its_identifier_through_a_2_s_gap():
    tracker = VehicleTracker("garagem", FPS)
    frames = [[car()]] * 10 + [[]] * (2 * FPS) + [[car()]] * 5
    tracks = feed(tracker, frames)
    before = {t.track_id for t in tracks[9]}
    after = {t.track_id for frame in tracks[-5:] for t in frame}
    assert before == after == {"garagem-1"}
    assert all(frame == [] for frame in tracks[10:20])


def test_a_gap_longer_than_3_s_gives_a_new_identifier():
    tracker = VehicleTracker("garagem", FPS)
    tracks = feed(tracker, [[car()]] * 10 + [[]] * (5 * FPS) + [[car()]] * 5)
    assert {t.track_id for t in tracks[-1]} == {"garagem-2"}


def test_two_vehicles_get_two_identifiers():
    tracker = VehicleTracker("garagem", FPS)
    other = (900.0, 400.0, 1100.0, 560.0)
    tracks = feed(tracker, [[car(), Vehicle("motorcycle", 0.7, other)]] * 5)
    last = {t.track_id: t.cls for t in tracks[-1]}
    assert sorted(last.values()) == ["car", "motorcycle"]
    assert len(last) == 2


def test_reset_forgets_tracks_but_never_reuses_an_identifier():
    tracker = VehicleTracker("garagem", FPS)
    feed(tracker, [[car()]] * 5)
    tracker.reset()
    tracks = feed(tracker, [[car()]] * 5, start=10)
    assert {t.track_id for t in tracks[-1]} == {"garagem-2"}


def test_track_class_is_the_most_frequent_one():
    tracker = VehicleTracker("garagem", FPS)
    frames = [[car()]] * 6 + [[Vehicle("motorcycle", 0.5, CAR_BOX)]] * 2
    tracks = feed(tracker, frames)
    assert tracks[-1][0].cls == "car"


def test_motorcycle_with_rider_through_the_tracker():
    tracker = VehicleTracker("garagem", FPS)
    moto = (500.0, 400.0, 640.0, 560.0)
    ridden = [[Vehicle("motorcycle", 0.8, moto, rider=True)]] * 10
    alone = [[Vehicle("motorcycle", 0.8, moto, rider=False)]] * 10
    tracks = feed(tracker, ridden + alone)
    assert tracks[9][0].with_rider is True
    assert tracks[-1][0].with_rider is False
    assert tracks[-1][0].track_id == tracks[9][0].track_id
