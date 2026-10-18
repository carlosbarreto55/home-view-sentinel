from dataclasses import replace
from datetime import UTC, datetime, time

import pytest

from sentinel.config import Notifications, QuietHours
from sentinel.notifier import messages
from sentinel.notifier.messages import DEFAULT, HIGH, LOW, Push
from test_events import started

SETTINGS = Notifications()
NIGHT = Notifications(quiet_hours=QuietHours(time(23, 0), time(6, 0)))


def event(
    vehicle="motorcycle", rider=True, seconds=30, at=datetime(2026, 10, 18, 15, 4, 37, tzinfo=UTC)
):
    base = started()
    return replace(
        base,
        occurred_at=at,
        seconds=seconds,
        object=replace(base.object, vehicle_class=vehicle, with_rider=rider),
    )


def test_motorcyclist_stopped_at_the_gate():
    push = messages.for_event(event(), SETTINGS)
    assert push == Push(
        title="Moto com piloto parada em frente de casa",
        message="Garagem · Calçada · parada há 30 s · 11:04",
        priority=HIGH,
        tags=("motorcycle",),
        snapshot="garagem/2026-10-18T15-04-37Z-garagem-42.jpg",
    )


def test_motorcycle_without_rider():
    push = messages.for_event(event(rider=False), SETTINGS)
    assert push.title == "Moto parada em frente de casa"
    assert push.message == "Garagem · Calçada · parada há 30 s · 11:04"
    assert push.priority == DEFAULT


def test_car():
    push = messages.for_event(event("car", rider=False, seconds=120), SETTINGS)
    assert push.title == "Carro parado em frente de casa"
    assert push.message == "Garagem · Calçada · parado há 2 min · 11:04"
    assert push.priority == DEFAULT
    assert push.tags == ("red_car",)
    assert push.snapshot is not None


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0 s"),
        (30, "30 s"),
        (59, "59 s"),
        (60, "1 min"),
        (150, "2 min"),
        (420, "7 min"),
        (3599, "59 min"),
        (3600, "1 h"),
        (3900, "1 h 5 min"),
        (7200 + 59, "2 h"),
    ],
)
def test_duration(seconds, text):
    assert messages.duration(seconds) == text


def test_end_notifications_are_off_by_default():
    end = started().ended(datetime(2026, 10, 18, 15, 11, 7, tzinfo=UTC), 420)
    assert messages.for_event(end, SETTINGS) is None


def test_end_notification_when_enabled():
    end = started().ended(datetime(2026, 10, 18, 15, 11, 7, tzinfo=UTC), 420)
    push = messages.for_event(end, Notifications(send_ended=True))
    assert push.title == "Saiu após 7 min"
    assert push.message == "Garagem · Calçada"
    assert push.priority == LOW
    assert push.snapshot is None


def test_a_car_at_3_am_during_quiet_hours_is_silent():
    at = datetime(2026, 10, 19, 7, 10, tzinfo=UTC)  # 03:10 at UTC-4
    push = messages.for_event(event("car", rider=False, seconds=120, at=at), NIGHT)
    assert push.priority == LOW
    assert push.message.endswith("03:10")


def test_a_motorcyclist_outside_quiet_hours_keeps_high_priority():
    at = datetime(2026, 10, 18, 22, 0, tzinfo=UTC)  # 18:00 at UTC-4
    assert messages.for_event(event(at=at), NIGHT).priority == HIGH


def test_without_quiet_hours_priorities_are_normal():
    at = datetime(2026, 10, 19, 7, 10, tzinfo=UTC)
    assert messages.for_event(event(at=at), SETTINGS).priority == HIGH


def test_detector_offline_and_back():
    at = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
    offline = messages.detector_offline(at, SETTINGS)
    online = messages.detector_online(at, SETTINGS)
    assert offline.title == "O Sentinel parou de vigiar as câmeras"
    assert offline.priority == HIGH
    assert online.title == "O Sentinel voltou a vigiar"
    assert (
        messages.detector_offline(datetime(2026, 10, 19, 7, 0, tzinfo=UTC), NIGHT).priority == LOW
    )


def test_camera_without_picture():
    push = messages.camera_without_picture(
        "Garagem", datetime(2026, 10, 18, 15, tzinfo=UTC), SETTINGS
    )
    assert push.title == "Sem imagem da câmera Garagem"


def test_messages_carry_no_image_data_or_credentials():
    push = messages.for_event(event(), SETTINGS)
    text = push.title + push.message
    assert "rtsp://" not in text and "base64" not in text
