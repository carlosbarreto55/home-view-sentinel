import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sentinel import secrets
from sentinel.bus import client as bus
from sentinel.bus.client import Status
from sentinel.config import Camera, Notifications
from sentinel.control import follow
from sentinel.control.follow import Follower, Run
from test_events import started

START = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)
CAMERAS = (
    Camera("garagem", "Garagem", 1, "main", ()),
    Camera("rua", "Rua", 2, "main", ()),
)
WATCH = Run(wanted=True, started_at=START)


def make(tmp_path, connected=True):
    snapshots = tmp_path / "snapshots"
    follower = Follower(
        CAMERAS,
        Notifications(),
        snapshots,
        tmp_path / "control" / "last-alert.json",
        stop_marker=tmp_path / "gateway" / "stopped-wrong-login",
    )
    follower.connected = lambda: connected
    return follower


def status(follower, component, state, detail=None):
    payload = Status(state, START, detail).to_json().encode()
    follower.on_status(f"sentinel/v1/status/{component}", payload)


def all_online(follower):
    for component in ("gateway", "notifier", "detector", "camera-garagem", "camera-rua"):
        status(follower, component, bus.ONLINE)


def test_control_has_its_own_broker_user():
    assert "control" in secrets.MQTT_USERS
    assert follow.USER == "control"


def test_off_is_stopped_or_stopping(tmp_path):
    follower = make(tmp_path)
    assert follower.phase(Run(wanted=False), START) == ("stopped", None)
    assert follower.phase(Run(wanted=False, busy=True), START) == ("stopping", None)


def test_starting_until_everything_is_online(tmp_path):
    follower = make(tmp_path)
    assert follower.phase(WATCH, START + timedelta(seconds=5)) == ("starting", None)
    status(follower, "gateway", bus.ONLINE)
    status(follower, "notifier", bus.ONLINE)
    status(follower, "detector", bus.ONLINE)
    status(follower, "camera-garagem", bus.ONLINE)
    status(follower, "camera-rua", bus.RECONNECTING, "starting")
    assert follower.phase(WATCH, START + timedelta(seconds=30)) == ("starting", None)
    status(follower, "camera-rua", bus.ONLINE)
    assert follower.phase(WATCH, START + timedelta(seconds=35)) == ("watching", None)


def test_a_component_not_online_after_2_minutes_is_a_problem(tmp_path):
    follower = make(tmp_path)
    status(follower, "gateway", bus.ONLINE)
    status(follower, "notifier", bus.ONLINE)
    at = START + timedelta(minutes=2)
    assert follower.phase(WATCH, at) == ("problem", "O detector não está funcionando")


def test_the_broker_not_answering(tmp_path):
    follower = make(tmp_path, connected=False)
    assert follower.phase(WATCH, START + timedelta(minutes=3)) == (
        "problem",
        "O broker MQTT não responde",
    )


def test_a_camera_reconnecting_while_watching(tmp_path):
    follower = make(tmp_path)
    all_online(follower)
    assert follower.phase(WATCH, START + timedelta(seconds=40))[0] == "watching"
    status(follower, "camera-rua", bus.RECONNECTING, "no frame for 10 s; retrying in 2 s")
    # Already watched once: a problem right away, not "starting" again.
    assert follower.phase(WATCH, START + timedelta(seconds=50)) == (
        "problem",
        "Câmera Rua sem imagem",
    )
    status(follower, "camera-rua", bus.ONLINE)
    assert follower.phase(WATCH, START + timedelta(seconds=55)) == ("watching", None)


def test_a_rejected_dvr_login_is_a_problem_at_once(tmp_path):
    follower = make(tmp_path)
    status(follower, "gateway", bus.OFFLINE, "Usuário ou senha do DVR incorretos")
    assert follower.phase(WATCH, START + timedelta(seconds=5)) == (
        "problem",
        "Usuário ou senha do DVR incorretos",
    )


def test_the_gateway_stop_marker_is_a_rejected_login(tmp_path):
    follower = make(tmp_path)
    marker = tmp_path / "gateway" / "stopped-wrong-login"
    marker.parent.mkdir()
    marker.write_text("Usuário ou senha do DVR incorretos\n")
    assert follower.phase(WATCH, START) == ("problem", "Usuário ou senha do DVR incorretos")


def test_a_failed_start_is_a_problem(tmp_path):
    follower = make(tmp_path)
    run = replace(WATCH, error="Não foi possível iniciar o broker")
    assert follower.phase(run, START) == ("problem", "Não foi possível iniciar o broker")


def test_reset_starts_over(tmp_path):
    follower = make(tmp_path)
    all_online(follower)
    follower.phase(WATCH, START)
    follower.reset()
    assert follower.phase(WATCH, START + timedelta(seconds=1)) == ("starting", None)


def test_cameras_count_only_while_the_detector_is_online(tmp_path):
    follower = make(tmp_path)
    assert [c.state for c in follower.camera_states()] == ["offline", "offline"]
    # Retained from before the detector stopped: stale.
    status(follower, "camera-garagem", bus.ONLINE)
    status(follower, "detector", bus.OFFLINE, "stopped")
    assert [c.state for c in follower.camera_states()] == ["offline", "offline"]
    status(follower, "detector", bus.ONLINE)
    status(follower, "camera-rua", bus.RECONNECTING, "starting")
    cameras = follower.camera_states()
    assert [(c.id, c.name, c.channel, c.state) for c in cameras] == [
        ("garagem", "Garagem", 1, "online"),
        ("rua", "Rua", 2, "reconnecting"),
    ]


def test_invalid_messages_are_ignored(tmp_path):
    follower = make(tmp_path)
    follower.on_status("sentinel/v1/status/detector", b"not json")
    follower.on_event("sentinel/v1/events/garagem", b'{"schema": "x"}')
    assert follower.last_alert() is None


def snapshot_for(tmp_path, event):
    path = tmp_path / "snapshots" / event.snapshot.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpeg")
    return path


def test_the_last_alert_reads_like_the_push(tmp_path):
    follower = make(tmp_path)
    event = started()
    path = snapshot_for(tmp_path, event)
    follower.on_event("sentinel/v1/events/garagem", event.to_json().encode())
    alert = follower.last_alert()
    assert alert.title == "Moto com piloto parada em frente de casa"
    assert alert.message == "Garagem · Calçada · parada há 30 s · 11:04"
    assert alert.occurred_at == event.occurred_at
    assert (alert.camera_id, alert.camera_name, alert.camera_channel) == ("garagem", "Garagem", 1)
    assert alert.zone == "Calçada"
    assert alert.snapshot == str(path)


def test_the_last_alert_survives_a_restart(tmp_path):
    event = started()
    snapshot_for(tmp_path, event)
    make(tmp_path).on_event("sentinel/v1/events/garagem", event.to_json().encode())
    saved = json.loads((tmp_path / "control" / "last-alert.json").read_text())
    assert saved["event_id"] == event.event_id
    assert make(tmp_path).last_alert().event_id == event.event_id


def test_a_newer_alert_replaces_it_and_an_ended_event_does_not(tmp_path):
    follower = make(tmp_path)
    first = started()
    later = replace(
        first,
        event_id="9a1d2c3b-4e5f-4a6b-8c7d-0e1f2a3b4c5d",
        occurred_at=first.occurred_at + timedelta(minutes=5),
    )
    for event in (first, later):
        snapshot_for(tmp_path, event)
    follower.on_event("t", later.to_json().encode())
    follower.on_event("t", first.to_json().encode())  # arrives late
    follower.on_event(
        "t", later.ended(later.occurred_at + timedelta(minutes=2), 150).to_json().encode()
    )
    assert follower.last_alert().event_id == later.event_id


def test_the_last_alert_is_dropped_with_its_snapshot(tmp_path):
    follower = make(tmp_path)
    event = started()
    path = snapshot_for(tmp_path, event)
    follower.on_event("t", event.to_json().encode())
    path.unlink()  # the 14-day retention
    assert follower.last_alert() is None
    assert not (tmp_path / "control" / "last-alert.json").exists()


class FakeBus:
    def __init__(self, component, user, password, persistent=False):
        self.args = (component, user, password, persistent)
        self.subscriptions = {}
        self.connected = False

    def subscribe(self, topic_filter, handler):
        self.subscriptions[topic_filter] = handler


def test_connect_follows_statuses_and_events_with_a_persistent_session(tmp_path):
    follower = make(tmp_path)
    client = follow.connect(follower, "pw", client_factory=FakeBus)
    assert client.args == ("control", "control", "pw", True)
    assert client.subscriptions == {
        "sentinel/v1/status/+": follower.on_status,
        "sentinel/v1/events/+": follower.on_event,
    }
    assert not follower.connected()
    client.connected = True
    assert follower.connected()
