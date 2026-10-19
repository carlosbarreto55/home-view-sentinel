"""MQTT client wrapper shared by every component.

- Authenticated connect with automatic retry (1 s doubling to 60 s).
- A retained status on ``sentinel/v1/status/<component>``, ``online`` once
  connected, with ``offline`` registered as the Last Will.
- QoS 1 event publishing, validated against the contract first.
- Subscriptions that survive reconnects; consumers use a persistent session
  (fixed client id, ``clean_session=False``) so nothing published while they
  were away is lost.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from sentinel.bus.events import DwellEvent, parse_utc, utc_text

log = logging.getLogger(__name__)

PREFIX = "sentinel/v1"
EVENTS_ALL = f"{PREFIX}/events/+"
STATUS_ALL = f"{PREFIX}/status/+"

ONLINE = "online"
RECONNECTING = "reconnecting"
OFFLINE = "offline"
STATES = (ONLINE, RECONNECTING, OFFLINE)


def events_topic(camera_id: str) -> str:
    return f"{PREFIX}/events/{camera_id}"


def status_topic(component: str) -> str:
    return f"{PREFIX}/status/{component}"


def camera_component(camera_id: str) -> str:
    """Status component name of a watched camera (one topic level, so ``status/+`` sees it)."""
    return f"camera-{camera_id}"


@dataclass(frozen=True)
class Status:
    """A component's state. ``since`` is None in the Last Will, which the broker
    publishes long after it was registered; readers then use the time received."""

    state: str
    since: datetime | None
    detail: str | None = None

    def to_json(self) -> str:
        data: dict[str, Any] = {"state": self.state}
        if self.since is not None:
            data["since"] = utc_text(self.since)
        if self.detail:
            data["detail"] = self.detail
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: bytes | str) -> Status:
        data = json.loads(payload)
        if data.get("state") not in STATES:
            raise ValueError(f"unknown state {data.get('state')!r}")
        since = parse_utc(data["since"]) if data.get("since") else None
        return cls(state=data["state"], since=since, detail=data.get("detail"))


Handler = Callable[[str, bytes], None]


class BusClient:
    def __init__(
        self,
        component: str,
        user: str,
        password: str,
        host: str = "127.0.0.1",
        port: int = 1883,
        persistent: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client_factory: Callable[..., Any] = mqtt.Client,
    ):
        self.component = component
        self.host = host
        self.port = port
        self.clock = clock
        self._subscriptions: dict[str, Handler] = {}
        self._statuses: dict[str, Status] = {}
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._client = client_factory(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sentinel-{component}",
            clean_session=not persistent,
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set(user, password)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.will_set(
            status_topic(component),
            Status(OFFLINE, None, "connection lost").to_json().encode(),
            qos=1,
            retain=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Connect in the background; retries until the broker is reachable."""
        self._client.connect_async(self.host, self.port, keepalive=30)
        self._client.loop_start()

    def wait_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def stop(self, detail: str = "stopped") -> None:
        """Publish ``offline`` (with ``detail``) for this component and disconnect cleanly."""
        if self.connected:
            info = self._client.publish(
                status_topic(self.component),
                Status(OFFLINE, self.clock(), detail).to_json().encode(),
                qos=1,
                retain=True,
            )
            info.wait_for_publish(timeout=2)
        self._client.disconnect()
        self._client.loop_stop()

    # --- publishing ---------------------------------------------------------

    def set_status(self, state: str, detail: str | None = None, component: str | None = None):
        """Publish a retained status (this component's, or a sub-component such as a camera).

        ``since`` only changes when the state changes. The latest status of each
        component is published again after every reconnect.
        """
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        component = component or self.component
        with self._lock:
            previous = self._statuses.get(component)
            since = previous.since if previous and previous.state == state else self.clock()
            status = Status(state, since, detail)
            self._statuses[component] = status
        return self._publish_status(component, status)

    def _publish_status(self, component: str, status: Status):
        return self._client.publish(
            status_topic(component), status.to_json().encode(), qos=1, retain=True
        )

    def publish_event(self, event: DwellEvent):
        """Publish an event with QoS 1, not retained. Raises InvalidEvent for a bad event."""
        payload = event.to_json()  # validates against the contract
        return self._client.publish(
            events_topic(event.camera.id), payload.encode(), qos=1, retain=False
        )

    # --- subscribing --------------------------------------------------------

    def subscribe(self, topic_filter: str, handler: Handler) -> None:
        """Call ``handler(topic, payload)`` for each message matching the filter."""
        with self._lock:
            self._subscriptions[topic_filter] = handler
        if self.connected:
            self._client.subscribe(topic_filter, qos=1)

    # --- paho callbacks -----------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            # Wrong broker credentials: paho keeps retrying with the backoff.
            log.error("broker refused the connection: %s", reason_code)
            return
        log.info("connected to the broker at %s:%s", self.host, self.port)
        self._connected.set()
        with self._lock:
            filters = list(self._subscriptions)
            statuses = dict(self._statuses)
        for topic_filter in filters:
            client.subscribe(topic_filter, qos=1)
        if self.component not in statuses:
            self.set_status(ONLINE)
        for component, status in statuses.items():
            self._publish_status(component, status)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()
        if reason_code != 0:
            log.warning("lost the broker connection (%s); retrying", reason_code)

    def _on_message(self, client, userdata, message):
        with self._lock:
            handlers = [
                handler
                for topic_filter, handler in self._subscriptions.items()
                if mqtt.topic_matches_sub(topic_filter, message.topic)
            ]
        for handler in handlers:
            try:
                handler(message.topic, message.payload)
            except Exception:
                log.exception("handler for %s failed", message.topic)
