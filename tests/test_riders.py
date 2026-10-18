from sentinel.detector.detection import Detection
from sentinel.detector.riders import RiderHistory, Vehicle, attach_riders, rider_overlap

MOTO = (500, 400, 640, 560)
RIDER = (530, 300, 610, 520)  # sitting: legs and seat over the motorcycle
WALKER = (660, 330, 720, 560)  # beside the motorcycle, on foot


def moto(box=MOTO):
    return Detection("motorcycle", 0.8, box)


def person(box):
    return Detection("person", 0.7, box)


def test_rider_waiting_on_a_stopped_motorcycle_is_one_object():
    vehicles = attach_riders([moto(), person(RIDER)])
    assert vehicles == [Vehicle("motorcycle", 0.8, MOTO, rider=True)]


def test_person_walking_beside_is_not_a_rider():
    assert rider_overlap(WALKER, MOTO) == 0
    assert attach_riders([moto(), person(WALKER)]) == [Vehicle("motorcycle", 0.8, MOTO)]


def test_overlap_threshold_is_30_percent_of_the_person_box():
    # Person 100x100; lower half 100x50. Overlap 100x30 = 30% -> rider; 100x29 -> not.
    motorcycle = (0, 170, 100, 300)
    assert rider_overlap((0, 100, 100, 200), motorcycle) == 0.30
    assert attach_riders([Detection("motorcycle", 0.9, motorcycle), person((0, 100, 100, 200))])[
        0
    ].rider
    assert not attach_riders([Detection("motorcycle", 0.9, motorcycle), person((0, 99, 100, 199))])[
        0
    ].rider


def test_only_the_lower_half_counts():
    # The person's upper half covers the motorcycle (e.g. someone standing behind it).
    assert rider_overlap((0, 0, 100, 200), (0, 0, 100, 100)) == 0


def test_rider_goes_to_the_motorcycle_it_overlaps_most():
    left, right = (0, 150, 100, 300), (60, 150, 200, 300)
    vehicles = attach_riders([moto(left), moto(right), person((70, 50, 150, 250))])
    assert [v.rider for v in vehicles] == [False, True]


def test_cars_are_kept_and_other_persons_dropped():
    car = Detection("car", 0.9, (0, 0, 300, 200))
    vehicles = attach_riders([car, person((10, 10, 50, 150))])
    assert vehicles == [Vehicle("car", 0.9, (0, 0, 300, 200))]


def test_rider_gets_off_within_ten_frames():
    history = RiderHistory()
    for _ in range(10):
        history.add(True)
    assert history.with_rider
    frames_until_without = 0
    while history.with_rider:
        history.add(False)
        frames_until_without += 1
    assert frames_until_without <= 10


def test_with_rider_is_at_least_half_of_last_ten_frames():
    history = RiderHistory()
    assert not history.with_rider
    for rider in [True, False, True, False, True, False, True, False, True, False]:
        history.add(rider)
    assert history.with_rider  # 5 of 10
    history.add(False)
    assert not history.with_rider  # 4 of 10 (oldest True dropped)


def test_a_missed_rider_detection_does_not_flip_the_state():
    history = RiderHistory()
    for rider in [True] * 6 + [False, False]:
        history.add(rider)
    assert history.with_rider
