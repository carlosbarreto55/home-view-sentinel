import json
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.reasoncodes import ReasonCode

from sentinel import secrets
from sentinel.bus import client as bus
from sentinel.bus.events import InvalidEvent
from test_events import started


class FakePaho:
    """Records what BusClient asks of paho."""

    def __init__(self, **options):
        self.options = options
        self.published = []
        self.subscribed = []
        self.will = None
        self.credentials = None

    def username_pw_set(self, user, password):
        self.credentials = (user, password)

    def reconnect_delay_set(self, min_delay, max_delay):
        self.delays = (min_delay, max_delay)

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, json.loads(payload), qos, retain)

    def connect_async(self, host, port, keepalive):
        self.address = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        self.disconnected = True

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, json.loads(payload), qos, retain))
        return SimpleNamespace(wait_for_publish=lambda timeout: None)

    def subscribe(self, topic_filter, qos):
        self.subscribed.append((topic_filter, qos))

    # Helpers for tests.
    def connect(self, ok=True):
        code = ReasonCode(mqtt.PacketTypes.CONNACK, identifier=0 if ok else 135)
        self.on_connect(self, None, None, code, None)

    def drop(self):
        self.on_disconnect(self, None, None, 7, None)

    def deliver(self, topic, payload):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 18, 15, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def make(component="detector", persistent=False):
    clock = Clock()
    client = bus.BusClient(
        component, "detector", "pw", persistent=persistent, clock=clock, client_factory=FakePaho
    )
    return client, client._client, clock


def test_persistent_session_and_client_id():
    _, paho, _ = make("notifier", persistent=True)
    assert paho.options["client_id"] == "sentinel-notifier"
    assert paho.options["clean_session"] is False
    _, producer, _ = make("detector")
    assert producer.options["clean_session"] is True


def test_authenticates_and_registers_offline_as_last_will():
    client, paho, _ = make()
    client.start()
    assert paho.credentials == ("detector", "pw")
    assert paho.address == ("127.0.0.1", 1883)
    assert paho.will == (
        "sentinel/v1/status/detector",
        {"state": "offline", "detail": "connection lost"},
        1,
        True,
    )


def test_online_status_is_retained_after_connecting():
    client, paho, _ = make()
    client.start()
    assert not client.connected
    paho.connect()
    assert client.connected
    assert paho.published == [
        (
            "sentinel/v1/status/detector",
            {"state": "online", "since": "2026-10-18T15:00:00Z"},
            1,
            True,
        )
    ]


def test_refused_connection_is_not_connected():
    client, paho, _ = make()
    paho.connect(ok=False)
    assert not client.connected
    assert paho.published == []


def test_status_since_changes_only_with_the_state():
    client, paho, clock = make()
    paho.connect()
    camera = bus.camera_component("garagem")
    client.set_status("reconnecting", "no frames for 10 s", component=camera)
    clock.now += timedelta(seconds=30)
    client.set_status("reconnecting", "retrying in 10 s", component=camera)
    clock.now += timedelta(seconds=30)
    client.set_status("online", component=camera)
    camera_messages = [p for p in paho.published if p[0] == "sentinel/v1/status/camera-garagem"]
    assert [m[1] for m in camera_messages] == [
        {"state": "reconnecting", "since": "2026-10-18T15:00:00Z", "detail": "no frames for 10 s"},
        {"state": "reconnecting", "since": "2026-10-18T15:00:00Z", "detail": "retrying in 10 s"},
        {"state": "online", "since": "2026-10-18T15:01:00Z"},
    ]
    assert all(m[2:] == (1, True) for m in camera_messages)


def test_statuses_and_subscriptions_are_restored_after_reconnect():
    client, paho, _ = make()
    client.subscribe(bus.STATUS_ALL, lambda t, p: None)
    paho.connect()
    client.set_status("online", component="camera-garagem")
    paho.drop()
    assert not client.connected
    paho.published.clear()
    paho.subscribed.clear()
    paho.connect()
    assert paho.subscribed == [("sentinel/v1/status/+", 1)]
    assert {p[0] for p in paho.published} == {
        "sentinel/v1/status/detector",
        "sentinel/v1/status/camera-garagem",
    }


def test_status_set_before_connecting_is_published_on_connect():
    client, paho, _ = make("gateway")
    client.set_status("offline", "Usuário ou senha do DVR incorretos")
    paho.connect()
    assert paho.published[-1][1]["detail"] == "Usuário ou senha do DVR incorretos"
    assert all(p[1]["state"] == "offline" for p in paho.published)


def test_events_are_qos1_not_retained_and_validated():
    client, paho, _ = make()
    paho.connect()
    client.publish_event(started())
    topic, payload, qos, retain = paho.published[-1]
    assert (topic, qos, retain) == ("sentinel/v1/events/garagem", 1, False)
    assert payload == started().to_dict()


def test_invalid_event_is_not_published():
    client, paho, _ = make()
    paho.connect()
    count = len(paho.published)
    bad = type(started())(**{**started().__dict__, "seconds": -1})
    with pytest.raises(InvalidEvent):
        client.publish_event(bad)
    assert len(paho.published) == count


def test_messages_go_to_matching_handlers_and_failures_are_contained():
    client, paho, _ = make("notifier", persistent=True)
    received = []
    client.subscribe(bus.EVENTS_ALL, lambda t, p: received.append(("events", t)))
    client.subscribe("sentinel/v1/status/detector", lambda t, p: 1 / 0)
    paho.connect()
    paho.deliver("sentinel/v1/status/detector", b"{}")
    paho.deliver("sentinel/v1/events/garagem", b"{}")
    assert received == [("events", "sentinel/v1/events/garagem")]


def test_stop_publishes_offline():
    client, paho, _ = make()
    paho.connect()
    client.stop()
    assert paho.published[-1][1] == {
        "state": "offline",
        "since": "2026-10-18T15:00:00Z",
        "detail": "stopped",
    }
    assert paho.disconnected


def test_status_round_trip():
    status = bus.Status("online", datetime(2026, 10, 18, 15, tzinfo=UTC), "ok")
    assert bus.Status.from_json(status.to_json()) == status
    assert bus.Status.from_json('{"state": "offline"}').since is None
    with pytest.raises(ValueError):
        bus.Status.from_json('{"state": "sleeping"}')


# --- integration with the broker container ------------------------------------


def broker_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 1883), timeout=0.2):
            return True
    except OSError:
        return False


def broker_password() -> str | None:
    try:
        return secrets.get(secrets.mqtt_item("admin"))
    except Exception:
        return None


integration = pytest.mark.skipif(
    not broker_running() or broker_password() is None,
    reason="broker container not running (make up) or no broker credentials (make secrets)",
)


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@integration
def test_broker_refuses_anonymous_clients():
    result = {}
    anonymous = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"anon-{uuid.uuid4()}")
    anonymous.on_connect = lambda c, u, f, rc, p: result.setdefault("rc", rc)
    anonymous.connect("127.0.0.1", 1883)
    anonymous.loop_start()
    try:
        assert wait_for(lambda: "rc" in result)
        assert result["rc"].is_failure
    finally:
        anonymous.loop_stop()


@integration
def test_status_last_will_and_persistent_session_against_the_broker():
    password = broker_password()
    suffix = uuid.uuid4().hex[:8]
    producer = bus.BusClient(f"test-producer-{suffix}", "admin", password)
    consumer = bus.BusClient(f"test-consumer-{suffix}", "admin", password, persistent=True)
    received, statuses = [], {}
    consumer.subscribe(bus.events_topic("garagem"), lambda t, p: received.append(json.loads(p)))
    consumer.subscribe(
        bus.status_topic(producer.component),
        lambda t, p: statuses.update(state=json.loads(p)["state"]),
    )
    producer.start()
    consumer.start()
    try:
        assert producer.wait_connected(5) and consumer.wait_connected(5)
        assert wait_for(lambda: statuses.get("state") == "online")

        # An event published while the consumer is away is delivered when it is back.
        consumer.stop()
        producer.publish_event(started()).wait_for_publish(2)
        consumer = bus.BusClient(f"test-consumer-{suffix}", "admin", password, persistent=True)
        consumer.subscribe(bus.events_topic("garagem"), lambda t, p: received.append(json.loads(p)))
        consumer.subscribe(
            bus.status_topic(producer.component),
            lambda t, p: statuses.update(state=json.loads(p)["state"]),
        )
        consumer.start()
        assert wait_for(lambda: received)
        assert received[0]["event_id"] == started().event_id

        # Killing the producer's connection makes the broker publish its Last Will.
        producer._client._sock.close()
        producer._client.loop_stop()
        assert wait_for(lambda: statuses.get("state") == "offline", timeout=60)
    finally:
        consumer.stop()
        cleanup = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"cleanup-{suffix}")
        cleanup.username_pw_set("admin", password)
        cleanup.connect("127.0.0.1", 1883)
        cleanup.publish(bus.status_topic(producer.component), b"", qos=1, retain=True)
        cleanup.publish(bus.status_topic(consumer.component), b"", qos=1, retain=True)
        cleanup.loop(1)
        cleanup.disconnect()
