"""Every scenario of the dwell-alerts spec that the state machine decides.

Frames come at 5 fps; positions are box centers in the 1280x720 frame.
2% of the diagonal is about 29 px.
"""

from __future__ import annotations

import itertools

import pytest

from sentinel import config
from sentinel.config import Zone
from sentinel.detector.dwell import (
    DwellEnd,
    DwellMonitor,
    DwellRules,
    DwellStart,
    Observation,
    Phase,
)
from sentinel.detector.tracking import TrackedVehicle

FPS = 5
CALCADA = Zone("calcada", "Calçada", ((0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)))
RULES = DwellRules(thresholds={"motorcycle": 30, "car": 120})


def box_at(x, y, w=200, h=120):
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def vehicle(track_id, x, y, cls="car", rider=False):
    return TrackedVehicle(
        track_id, cls, 0.8, box_at(x, y), with_rider=rider and cls == "motorcycle"
    )


def in_zone(v):
    return Observation(v, CALCADA)


class Sim:
    """Runs a monitor over a timeline, frame by frame."""

    def __init__(self, rules=RULES):
        ids = (f"event-{n}" for n in itertools.count(1))
        self.monitor = DwellMonitor(rules, new_id=lambda: next(ids))
        self.t = 0.0
        self.actions: list[tuple[float, object]] = []

    def run(self, seconds, frame):
        """``frame(t_relative)`` returns the observations of one frame."""
        start = self.t
        for i in range(round(seconds * FPS)):
            self.t = start + i / FPS
            for action in self.monitor.update(self.t, frame(self.t - start)):
                self.actions.append((self.t, action))
        self.t = start + round(seconds * FPS) / FPS

    def stay(self, seconds, track_id="garagem-1", x=640, y=600, **kw):
        self.run(seconds, lambda _: [in_zone(vehicle(track_id, x, y, **kw))])

    def drive(self, seconds, track_id="garagem-1", x0=0, speed=300, y=600, zone=True, **kw):
        def frame(dt):
            v = vehicle(track_id, x0 + speed * dt, y, **kw)
            return [Observation(v, CALCADA if zone else None)]

        self.run(seconds, frame)

    def nothing(self, seconds):
        self.run(seconds, lambda _: [])

    def starts(self):
        return [(t, a) for t, a in self.actions if isinstance(a, DwellStart)]

    def ends(self):
        return [(t, a) for t, a in self.actions if isinstance(a, DwellEnd)]


# --- when a vehicle counts as stopped ------------------------------------------


def test_a_car_slowing_down_to_pass_never_counts_as_stopped():
    sim = Sim()
    sim.drive(3, speed=300)
    sim.drive(2, x0=900, speed=10)  # crawling for 2 seconds
    sim.drive(3, x0=920, speed=300)
    assert sim.actions == []
    assert sim.monitor.phase("garagem-1") is Phase.MOVING


def test_a_vehicle_still_for_the_window_counts_as_stopped_since_the_window_start():
    sim = Sim()
    sim.drive(2, speed=300)
    sim.stay(4)
    assert sim.monitor.phase("garagem-1") is Phase.MOVING
    sim.stay(2)
    assert sim.monitor.phase("garagem-1") is Phase.STOPPED
    assert sim.monitor._tracks["garagem-1"].since == pytest.approx(2.2, abs=0.25)


def test_a_rider_adjusting_the_motorcycle_keeps_the_stopped_time():
    sim = Sim()
    sim.stay(10, cls="motorcycle", rider=True)  # stopped since t=0
    sim.drive(1, x0=640, speed=60, cls="motorcycle", rider=True)  # 60 px forward in 1 s
    sim.stay(25, x=700, cls="motorcycle", rider=True)
    [(t, start)] = sim.starts()
    assert start.since == pytest.approx(0)
    assert t == pytest.approx(30, abs=0.3)


def test_moving_longer_than_the_grace_resets_the_stopped_time():
    sim = Sim()
    sim.stay(20, cls="motorcycle")
    sim.drive(4, x0=640, speed=40, cls="motorcycle")  # 4 s of slow movement
    sim.stay(20, x=800, cls="motorcycle")
    assert sim.starts() == []


def test_a_missing_detection_shorter_than_the_grace_keeps_the_stopped_time():
    sim = Sim()
    sim.stay(20, cls="motorcycle")
    sim.nothing(2)  # a pedestrian passes in front
    sim.stay(10, cls="motorcycle")
    [(t, start)] = sim.starts()
    assert start.since == pytest.approx(0)
    assert t == pytest.approx(30, abs=0.3)


def test_a_missing_detection_longer_than_the_grace_forgets_a_stopped_vehicle():
    sim = Sim()
    sim.stay(20, cls="motorcycle")
    sim.nothing(4)
    sim.stay(20, cls="motorcycle")
    assert sim.starts() == []


# --- dwell threshold and start event --------------------------------------------


def test_a_car_parked_for_three_minutes_alerts_once_about_120_s_after_stopping():
    sim = Sim()
    sim.drive(2, speed=300)  # arrives
    sim.stay(180, x=600)
    [(t, start)] = sim.starts()
    assert start.cls == "car"
    assert start.since == pytest.approx(2, abs=0.3)
    assert t - start.since == pytest.approx(120, abs=0.3)
    assert start.seconds == 120
    assert start.zone == CALCADA
    assert start.track_id == "garagem-1"
    assert sim.ends() == []


def test_a_car_stopping_briefly_publishes_nothing():
    sim = Sim()
    sim.drive(2, speed=300)
    sim.stay(60, x=600)
    sim.drive(5, x0=600, speed=300)
    sim.nothing(15)
    assert sim.actions == []


def test_a_motorcyclist_waiting_alerts_once_after_30_s_with_rider():
    sim = Sim()
    sim.drive(2, speed=300, cls="motorcycle", rider=True)
    sim.stay(45, x=600, cls="motorcycle", rider=True)
    [(t, start)] = sim.starts()
    assert start.cls == "motorcycle"
    assert start.with_rider is True
    assert t - start.since == pytest.approx(30, abs=0.3)


def test_camera_override_of_the_threshold():
    data = {
        "cameras": [
            {
                "id": "garagem",
                "channel": 1,
                "thresholds": {"car": 60},
                "zones": [
                    {"id": "calcada", "name": "Calçada", "polygon": [[0, 0.5], [1, 0.5], [1, 1]]}
                ],
            }
        ]
    }
    cfg = config.from_dict(data)
    rules = DwellRules.for_camera(cfg, cfg.camera("garagem"))
    assert rules.thresholds == {"car": 60, "motorcycle": 30}
    sim = Sim(rules)
    sim.stay(70)
    [(t, _)] = sim.starts()
    assert t == pytest.approx(60, abs=0.3)


def test_vehicles_outside_every_zone_are_ignored():
    sim = Sim()
    sim.run(200, lambda _: [Observation(vehicle("garagem-1", 640, 300), None)])
    assert sim.actions == []
    assert sim.monitor.phase("garagem-1") is None


def test_two_vehicles_are_followed_separately():
    sim = Sim()
    sim.run(
        130,
        lambda _: [
            in_zone(vehicle("garagem-1", 300, 600, cls="motorcycle", rider=True)),
            in_zone(vehicle("garagem-2", 900, 600, cls="car")),
        ],
    )
    assert [(round(t), a.track_id) for t, a in sim.starts()] == [
        (30, "garagem-1"),
        (120, "garagem-2"),
    ]


# --- end event ------------------------------------------------------------------


def test_the_parked_car_leaving_ends_within_10_s_with_its_total_time():
    sim = Sim()
    sim.stay(420)
    sim.drive(3, x0=640, speed=300, zone=False)  # pulls out of the zone
    sim.nothing(10)
    [(t_start, start)] = sim.starts()
    [(t_end, end)] = sim.ends()
    assert end.started is start
    assert end.seconds == pytest.approx(420, abs=1)
    assert t_end - 420 <= 10


def test_an_alerted_vehicle_lost_for_the_end_grace_ends():
    sim = Sim()
    sim.stay(150)
    sim.nothing(12)
    [(t_end, end)] = sim.ends()
    assert t_end == pytest.approx(150 + 10, abs=0.3)
    assert end.seconds == pytest.approx(150, abs=1)


def test_an_alerted_vehicle_lost_briefly_does_not_end():
    sim = Sim()
    sim.stay(150)
    sim.nothing(8)
    sim.stay(30)
    assert sim.ends() == []
    assert len(sim.starts()) == 1


def test_a_vehicle_that_never_reached_its_threshold_produces_no_end():
    sim = Sim()
    sim.stay(100)
    sim.nothing(15)
    assert sim.actions == []


def test_time_passes_without_frames():
    sim = Sim()
    sim.stay(150)
    assert sim.monitor.update(sim.t + 11, []) != []


# --- no duplicate alerts for the same vehicle -----------------------------------


def test_the_tracker_losing_a_parked_car_at_night_does_not_alert_again():
    sim = Sim()
    sim.stay(150, track_id="garagem-1")  # alerted at 120 s
    sim.nothing(40)  # headlights blind the detector
    sim.stay(60, track_id="garagem-7", x=650)  # found again, new identifier, same place
    sim.drive(3, track_id="garagem-7", x0=650, speed=300, zone=False)
    sim.nothing(5)
    assert len(sim.starts()) == 1
    first = sim.starts()[0][1]
    last_end = sim.ends()[-1][1]
    # The car that leaves is still the first alert, stopped since the original stop.
    assert last_end.started is first
    assert last_end.seconds == pytest.approx(150 + 40 + 60, abs=1)


def test_a_new_identifier_while_the_alerted_car_is_briefly_lost_neither_ends_nor_alerts():
    sim = Sim()
    sim.stay(150, track_id="garagem-1")
    sim.nothing(1)
    sim.stay(60, track_id="garagem-2", x=645)
    assert len(sim.starts()) == 1
    assert sim.ends() == []
    assert sim.monitor.phase("garagem-2") is Phase.ALERTED
    assert sim.monitor.phase("garagem-1") is None


def test_one_car_leaves_and_another_arrives_three_minutes_later():
    sim = Sim()
    sim.stay(150, track_id="garagem-1")
    sim.drive(3, track_id="garagem-1", x0=640, speed=300, zone=False)
    sim.nothing(180)
    sim.drive(2, track_id="garagem-2", speed=300)
    sim.stay(125, track_id="garagem-2", x=600)
    starts = sim.starts()
    assert [s.track_id for _, s in starts] == ["garagem-1", "garagem-2"]
    assert len(sim.ends()) == 1


def test_another_car_parking_at_the_same_spot_right_after_one_left_alerts():
    sim = Sim()
    sim.stay(150, track_id="garagem-1")
    sim.drive(3, track_id="garagem-1", x0=640, speed=300, zone=False)
    sim.nothing(30)
    sim.stay(125, track_id="garagem-2")  # same spot, 33 s later
    assert [s.track_id for _, s in sim.starts()] == ["garagem-1", "garagem-2"]


def test_cooldown_stops_a_second_alert_when_reidentification_misses():
    sim = Sim()
    sim.stay(150, track_id="garagem-1", x=400)
    sim.nothing(15)  # lost: ended after 10 s
    sim.stay(130, track_id="garagem-2", x=600)  # 200 px away: not the same spot
    assert len(sim.starts()) == 1


def test_cooldown_is_per_class():
    sim = Sim()
    sim.run(
        130,
        lambda _: [
            in_zone(vehicle("garagem-1", 300, 600, cls="car")),
            in_zone(vehicle("garagem-2", 900, 600, cls="motorcycle")),
        ],
    )
    assert sorted(s.cls for _, s in sim.starts()) == ["car", "motorcycle"]


def test_cooldown_expires_after_ten_minutes():
    sim = Sim()
    sim.stay(150, track_id="garagem-1", x=300)
    sim.nothing(15)  # lost, keeps the cooldown
    sim.nothing(600)
    sim.stay(125, track_id="garagem-2", x=900)
    assert [s.track_id for _, s in sim.starts()] == ["garagem-1", "garagem-2"]


def test_reidentification_only_within_two_minutes():
    sim = Sim(DwellRules(thresholds={"motorcycle": 30, "car": 120}, cooldown=0))
    sim.stay(150, track_id="garagem-1")
    sim.nothing(130)
    sim.stay(125, track_id="garagem-2")
    assert [s.track_id for _, s in sim.starts()] == ["garagem-1", "garagem-2"]


def test_reidentification_needs_the_same_class():
    sim = Sim(DwellRules(thresholds={"motorcycle": 30, "car": 120}, cooldown=0))
    sim.stay(150, track_id="garagem-1", cls="car")
    sim.nothing(20)
    sim.stay(40, track_id="garagem-2", cls="motorcycle")
    assert [s.cls for _, s in sim.starts()] == ["car", "motorcycle"]
