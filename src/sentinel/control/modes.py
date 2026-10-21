"""The watch mode: off, 24/7 (``always``) or timed until a wall-clock time.

::

               watch(h ∈ {1,2,4,8,12})          now ≥ until
       OFF ─────────────────────────▶ TIMED(until) ────────────▶ OFF
        ▲  ╲                            │  ▲ watch(h): until = now + h
        │   ╲ always                    │  │
        │    ▼                          ▼  │
        └──── ALWAYS ◀──── always ──────┘──┘
         off                        (off from any state → OFF)

``ModeMachine`` takes requests and the time from an injected clock and does no
I/O; ``load`` and ``save`` keep the mode in ``control/mode.json``. A wake from
sleep is just a ``tick`` where the time jumped, so an expired timer turns off then.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.bus.events import parse_utc, utc_text
from sentinel.control.contract import ALWAYS, DURATIONS_HOURS, OFF, TIMED
from sentinel.control.files import write_private

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class Refused(ValueError):
    """A mode request that cannot be applied; the mode does not change."""


@dataclass(frozen=True)
class Mode:
    name: str
    until: datetime | None = None  # only for timed

    @property
    def watching(self) -> bool:
        """Whether the components should run."""
        return self.name != OFF

    def to_dict(self) -> dict:
        data: dict = {"mode": self.name}
        if self.until is not None:
            data["until"] = utc_text(self.until)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Mode:
        name = data.get("mode")
        if name in (OFF, ALWAYS):
            return cls(name)
        if name == TIMED:
            return cls(TIMED, parse_utc(data["until"]))
        raise ValueError(f"unknown mode {name!r}")


OFF_MODE = Mode(OFF)


class ModeMachine:
    def __init__(self, mode: Mode = OFF_MODE, clock: Clock = lambda: datetime.now(UTC)):
        self.clock = clock
        self.mode = mode
        self.tick()

    def request(self, name: str, hours: int | None = None) -> Mode:
        """Apply a request and return the new mode; raises Refused."""
        if name == OFF:
            self.mode = OFF_MODE
        elif name == ALWAYS:
            self.mode = Mode(ALWAYS)
        elif name == TIMED:
            if hours not in DURATIONS_HOURS or isinstance(hours, bool):
                offered = ", ".join(str(h) for h in DURATIONS_HOURS)
                raise Refused(f"Duração de {hours} h não oferecida (use {offered} h)")
            # Seconds precision, as in the state file.
            now = self.clock().astimezone(UTC).replace(microsecond=0)
            self.mode = Mode(TIMED, now + timedelta(hours=hours))
        else:
            raise Refused(f"Modo desconhecido: {name!r}")
        return self.mode

    def tick(self) -> bool:
        """Turn off a timed watch that is over; returns whether the mode changed."""
        if self.mode.name == TIMED and self.clock() >= self.mode.until:
            log.info("the timed watch until %s is over", utc_text(self.mode.until))
            self.mode = OFF_MODE
            return True
        return False


def load(path: Path) -> Mode:
    """The saved mode; off when there is none (first install) or it is unreadable."""
    try:
        return Mode.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return OFF_MODE
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning("ignored an unreadable %s (%s); the mode is off", path.name, error)
        return OFF_MODE


def save(path: Path, mode: Mode) -> None:
    write_private(path, json.dumps(mode.to_dict()) + "\n")
