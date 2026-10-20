"""The control folder's files, shared with other apps on the Mac.

Everything is in ``control/`` under Sentinel's Application Support folder, which
only the user can open (0700); files are replaced atomically and owner-only (0600).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from sentinel.control.contract import ControlState, InvalidRequest, Request


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` atomically with an owner-only file in an owner-only folder."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(temporary, path)


MODE_FILE = "mode.json"
STATE_FILE = "state.json"
REQUEST_FILE = "request.json"
ALERT_FILE = "last-alert.json"
REWRITE_SECONDS = 5


class ControlFiles:
    """``state.json`` out, ``request.json`` in."""

    def __init__(self, directory: Path, clock: Callable[[], datetime]):
        self.directory = directory
        self.clock = clock
        self._written: dict | None = None
        self._written_at: datetime | None = None

    @property
    def state_path(self) -> Path:
        return self.directory / STATE_FILE

    @property
    def request_path(self) -> Path:
        return self.directory / REQUEST_FILE

    @property
    def mode_path(self) -> Path:
        return self.directory / MODE_FILE

    @property
    def alert_path(self) -> Path:
        return self.directory / ALERT_FILE

    def prepare(self) -> None:
        """Create the folder, owner-only even if it existed with other permissions."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)

    def write_state(self, state: ControlState) -> bool:
        """Write the state when it changed or every 5 seconds; returns whether it wrote."""
        now = self.clock()
        content = state.to_dict()
        del content["written_at"]
        due = (
            self._written_at is None
            or now - self._written_at >= timedelta(seconds=REWRITE_SECONDS)
            or now < self._written_at  # the clock went back
        )
        if content == self._written and not due:
            return False
        write_private(self.state_path, replace(state, written_at=now).to_json())
        self._written, self._written_at = content, now
        return True

    def take_request(self) -> Request | InvalidRequest | None:
        """The pending request (or why it is refused), removed from the folder.

        It is renamed away before it is read, so a newer request written in the
        meantime is kept for the next look instead of being deleted unread."""
        taken = self.directory / f".{REQUEST_FILE}.taken"
        try:
            os.replace(self.request_path, taken)
        except FileNotFoundError:
            return None
        try:
            text = taken.read_bytes()
        finally:
            taken.unlink(missing_ok=True)
        try:
            return Request.from_json(text)
        except InvalidRequest as refused:
            return refused
