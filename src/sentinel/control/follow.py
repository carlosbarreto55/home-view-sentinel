"""What the components report on the bus: the phase, the cameras and the last alert.

While the components run, the control agent connects to the broker as its own
user, ``control``, with a persistent session, and follows
``sentinel/v1/status/+`` and ``sentinel/v1/events/+``:

- the phase is ``starting`` from the start request until the gateway, notifier,
  detector and every camera are online (or 2 minutes pass), then ``watching``
  while they stay online and ``problem`` when one is not, with a detail in
  Portuguese. A rejected DVR login is a problem at once;
- a camera's state counts only while the detector is online (a camera status
  retained from before the detector stopped is stale);
- the last ``dwell.started`` is built with the notifier's own messages, so it
  reads exactly like the push, and kept in ``control/last-alert.json`` across
  restarts and while off, until a newer one replaces it or its snapshot is
  deleted by the retention.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sentinel.bus import client as bus
from sentinel.bus.client import Status
from sentinel.bus.events import DWELL_STARTED, DwellEvent, InvalidEvent
from sentinel.config import Camera, Notifications
from sentinel.control.contract import (
    PROBLEM,
    STARTING,
    STOPPED,
    STOPPING,
    WATCHING,
    CameraState,
    LastAlert,
)
from sentinel.control.files import write_private
from sentinel.gateway.launcher import WRONG_LOGIN
from sentinel.notifier import messages

log = logging.getLogger(__name__)

USER = "control"
START_TIMEOUT = timedelta(minutes=2)
# Components that publish a status, in the order their problems are reported.
REPORTING = {
    "gateway": "O gateway de vídeo não está funcionando",
    "notifier": "O notificador não está funcionando",
    "detector": "O detector não está funcionando",
}
BROKER_DOWN = "O broker MQTT não responde"


def camera_problem(name: str) -> str:
    return f"Câmera {name} sem imagem"


@dataclass(frozen=True)
class Run:
    """What the agent asked of the components."""

    wanted: bool  # the mode says watch
    busy: bool = False  # a start or stop is in progress
    started_at: datetime | None = None  # when watching was asked for
    error: str | None = None  # loading the agents failed


class Follower:
    """Thread-safe: the bus callbacks run on paho's thread."""

    def __init__(
        self,
        cameras: tuple[Camera, ...],
        settings: Notifications,
        snapshots_root: Path,
        alert_path: Path,
        stop_marker: Path | None = None,
    ):
        self.cameras = cameras
        self.settings = settings
        self.snapshots_root = snapshots_root
        self.alert_path = alert_path
        self.stop_marker = stop_marker
        self.connected: Callable[[], bool] = lambda: False
        self._statuses: dict[str, Status] = {}
        self._seen_watching = False
        self._lock = threading.Lock()
        self._alert = self._load_alert()

    # --- bus ------------------------------------------------------------------

    def reset(self) -> None:
        """Forget the statuses, when watching starts again."""
        with self._lock:
            self._statuses.clear()
            self._seen_watching = False

    def on_status(self, topic: str, payload: bytes) -> None:
        component = topic.rsplit("/", 1)[-1]
        try:
            status = Status.from_json(payload)
        except (ValueError, KeyError) as error:
            log.warning("ignored an invalid status on %s: %s", topic, error)
            return
        with self._lock:
            self._statuses[component] = status

    def on_event(self, topic: str, payload: bytes) -> None:
        try:
            event = DwellEvent.from_json(payload)
        except InvalidEvent as error:
            log.warning("ignored an invalid event on %s: %s", topic, error)
            return
        if event.type != DWELL_STARTED:
            return
        push = messages.for_event(event, self.settings)
        if push is None:
            return
        alert = LastAlert(
            event_id=event.event_id,
            title=push.title,
            message=push.message,
            occurred_at=event.occurred_at,
            camera_id=event.camera.id,
            camera_name=event.camera.name,
            camera_channel=event.camera.channel,
            zone=event.zone.name,
            snapshot=str(self.snapshots_root / event.snapshot.file),
        )
        with self._lock:
            # QoS 1 may repeat an event, or a queued one arrive late.
            if self._alert is not None and self._alert.occurred_at > alert.occurred_at:
                return
            self._alert = alert
        write_private(self.alert_path, json.dumps(alert.to_dict(), ensure_ascii=False) + "\n")
        log.info("last alert: %s (%s)", alert.title, alert.message)

    # --- state ----------------------------------------------------------------

    def _online(self, component: str) -> bool:
        status = self._statuses.get(component)
        return status is not None and status.state == bus.ONLINE

    def camera_states(self) -> tuple[CameraState, ...]:
        with self._lock:
            detector = self._online("detector")
            states = []
            for camera in self.cameras:
                status = self._statuses.get(bus.camera_component(camera.id))
                state = status.state if detector and status is not None else bus.OFFLINE
                states.append(CameraState(camera.id, camera.name, camera.channel, state))
        return tuple(states)

    def _wrong_login(self) -> bool:
        gateway = self._statuses.get("gateway")
        if gateway is not None and gateway.state != bus.ONLINE and gateway.detail == WRONG_LOGIN:
            return True
        return self.stop_marker is not None and self.stop_marker.exists()

    def _problems(self) -> list[str]:
        if not self.connected():
            return [BROKER_DOWN]
        found = [text for name, text in REPORTING.items() if not self._online(name)]
        if self._online("detector"):
            found += [
                camera_problem(camera.name)
                for camera in self.cameras
                if not self._online(bus.camera_component(camera.id))
            ]
        return found

    def phase(self, run: Run, now: datetime) -> tuple[str, str | None]:
        """The phase and its detail (only for a problem)."""
        if not run.wanted:
            return (STOPPING if run.busy else STOPPED), None
        if run.error:
            return PROBLEM, run.error
        with self._lock:
            if self._wrong_login():
                return PROBLEM, WRONG_LOGIN
            problems = self._problems()
            if not problems:
                self._seen_watching = True
                return WATCHING, None
            started_at = run.started_at or now
            if not self._seen_watching and now - started_at < START_TIMEOUT:
                return STARTING, None
            return PROBLEM, problems[0]

    # --- last alert -------------------------------------------------------------

    def last_alert(self) -> LastAlert | None:
        """The last alert, dropped once its snapshot is gone (14-day retention)."""
        with self._lock:
            alert = self._alert
            if alert is not None and not Path(alert.snapshot).exists():
                log.info("forgot the last alert: its snapshot %s is gone", alert.snapshot)
                self._alert = alert = None
                self.alert_path.unlink(missing_ok=True)
            return alert

    def _load_alert(self) -> LastAlert | None:
        try:
            return LastAlert.from_dict(json.loads(self.alert_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError) as error:
            log.warning("ignored an unreadable %s: %s", self.alert_path.name, error)
            return None


def connect(
    follower: Follower,
    password: str,
    client_factory: Callable[..., bus.BusClient] = bus.BusClient,
) -> bus.BusClient:
    """A persistent-session client feeding ``follower``; call ``start()`` on it."""
    client = client_factory(USER, USER, password, persistent=True)
    client.subscribe(bus.STATUS_ALL, follower.on_status)
    client.subscribe(bus.EVENTS_ALL, follower.on_event)
    follower.connected = lambda: client.connected
    return client
