"""The detection pipeline: frames in, events out.

``CameraPipeline`` runs one camera's frames through detection, rider
association, tracking, zones and the dwell rules, saves snapshots and turns the
dwell actions into contract events. It is pure apart from writing snapshots,
so replay and tests use it exactly as the service does.

``Detector`` is the service: one frame reader thread per camera keeps the
latest frame of its camera, and a single loop runs inference on them (so the
model is only used from one thread), publishes events and statuses, lets time
pass for cameras without frames, and deletes old snapshots once a day.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.bus import client as bus
from sentinel.bus.events import (
    DWELL_STARTED,
    CameraRef,
    DwellEvent,
    ObjectRef,
    ZoneRef,
)
from sentinel.config import Camera, Config
from sentinel.detector import snapshots
from sentinel.detector.dwell import DwellEnd, DwellMonitor, DwellRules, DwellStart, Observation
from sentinel.detector.frames import HEIGHT, WIDTH, Frame
from sentinel.detector.inference import InferenceBackend
from sentinel.detector.riders import attach_riders
from sentinel.detector.tracking import VehicleTracker
from sentinel.detector.zones import zone_of

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0
CLEANUP_INTERVAL = timedelta(days=1)


class CameraPipeline:
    def __init__(
        self,
        config: Config,
        camera: Camera,
        backend: InferenceBackend,
        snapshots_root: Path,
        frame_size: tuple[int, int] = (WIDTH, HEIGHT),
    ):
        self.camera = camera
        self.backend = backend
        self.snapshots_root = snapshots_root
        self.width, self.height = frame_size
        self.tracker = VehicleTracker(
            camera.id, config.sampling.fps, min_confidence=config.model.min_confidence
        )
        self.dwell = DwellMonitor(DwellRules.for_camera(config, camera), frame_size)
        self._started: dict[str, DwellEvent] = {}  # start event id -> event
        self._wall_offset: float | None = None  # wall clock - frame clock, from the last frame
        self._last_t: float | None = None  # frame clock of the last frame or tick

    def process(self, frame: Frame) -> list[DwellEvent]:
        """One frame through the whole pipeline; the events it produces."""
        if self._last_t is not None and frame.monotonic < self._last_t:
            return []  # older than a tick already applied
        self._last_t = frame.monotonic
        self._wall_offset = frame.wall.timestamp() - frame.monotonic
        vehicles = attach_riders(self.backend.detect(frame.image))
        tracked = self.tracker.update(vehicles, frame.monotonic)
        observations = [
            Observation(v, zone_of(v.box, self.camera.zones, self.width, self.height))
            for v in tracked
        ]
        actions = self.dwell.update(frame.monotonic, observations)
        return [self._event(action, frame) for action in actions]

    def tick(self, monotonic: float) -> list[DwellEvent]:
        """Let time pass without frames (the stream is down), so lost vehicles end.
        Does nothing while frames keep coming (within a second)."""
        if self._last_t is not None and monotonic - self._last_t < TICK_SECONDS:
            return []
        self._last_t = monotonic
        return [self._event(action, None) for action in self.dwell.update(monotonic, [])]

    def reset(self) -> None:
        """The stream was lost: forget the tracks. Alerted vehicles end by the end grace,
        and one found again at the same spot is re-identified."""
        self.tracker.reset()

    def _wall(self, monotonic: float) -> datetime:
        if self._wall_offset is None:
            return datetime.now(UTC)
        return datetime.fromtimestamp(monotonic + self._wall_offset, UTC)

    def _event(self, action: DwellStart | DwellEnd, frame: Frame | None) -> DwellEvent:
        if isinstance(action, DwellStart):
            assert frame is not None  # alerts only happen on frames
            ref = snapshots.save(
                self.snapshots_root,
                self.camera.id,
                frame.wall,
                action.track_id,
                frame.image,
                action.box,
                action.zone.polygon,
            )
            event = DwellEvent(
                type=DWELL_STARTED,
                event_id=action.event_id,
                occurred_at=frame.wall,
                camera=CameraRef(self.camera.id, self.camera.name, self.camera.channel),
                zone=ZoneRef(action.zone.id, action.zone.name),
                object=ObjectRef(action.cls, action.with_rider, action.track_id, action.confidence),
                since=frame.wall - timedelta(seconds=action.at - action.since),
                seconds=action.seconds,
                snapshot=ref,
            )
            self._started[event.event_id] = event
            return event
        start = self._started[action.started.event_id]
        return start.ended(self._wall(action.at), action.seconds)


class Detector:
    """Runs every camera's pipeline and publishes to the bus."""

    def __init__(
        self,
        config: Config,
        backend: InferenceBackend,
        publisher: bus.BusClient,
        snapshots_root: Path,
        reader_factory: Callable[..., object],
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.config = config
        self.publisher = publisher
        self.snapshots_root = snapshots_root
        self.monotonic = monotonic
        self.now = now
        self.pipelines = {
            camera.id: CameraPipeline(config, camera, backend, snapshots_root)
            for camera in config.cameras
        }
        self._latest: dict[str, Frame | None] = {camera.id: None for camera in config.cameras}
        self._lost: set[str] = set()
        self._lock = threading.Condition()
        self._last_cleanup: datetime | None = None
        self.readers = [
            reader_factory(
                camera,
                on_frame=self._on_frame,
                on_state=lambda state, detail, cid=camera.id: self._on_state(cid, state, detail),
            )
            for camera in config.cameras
        ]

    # --- called from the reader threads ---------------------------------------

    def _on_frame(self, frame: Frame) -> None:
        with self._lock:
            self._latest[frame.camera_id] = frame  # older unprocessed frames are dropped
            self._lock.notify()

    def _on_state(self, camera_id: str, state: str, detail: str | None) -> None:
        self.publisher.set_status(state, detail, component=bus.camera_component(camera_id))
        if state == bus.RECONNECTING:
            with self._lock:
                self._lost.add(camera_id)
                self._latest[camera_id] = None

    # --- the processing loop --------------------------------------------------

    def step(self, timeout: float = TICK_SECONDS) -> list[DwellEvent]:
        """Process the frames that arrived (waiting up to ``timeout`` for one)."""
        with self._lock:
            if not any(self._latest.values()):
                self._lock.wait(timeout)
            frames = {cid: f for cid, f in self._latest.items() if f is not None}
            lost, self._lost = self._lost, set()
            for cid in frames:
                self._latest[cid] = None
        produced: list[DwellEvent] = []
        for camera_id in lost:
            self.pipelines[camera_id].reset()
        for camera_id, pipeline in self.pipelines.items():
            frame = frames.get(camera_id)
            try:
                events = pipeline.process(frame) if frame else pipeline.tick(self.monotonic())
            except Exception:
                log.exception("%s: processing failed", camera_id)
                continue
            for event in events:
                self._publish(event)
            produced.extend(events)
        self._cleanup()
        return produced

    def _publish(self, event: DwellEvent) -> None:
        log.info(
            "%s %s %s in %s: %s s (%s)",
            event.type,
            event.camera.id,
            event.object.track_id,
            event.zone.id,
            event.seconds,
            event.snapshot.file,
        )
        try:
            self.publisher.publish_event(event)
        except Exception:
            log.exception("could not publish %s", event.event_id)

    def _cleanup(self) -> None:
        now = self.now()
        if self._last_cleanup is None or now - self._last_cleanup >= CLEANUP_INTERVAL:
            self._last_cleanup = now
            snapshots.delete_old(self.snapshots_root, self.config.snapshots.retention_days, now)

    def run(self, stop: threading.Event) -> None:
        for camera in self.config.cameras:
            self.publisher.set_status(
                bus.RECONNECTING, "starting", component=bus.camera_component(camera.id)
            )
        threads = [
            threading.Thread(target=reader.run, args=(stop,), name=f"reader-{reader.camera_id}")
            for reader in self.readers
        ]
        for thread in threads:
            thread.start()
        try:
            while not stop.is_set():
                self.step()
        finally:
            stop.set()
            with self._lock:
                self._lock.notify_all()
            for thread in threads:
                thread.join(timeout=5)
