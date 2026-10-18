"""What each push says, in Portuguese, and at which priority.

Pure functions: an event (or a status change) and the settings in, a ``Push``
out. Times are shown in the configured time zone (Etc/GMT+4).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from sentinel.bus.events import DWELL_ENDED, DWELL_STARTED, DwellEvent
from sentinel.config import Notifications

LOW, DEFAULT, HIGH = "low", "default", "high"
SEPARATOR = " · "


@dataclass(frozen=True)
class Push:
    title: str
    message: str
    priority: str = DEFAULT
    tags: tuple[str, ...] = ()
    snapshot: str | None = None  # path relative to the snapshots folder


def duration(seconds: int) -> str:
    """``30 s``, ``2 min``, ``1 h 5 min`` (minutes and hours rounded down)."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def local_clock(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).strftime("%H:%M")


def _stopped_word(vehicle_class: str) -> str:
    # "a moto" is feminine, "o carro" masculine.
    return "parada" if vehicle_class == "motorcycle" else "parado"


def _title(event: DwellEvent) -> str:
    if event.object.vehicle_class == "motorcycle":
        if event.object.with_rider:
            return "Moto com piloto parada em frente de casa"
        return "Moto parada em frente de casa"
    return "Carro parado em frente de casa"


def for_event(event: DwellEvent, settings: Notifications) -> Push | None:
    """The push for an event, or None when that kind of event is not sent."""
    where = SEPARATOR.join([event.camera.name, event.zone.name])
    if event.type == DWELL_STARTED:
        vehicle = event.object.vehicle_class
        message = SEPARATOR.join(
            [
                where,
                f"{_stopped_word(vehicle)} há {duration(event.seconds)}",
                local_clock(event.occurred_at, settings.timezone),
            ]
        )
        push = Push(
            title=_title(event),
            message=message,
            priority=HIGH if vehicle == "motorcycle" and event.object.with_rider else DEFAULT,
            tags=("motorcycle",) if vehicle == "motorcycle" else ("red_car",),
            snapshot=event.snapshot.file,
        )
    elif event.type == DWELL_ENDED:
        if not settings.send_ended:
            return None
        push = Push(
            title=f"Saiu após {duration(event.seconds)}",
            message=where,
            priority=LOW,
            tags=("wave",),
        )
    else:
        return None
    return quiet(push, event.occurred_at, settings)


def detector_offline(at: datetime, settings: Notifications) -> Push:
    push = Push(
        "O Sentinel parou de vigiar as câmeras",
        "O detector está parado há mais de 2 minutos.",
        HIGH,
        ("warning",),
    )
    return quiet(push, at, settings)


def detector_online(at: datetime, settings: Notifications) -> Push:
    push = Push(
        "O Sentinel voltou a vigiar",
        "As câmeras estão sendo vigiadas de novo.",
        DEFAULT,
        ("white_check_mark",),
    )
    return quiet(push, at, settings)


def camera_without_picture(camera_name: str, at: datetime, settings: Notifications) -> Push:
    push = Push(
        f"Sem imagem da câmera {camera_name}",
        "A câmera está sem imagem há mais de 10 minutos.",
        DEFAULT,
        ("warning",),
    )
    return quiet(push, at, settings)


def quiet(push: Push, at: datetime, settings: Notifications) -> Push:
    """During quiet hours every push still goes out, at low priority (no sound)."""
    hours = settings.quiet_hours
    if hours is None:
        return push
    local = at.astimezone(ZoneInfo(settings.timezone)).time()
    return replace(push, priority=LOW) if hours.contains(local) else push
