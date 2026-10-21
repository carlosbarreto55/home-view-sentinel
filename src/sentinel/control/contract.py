"""The control contract: models serialized to contracts/control-state.v1.schema.json
and read from contracts/control-request.v1.schema.json.

The JSON Schemas are the source of truth, shared with HomeView; the state is
validated before it is written and every request before it is applied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from sentinel.bus.events import parse_utc, utc_text

STATE_SCHEMA_ID = "sentinel.control-state/v1"
REQUEST_SCHEMA_ID = "sentinel.control-request/v1"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
STATE_SCHEMA_PATH = CONTRACTS / "control-state.v1.schema.json"
REQUEST_SCHEMA_PATH = CONTRACTS / "control-request.v1.schema.json"

OFF, ALWAYS, TIMED = "off", "always", "timed"
DURATIONS_HOURS = (1, 2, 4, 8, 12)
STOPPED, STARTING, WATCHING, STOPPING, PROBLEM = (
    "stopped",
    "starting",
    "watching",
    "stopping",
    "problem",
)
AC, BATTERY = "ac", "battery"
UNKNOWN_REQUEST = "unknown"  # refused_request for a request without a readable id

_ID_IN_TEXT = re.compile(r'"id"\s*:\s*"([^"\\]{1,128})"')


class InvalidState(ValueError):
    pass


class InvalidRequest(ValueError):
    """A request that is refused. ``request_id`` is its id, or ``UNKNOWN_REQUEST``."""

    def __init__(self, request_id: str, reason: str):
        self.request_id = request_id
        self.reason = reason
        super().__init__(reason)


@cache
def _validator(path: Path) -> jsonschema.protocols.Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _first_error(path: Path, data: Any) -> str | None:
    errors = sorted(_validator(path).iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.absolute_path) or "<root>"
    return f"{where}: {first.message}"


def validate_state(data: dict[str, Any]) -> None:
    problem = _first_error(STATE_SCHEMA_PATH, data)
    if problem:
        raise InvalidState(problem)


def _utc_or_none(moment: datetime | None) -> str | None:
    return utc_text(moment) if moment is not None else None


@dataclass(frozen=True)
class CameraState:
    id: str
    name: str
    channel: int
    state: str  # online, reconnecting or offline


@dataclass(frozen=True)
class LastAlert:
    event_id: str
    title: str
    message: str
    occurred_at: datetime
    camera_id: str
    camera_name: str
    camera_channel: int
    zone: str
    snapshot: str  # absolute path

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "message": self.message,
            "occurred_at": utc_text(self.occurred_at),
            "camera": {
                "id": self.camera_id,
                "name": self.camera_name,
                "channel": self.camera_channel,
            },
            "zone": self.zone,
            "snapshot": self.snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LastAlert:
        return cls(
            event_id=data["event_id"],
            title=data["title"],
            message=data["message"],
            occurred_at=parse_utc(data["occurred_at"]),
            camera_id=data["camera"]["id"],
            camera_name=data["camera"]["name"],
            camera_channel=data["camera"]["channel"],
            zone=data["zone"],
            snapshot=data["snapshot"],
        )


@dataclass(frozen=True)
class ControlState:
    written_at: datetime
    mode: str
    until: datetime | None
    phase: str
    detail: str | None
    power: str
    kept_awake: bool
    cameras: tuple[CameraState, ...]
    applied_request: str | None
    refused_request: str | None
    last_alert: LastAlert | None
    durations_hours: tuple[int, ...] = DURATIONS_HOURS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA_ID,
            "written_at": utc_text(self.written_at),
            "mode": self.mode,
            "until": _utc_or_none(self.until),
            "phase": self.phase,
            "detail": self.detail,
            "power": self.power,
            "kept_awake": self.kept_awake,
            "cameras": [
                {"id": c.id, "name": c.name, "channel": c.channel, "state": c.state}
                for c in self.cameras
            ],
            "durations_hours": list(self.durations_hours),
            "applied_request": self.applied_request,
            "refused_request": self.refused_request,
            "last_alert": self.last_alert.to_dict() if self.last_alert else None,
        }

    def to_json(self) -> str:
        """The validated JSON text; raises InvalidState instead of producing a bad one."""
        data = self.to_dict()
        validate_state(data)
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlState:
        validate_state(data)
        return cls(
            written_at=parse_utc(data["written_at"]),
            mode=data["mode"],
            until=parse_utc(data["until"]) if data["until"] else None,
            phase=data["phase"],
            detail=data["detail"],
            power=data["power"],
            kept_awake=data["kept_awake"],
            cameras=tuple(CameraState(**c) for c in data["cameras"]),
            durations_hours=tuple(data["durations_hours"]),
            applied_request=data["applied_request"],
            refused_request=data["refused_request"],
            last_alert=LastAlert.from_dict(data["last_alert"]) if data["last_alert"] else None,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> ControlState:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidState(f"not JSON: {error}") from None
        if not isinstance(data, dict):
            raise InvalidState("<root>: must be an object")
        return cls.from_dict(data)


@dataclass(frozen=True)
class Request:
    id: str
    mode: str
    hours: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"schema": REQUEST_SCHEMA_ID, "id": self.id, "mode": self.mode}
        if self.hours is not None:
            data["hours"] = self.hours
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str | bytes) -> Request:
        """The request in ``text``; raises InvalidRequest (with the id when it can
        be read) for bad JSON or anything the schema does not allow."""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            found = _ID_IN_TEXT.search(text)
            request_id = found[1] if found else UNKNOWN_REQUEST
            raise InvalidRequest(request_id, f"not JSON: {error}") from None
        request_id = UNKNOWN_REQUEST
        if isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]:
            request_id = data["id"][:128]
        problem = _first_error(REQUEST_SCHEMA_PATH, data)
        if problem:
            raise InvalidRequest(request_id, problem)
        return cls(id=data["id"], mode=data["mode"], hours=data.get("hours"))
