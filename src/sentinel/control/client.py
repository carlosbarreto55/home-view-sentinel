"""A client of the control agent, as HomeView is: write a request, read the state.

``make up``, ``make watch`` and ``make down`` use it, so they and other apps
always agree on the mode: only the agent changes it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sentinel.control.contract import ControlState, InvalidState, Request
from sentinel.control.files import REQUEST_FILE, STATE_FILE, write_private

ANSWER_TIMEOUT = 15.0
STALE_AFTER = timedelta(seconds=15)


def new_request(mode: str, hours: int | None = None) -> Request:
    return Request(str(uuid.uuid4()), mode, hours)


def send(directory: Path, request: Request) -> None:
    """Write ``request.json`` atomically (a newer request replaces one not read yet)."""
    write_private(directory / REQUEST_FILE, request.to_json() + "\n")


def read_state(directory: Path) -> ControlState | None:
    """The agent's state, or None when there is none or it cannot be read."""
    try:
        return ControlState.from_json((directory / STATE_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, InvalidState):
        return None


def stale(state: ControlState, now: datetime) -> bool:
    return now - state.written_at > STALE_AFTER


def wait_for(
    directory: Path,
    request_id: str,
    timeout: float = ANSWER_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ControlState | None:
    """The state once the agent reports the request applied or refused, or None
    when it did not answer within ``timeout`` seconds."""
    deadline = monotonic() + timeout
    while True:
        state = read_state(directory)
        if state is not None and request_id in (state.applied_request, state.refused_request):
            return state
        if monotonic() >= deadline:
            return None
        sleep(0.25)


def applied(state: ControlState, request_id: str) -> bool:
    """Whether the request was applied (else it was refused)."""
    return state.applied_request == request_id
