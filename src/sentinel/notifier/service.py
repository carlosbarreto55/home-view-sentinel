"""The notifier service: events and statuses in, pushes out.

``python -m sentinel.notifier`` (run by launchd, natively: it only needs the
broker and ntfy on loopback) subscribes with a persistent session to
``sentinel/v1/events/+`` and ``sentinel/v1/status/+`` and sends:

- one push per ``dwell.started`` (and ``dwell.ended`` when enabled), with the
  snapshot, built by ``messages``;
- "O Sentinel parou de vigiar as câmeras" once, when the detector has been
  offline for more than 2 minutes, and "O Sentinel voltou a vigiar" when it is
  back after that;
- "Sem imagem da câmera <nome>" once, when a camera has been without a picture
  for more than 10 minutes while the detector runs.

Offline time is counted from when the notifier itself saw the problem: a status
retained from before the notifier started (e.g. "offline" since the Mac was shut
down) counts from the notifier's start, so a normal boot sends nothing.
Sending happens on its own thread, so ntfy's retries never hold up the bus.
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.bus import client as bus
from sentinel.bus.client import Status
from sentinel.bus.events import DwellEvent, InvalidEvent
from sentinel.config import Notifications
from sentinel.notifier import messages, ntfy_server
from sentinel.notifier.messages import Push
from sentinel.notifier.ntfy import NtfyClient
from sentinel.runtime import EXIT_DONE, EXIT_RESTART, logs

log = logging.getLogger("sentinel.notifier")

DETECTOR = "detector"
CAMERA_PREFIX = bus.camera_component("")
DETECTOR_OFFLINE_AFTER = timedelta(minutes=2)
CAMERA_NO_PICTURE_AFTER = timedelta(minutes=10)
TICK_SECONDS = 5
REMEMBERED_EVENTS = 200


@dataclass
class _Problem:
    since: datetime
    alerted: bool = False


class StatusWatch:
    """The offline rules, driven by status messages and an injected clock."""

    def __init__(self, settings: Notifications, camera_names: dict[str, str], started: datetime):
        self.settings = settings
        self.camera_names = camera_names
        self.started = started
        self.detector: _Problem | None = None  # offline since
        self.detector_online = False
        self.cameras: dict[str, _Problem] = {}  # without picture since

    def _observed(self, status: Status, received: datetime) -> datetime:
        return max(status.since or received, self.started)

    def on_status(self, component: str, status: Status, received: datetime) -> list[Push]:
        if component == DETECTOR:
            if status.state == bus.ONLINE:
                was_alerted = self.detector is not None and self.detector.alerted
                self.detector = None
                self.detector_online = True
                if was_alerted:
                    return [messages.detector_online(received, self.settings)]
            elif self.detector is None:
                self.detector = _Problem(self._observed(status, received))
                self.detector_online = False
                # Camera statuses are stale while the detector is down.
                self.cameras.clear()
        elif component.startswith(CAMERA_PREFIX):
            camera_id = component[len(CAMERA_PREFIX) :]
            if status.state == bus.ONLINE:
                self.cameras.pop(camera_id, None)
            elif camera_id not in self.cameras:
                self.cameras[camera_id] = _Problem(self._observed(status, received))
        return []

    def tick(self, now: datetime) -> list[Push]:
        pushes = []
        if (
            self.detector is not None
            and not self.detector.alerted
            and now - self.detector.since >= DETECTOR_OFFLINE_AFTER
        ):
            self.detector.alerted = True
            pushes.append(messages.detector_offline(now, self.settings))
        if self.detector_online:
            for camera_id, problem in self.cameras.items():
                if not problem.alerted and now - problem.since >= CAMERA_NO_PICTURE_AFTER:
                    problem.alerted = True
                    name = self.camera_names.get(camera_id, camera_id)
                    pushes.append(messages.camera_without_picture(name, now, self.settings))
        return pushes


class Notifier:
    """Turns bus messages into pushes handed to ``send`` (thread-safe)."""

    def __init__(
        self,
        settings: Notifications,
        camera_names: dict[str, str],
        send: Callable[[Push], None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.settings = settings
        self.send = send
        self.clock = clock
        self.watch = StatusWatch(settings, camera_names, clock())
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def on_event(self, topic: str, payload: bytes) -> None:
        try:
            event = DwellEvent.from_json(payload)
        except InvalidEvent as error:
            log.warning("ignored an invalid event on %s: %s", topic, error)
            return
        with self._lock:
            # QoS 1 may deliver an event twice; push it once.
            if event.event_id in self._seen:
                return
            self._seen[event.event_id] = None
            while len(self._seen) > REMEMBERED_EVENTS:
                self._seen.popitem(last=False)
        push = messages.for_event(event, self.settings)
        log.info(
            "%s %s on %s/%s", event.type, event.object.vehicle_class, event.camera.id, event.zone.id
        )
        if push is not None:
            self.send(push)

    def on_status(self, topic: str, payload: bytes) -> None:
        component = topic.rsplit("/", 1)[-1]
        try:
            status = Status.from_json(payload)
        except (ValueError, KeyError) as error:
            log.warning("ignored an invalid status on %s: %s", topic, error)
            return
        with self._lock:
            pushes = self.watch.on_status(component, status, self.clock())
        for push in pushes:
            self.send(push)

    def tick(self) -> None:
        with self._lock:
            pushes = self.watch.tick(self.clock())
        for push in pushes:
            self.send(push)


class Sender:
    """Sends pushes one at a time on a background thread."""

    def __init__(self, client: NtfyClient):
        self.client = client
        self.queue: queue.Queue[Push | None] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="ntfy-sender", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def __call__(self, push: Push) -> None:
        self.queue.put(push)

    def stop(self, timeout: float = 5) -> None:
        self.queue.put(None)
        self.thread.join(timeout)

    def _run(self) -> None:
        while (push := self.queue.get()) is not None:
            self.client.send(push)


def main() -> int:
    redacting = logs.setup("notifier")
    try:
        config = config_module.load(config_module.DEFAULT_PATH)
        token = secrets.require(secrets.NTFY_NOTIFIER_TOKEN)
        password = secrets.require(secrets.mqtt_item("notifier"))
    except config_module.ConfigError as error:
        log.error("%s", error)
        return EXIT_DONE
    except secrets.KeychainError as error:
        log.error("%s", error)
        return EXIT_DONE
    redacting.add(token, password)

    settings = config.notifications
    sender = Sender(
        NtfyClient(
            f"http://127.0.0.1:{ntfy_server.PORT}",
            settings.ntfy_topic,
            token,
            paths.snapshots_dir(),
        )
    )
    notifier = Notifier(settings, {c.id: c.name for c in config.cameras}, sender)
    subscriber = bus.BusClient("notifier", "notifier", password, persistent=True)
    subscriber.subscribe(bus.EVENTS_ALL, notifier.on_event)
    subscriber.subscribe(bus.STATUS_ALL, notifier.on_status)

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    sender.start()
    subscriber.start()
    log.info("sending to topic %s", settings.ntfy_topic)
    try:
        while not stop.wait(TICK_SECONDS):
            notifier.tick()
    except Exception:
        log.exception("the notifier failed")
        return EXIT_RESTART
    finally:
        subscriber.stop()
        sender.stop()
    return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
