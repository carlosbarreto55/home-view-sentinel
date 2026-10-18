"""Track vehicles across frames with ByteTrack (Roboflow ``trackers``, on supervision).

One tracker per camera. A track survives gaps of up to 3 s without a detection.
Track identifiers are ``<camera>-<n>``, with ``n`` growing for the whole life of
the process (also across ``reset`` after a lost stream), so an identifier is
never reused for a different vehicle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import supervision as sv
import trackers

from sentinel.detector.detection import CAR, MOTORCYCLE, Box
from sentinel.detector.riders import RiderHistory, Vehicle

LOST_SECONDS = 3.0
_CLASS_IDS = {CAR: 0, MOTORCYCLE: 1}


@dataclass(frozen=True)
class TrackedVehicle:
    track_id: str
    cls: str  # the class seen most often on this track
    confidence: float
    box: Box
    with_rider: bool


class _TrackState:
    def __init__(self, track_id: str):
        self.track_id = track_id
        self.classes: Counter[str] = Counter()
        self.riders = RiderHistory()
        self.last_seen = 0.0


class VehicleTracker:
    def __init__(
        self,
        camera_id: str,
        fps: float,
        min_confidence: float = 0.4,
        lost_seconds: float = LOST_SECONDS,
    ):
        self.camera_id = camera_id
        self.fps = fps
        self.min_confidence = min_confidence
        self.lost_seconds = lost_seconds
        self._next_number = 1
        self._states: dict[int, _TrackState] = {}
        self._tracker = self._new_tracker()

    def _new_tracker(self) -> trackers.ByteTrackTracker:
        return trackers.ByteTrackTracker(
            # Counted in 30 fps frames: 90 is 3 s whatever the sampling rate.
            lost_track_buffer=round(self.lost_seconds * 30),
            frame_rate=self.fps,
            track_activation_threshold=self.min_confidence,
        )

    def reset(self) -> None:
        """Forget every track (the stream was lost)."""
        self._tracker = self._new_tracker()
        self._states.clear()

    def update(self, vehicles: list[Vehicle], timestamp: float) -> list[TrackedVehicle]:
        """Vehicles of one frame in, the confirmed tracks seen in that frame out."""
        detections = sv.Detections(
            xyxy=np.array([v.box for v in vehicles], dtype=float).reshape(-1, 4),
            confidence=np.array([v.confidence for v in vehicles], dtype=float),
            class_id=np.array([_CLASS_IDS[v.cls] for v in vehicles], dtype=int),
            data={"index": np.arange(len(vehicles))},
        )
        tracked = self._tracker.update(detections, timestamp=timestamp)
        result = []
        if len(tracked) == 0:
            self._forget_old(timestamp)
            return result
        for tracker_id, index in zip(tracked.tracker_id, tracked.data["index"], strict=True):
            if tracker_id < 0:
                continue  # not confirmed yet
            vehicle = vehicles[int(index)]
            state = self._states.get(int(tracker_id))
            if state is None:
                state = _TrackState(f"{self.camera_id}-{self._next_number}")
                self._next_number += 1
                self._states[int(tracker_id)] = state
            state.classes[vehicle.cls] += 1
            state.last_seen = timestamp
            cls = state.classes.most_common(1)[0][0]
            if cls == MOTORCYCLE:
                state.riders.add(vehicle.rider)
            result.append(
                TrackedVehicle(
                    track_id=state.track_id,
                    cls=cls,
                    confidence=vehicle.confidence,
                    box=vehicle.box,
                    with_rider=cls == MOTORCYCLE and state.riders.with_rider,
                )
            )
        self._forget_old(timestamp)
        return result

    def _forget_old(self, now: float) -> None:
        horizon = 2 * self.lost_seconds
        for key in [k for k, s in self._states.items() if now - s.last_seen > horizon]:
            del self._states[key]
