import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

from sentinel.bus.client import Status
from sentinel.config import Notifications, QuietHours
from sentinel.notifier.service import Notifier
from test_events import started

START = datetime(2026, 10, 19, 15, 0, 0, tzinfo=UTC)
SETTINGS = Notifications()
NAMES = {"garagem": "Garagem", "rua": "Rua"}


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def notifier(settings=SETTINGS):
    clock, sent = Clock(), []
    return Notifier(settings, NAMES, sent.append, clock), clock, sent


def status(component, state, since=None, detail=None):
    payload = Status(state, since, detail).to_json().encode()
    return f"sentinel/v1/status/{component}", payload


def run_for(n, clock, seconds, step=5):
    for _ in range(int(seconds // step)):
        clock.advance(step)
        n.tick()


def titles(sent):
    return [p.title for p in sent]


def test_a_short_restart_sends_nothing():
    n, clock, sent = notifier()
    n.on_status(*status("detector", "online", START))
    clock.advance(60)
    n.on_status(*status("detector", "offline", clock.now, "stopped"))
    run_for(n, clock, 20)
    n.on_status(*status("detector", "online", clock.now))
    run_for(n, clock, 300)
    assert sent == []


def test_detector_down_for_3_minutes_sends_offline_then_online_once_each():
    n, clock, sent = notifier()
    n.on_status(*status("detector", "online", START))
    n.on_status(*status("detector", "offline", None, "connection lost"))  # Last Will
    run_for(n, clock, 115)
    assert sent == []
    run_for(n, clock, 65)
    assert titles(sent) == ["O Sentinel parou de vigiar as câmeras"]
    assert sent[0].priority == "high"
    run_for(n, clock, 600)
    n.on_status(*status("detector", "offline", clock.now))  # repeated status: no new push
    run_for(n, clock, 300)
    n.on_status(*status("detector", "online", clock.now))
    n.on_status(*status("detector", "online", clock.now))
    assert titles(sent) == ["O Sentinel parou de vigiar as câmeras", "O Sentinel voltou a vigiar"]


def test_a_status_retained_from_before_the_start_counts_from_the_start():
    # The Mac was off overnight: the broker kept "offline since yesterday".
    n, clock, sent = notifier()
    n.on_status(*status("detector", "offline", START - timedelta(hours=10), "stopped"))
    run_for(n, clock, 40)
    n.on_status(*status("detector", "online", clock.now))
    run_for(n, clock, 600)
    assert sent == []


def test_a_camera_without_picture_for_10_minutes_alerts_once():
    n, clock, sent = notifier()
    n.on_status(*status("detector", "online", START))
    n.on_status(*status("camera-rua", "reconnecting", START, "no frame for 10 s"))
    run_for(n, clock, 595)
    assert sent == []
    run_for(n, clock, 3600)
    assert titles(sent) == ["Sem imagem da câmera Rua"]
    # Back, then lost again: a new problem, a new push after 10 minutes.
    n.on_status(*status("camera-rua", "online", clock.now))
    n.on_status(*status("camera-rua", "reconnecting", clock.now))
    run_for(n, clock, 605)
    assert titles(sent) == ["Sem imagem da câmera Rua"] * 2


def test_camera_statuses_are_ignored_while_the_detector_is_down():
    n, clock, sent = notifier()
    n.on_status(*status("camera-garagem", "reconnecting", START))
    n.on_status(*status("detector", "offline", START))
    run_for(n, clock, 900)
    assert titles(sent) == ["O Sentinel parou de vigiar as câmeras"]


def test_other_components_and_bad_statuses_are_ignored():
    n, clock, sent = notifier()
    n.on_status(*status("gateway", "offline", START, "Usuário ou senha do DVR incorretos"))
    n.on_status("sentinel/v1/status/detector", b'{"state": "sleeping"}')
    n.on_status("sentinel/v1/status/detector", b"not json")
    run_for(n, clock, 900)
    assert sent == []


def test_quiet_hours_lower_the_offline_alert():
    quiet = replace(SETTINGS, quiet_hours=QuietHours(time(23, 0), time(6, 0)))
    clock, sent = Clock(datetime(2026, 10, 19, 7, 10, tzinfo=UTC)), []  # 03:10 at UTC-4
    n = Notifier(quiet, NAMES, sent.append, clock)
    n.on_status(*status("detector", "offline", clock.now))
    run_for(n, clock, 150)
    assert [(p.title, p.priority) for p in sent] == [
        ("O Sentinel parou de vigiar as câmeras", "low")
    ]


def test_an_event_becomes_one_push_with_its_snapshot():
    n, clock, sent = notifier()
    payload = started().to_json().encode()
    n.on_event("sentinel/v1/events/garagem", payload)
    n.on_event("sentinel/v1/events/garagem", payload)  # QoS 1 duplicate
    assert len(sent) == 1
    push = sent[0]
    assert push.title == "Moto com piloto parada em frente de casa"
    assert push.message == "Garagem · Calçada · parada há 30 s · 11:04"
    assert push.snapshot == "garagem/2026-10-18T15-04-37Z-garagem-42.jpg"


def test_ended_events_follow_the_setting():
    event = started().ended(datetime(2026, 10, 18, 15, 11, 37, tzinfo=UTC), 420)
    n, _, sent = notifier()
    n.on_event("sentinel/v1/events/garagem", event.to_json().encode())
    assert sent == []
    n, _, sent = notifier(replace(SETTINGS, send_ended=True))
    n.on_event("sentinel/v1/events/garagem", event.to_json().encode())
    assert titles(sent) == ["Saiu após 7 min"]


def test_an_invalid_event_is_logged_and_skipped(caplog):
    n, _, sent = notifier()
    bad = json.loads(started().to_json())
    del bad["camera"]
    n.on_event("sentinel/v1/events/garagem", json.dumps(bad).encode())
    n.on_event("sentinel/v1/events/garagem", b"{")
    assert sent == []
    assert "ignored an invalid event" in caplog.text
