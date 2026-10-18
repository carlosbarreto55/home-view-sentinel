"""The dwell state machine: when a tracked vehicle in a zone counts as stopped,
when it has stayed long enough to alert, and when it has gone.

Pure logic driven only by observations and the frames' clock (``t``, seconds):

    (absent) ─enter zone─► MOVING ─still over the window─► STOPPED(since)
    STOPPED ─moves or missing ≥ grace─► MOVING
    STOPPED ─stopped ≥ threshold─► ALERTED (dwell.started)
    ALERTED ─out of the zone ≥ grace, or lost ≥ end grace─► ENDED (dwell.ended)

- Still: over the last window (5 s) the box center moved less than 2% of the
  frame diagonal. ``since`` is the start of that window, when it stopped.
- Movement grace (3 s): once stopped, moving away from the resting spot,
  leaving the zone or not being detected for less than this keeps ``since``.
  If it settles at a new spot (a rider moving forward a little), that spot
  becomes the resting spot.
- Alerts are emitted on a frame where the vehicle is at rest.

Avoiding duplicate alerts for the same vehicle:

- Cooldown: after a ``dwell.started`` for a class in a zone, no other one for
  that class and zone for 10 minutes, unless the first vehicle was seen leaving
  the zone. (A vehicle that ended because it was lost keeps the cooldown: the
  tracker losing and re-finding a parked car must not alert twice.)
- Re-identification: a new track that comes to rest within 5% of the diagonal
  of an alerted vehicle of the same class and zone that was lost less than
  2 minutes ago is that vehicle: it inherits its ``since`` and its alert.
"""

from __future__ import annotations

import math
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from sentinel.config import Camera, Config, Zone
from sentinel.detector.detection import Box
from sentinel.detector.tracking import TrackedVehicle

SETTLE_SECONDS = 1.0  # at rest this long at a new spot during an excursion: new resting spot
COVERAGE_TOLERANCE = 0.25  # the still window may start this much after its nominal start


class Phase(Enum):
    MOVING = "moving"
    STOPPED = "stopped"
    ALERTED = "alerted"


@dataclass(frozen=True)
class DwellRules:
    thresholds: dict[str, float]  # seconds, per class, camera overrides applied
    still_window: float = 5.0
    still_max_movement: float = 0.02  # fraction of the frame diagonal
    movement_grace: float = 3.0
    end_grace: float = 10.0
    cooldown: float = 600.0
    reid_max_distance: float = 0.05  # fraction of the frame diagonal
    reid_max_age: float = 120.0

    @classmethod
    def for_camera(cls, config: Config, camera: Camera) -> DwellRules:
        d = config.dwell
        return cls(
            thresholds={c: config.threshold(camera, c) for c in d.thresholds},
            still_window=d.still_window_seconds,
            still_max_movement=d.still_max_movement,
            movement_grace=d.movement_grace_seconds,
            end_grace=d.end_grace_seconds,
            cooldown=d.cooldown_seconds,
            reid_max_distance=d.reid_max_distance,
            reid_max_age=d.reid_max_age_seconds,
        )


@dataclass(frozen=True)
class Observation:
    vehicle: TrackedVehicle
    zone: Zone | None  # None when outside every zone


@dataclass(frozen=True)
class DwellStart:
    event_id: str
    track_id: str
    cls: str
    with_rider: bool
    confidence: float
    zone: Zone
    box: Box
    since: float  # frame clock
    at: float  # frame clock
    seconds: int


@dataclass(frozen=True)
class DwellEnd:
    started: DwellStart
    at: float  # frame clock
    seconds: int  # total time stopped


Action = DwellStart | DwellEnd
Point = tuple[float, float]


def center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _spread(points: list[Point]) -> float:
    """Diagonal of the points' bounding box (an upper bound of their largest distance)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _centroid(points: list[Point]) -> Point:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


@dataclass
class _Track:
    track_id: str
    zone: Zone
    phase: Phase = Phase.MOVING
    samples: deque[tuple[float, Point]] = field(default_factory=deque)
    since: float | None = None
    rest: Point | None = None
    away_since: float | None = None  # when it left its resting spot
    last_seen: float = 0.0  # detected anywhere
    last_in_zone: float = 0.0  # detected in its zone
    started: DwellStart | None = None


class DwellMonitor:
    """Dwell rules for one camera. Call ``update`` once per frame with every
    tracked vehicle of the frame (also those outside the zones), or with an
    empty list to let time pass while no frames arrive."""

    def __init__(
        self,
        rules: DwellRules,
        frame_size: tuple[int, int] = (1280, 720),
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        self.rules = rules
        self.diagonal = math.hypot(*frame_size)
        self.radius = rules.still_max_movement * self.diagonal
        self.new_id = new_id
        self.reid_radius = rules.reid_max_distance * self.diagonal
        self._tracks: dict[str, _Track] = {}
        self._lost_alerted: list[_Track] = []  # ended because they were lost
        self._cooldowns: dict[tuple[str, str], float] = {}  # (zone, class) -> alert time

    def phase(self, track_id: str) -> Phase | None:
        track = self._tracks.get(track_id)
        return track.phase if track else None

    def update(self, t: float, observations: list[Observation]) -> list[Action]:
        actions: list[Action] = []
        seen: set[str] = set()
        in_zone: list[tuple[_Track, TrackedVehicle]] = []
        # First note who is where, so re-identification knows who is lost on this frame.
        for obs in observations:
            vehicle = obs.vehicle
            seen.add(vehicle.track_id)
            track = self._tracks.get(vehicle.track_id)
            if track is not None:
                track.last_seen = t
            if obs.zone is None or (track is not None and obs.zone.id != track.zone.id):
                continue  # outside its zone: handled below like a missing detection
            if track is None:
                track = _Track(vehicle.track_id, obs.zone, last_seen=t)
                self._tracks[vehicle.track_id] = track
            track.last_in_zone = t
            in_zone.append((track, vehicle))
        for track, vehicle in in_zone:
            if track.track_id in self._tracks:  # not taken over by re-identification
                action = self._observe(track, t, vehicle)
                if action:
                    actions.append(action)
        for track in list(self._tracks.values()):
            if track.last_in_zone != t:
                action = self._absent(track, t, detected=track.track_id in seen)
                if action:
                    actions.append(action)
        return actions

    # --- one observation in the zone -------------------------------------------

    def _observe(self, track: _Track, t: float, vehicle: TrackedVehicle) -> Action | None:
        point = center(vehicle.box)
        track.samples.append((t, point))
        while track.samples and track.samples[0][0] < t - self.rules.still_window:
            track.samples.popleft()

        if track.phase is Phase.MOVING:
            if self._still_over_window(track, t):
                track.phase = Phase.STOPPED
                track.since = track.samples[0][0]
                track.rest = _centroid([p for _, p in track.samples])
                track.away_since = None
                self._reidentify(track, t, vehicle)
            return None

        at_rest = self._at_rest(track, t, point)
        if track.phase is Phase.STOPPED:
            if not at_rest:
                if t - track.away_since >= self.rules.movement_grace:
                    self._to_moving(track)
                return None
            if t - track.since >= self.rules.thresholds[vehicle.cls] and not self._cooling(
                track.zone, vehicle.cls, t
            ):
                return self._alert(track, t, vehicle)
        return None

    def _cooling(self, zone: Zone, cls: str, t: float) -> bool:
        alerted_at = self._cooldowns.get((zone.id, cls))
        return alerted_at is not None and t - alerted_at < self.rules.cooldown

    def _reidentify(self, track: _Track, t: float, vehicle: TrackedVehicle) -> None:
        """Take over the alert of a lost vehicle resting at the same spot, if there is one."""
        self._lost_alerted = [
            old for old in self._lost_alerted if t - old.last_in_zone < self.rules.reid_max_age
        ]
        lost_now = [
            old for old in self._tracks.values() if old.phase is Phase.ALERTED and old.last_seen < t
        ]
        candidates = [
            old
            for old in self._lost_alerted + lost_now
            if old.zone.id == track.zone.id
            and old.started.cls == vehicle.cls
            and t - old.last_in_zone < self.rules.reid_max_age
            and math.dist(old.rest, track.rest) < self.reid_radius
        ]
        if not candidates:
            return
        old = min(candidates, key=lambda c: math.dist(c.rest, track.rest))
        if old in self._lost_alerted:
            self._lost_alerted.remove(old)
        else:
            del self._tracks[old.track_id]
        track.phase = Phase.ALERTED
        track.since = old.since
        track.started = old.started

    def _still_over_window(self, track: _Track, t: float) -> bool:
        first = track.samples[0][0]
        if t - first < self.rules.still_window - COVERAGE_TOLERANCE:
            return False
        return _spread([p for _, p in track.samples]) < self.radius

    def _at_rest(self, track: _Track, t: float, point: Point) -> bool:
        """Whether the vehicle is at its resting spot, moving to a new one if it settled there."""
        if math.dist(point, track.rest) < self.radius:
            track.away_since = None
            return True
        if track.away_since is None:
            track.away_since = t
            return False
        recent = [p for s, p in track.samples if s >= t - SETTLE_SECONDS and s > track.away_since]
        settled = (
            t - track.away_since >= SETTLE_SECONDS
            and len(recent) >= 2
            and _spread(recent) < self.radius
        )
        if settled and t - track.away_since < self.rules.movement_grace:
            track.rest = _centroid(recent)
            track.away_since = None
            return True
        return False

    def _to_moving(self, track: _Track) -> None:
        track.phase = Phase.MOVING
        track.since = track.rest = track.away_since = None
        last = track.samples[-1]
        track.samples = deque([last])

    def _alert(self, track: _Track, t: float, vehicle: TrackedVehicle) -> DwellStart:
        track.phase = Phase.ALERTED
        self._cooldowns[(track.zone.id, vehicle.cls)] = t
        track.started = DwellStart(
            event_id=self.new_id(),
            track_id=track.track_id,
            cls=vehicle.cls,
            with_rider=vehicle.with_rider,
            confidence=vehicle.confidence,
            zone=track.zone,
            box=vehicle.box,
            since=track.since,
            at=t,
            seconds=round(t - track.since),
        )
        return track.started

    # --- not in the zone on this frame ----------------------------------------

    def _absent(self, track: _Track, t: float, detected: bool) -> Action | None:
        """``detected``: seen this frame, but outside its zone."""
        gone_for = t - track.last_in_zone
        if track.phase is Phase.ALERTED:
            out_of_zone = detected or track.last_seen > track.last_in_zone
            limit = self.rules.movement_grace if out_of_zone else self.rules.end_grace
            if gone_for >= limit:
                del self._tracks[track.track_id]
                key = (track.zone.id, track.started.cls)
                if out_of_zone:
                    # Seen leaving: another vehicle may alert at once.
                    if self._cooldowns.get(key) == track.started.at:
                        del self._cooldowns[key]
                else:
                    self._lost_alerted.append(track)
                return DwellEnd(
                    started=track.started,
                    at=t,
                    seconds=round(track.last_in_zone - track.since),
                )
            return None
        if gone_for >= self.rules.movement_grace:
            del self._tracks[track.track_id]
        return None
