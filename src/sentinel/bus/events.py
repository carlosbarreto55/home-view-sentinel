"""Event models serialized to the v1 contract (contracts/event.v1.schema.json).

The JSON Schema is the source of truth; ``validate`` checks every event against
it, and producers never publish an event that fails.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_ID = "sentinel.event/v1"
DWELL_STARTED = "dwell.started"
DWELL_ENDED = "dwell.ended"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "event.v1.schema.json"


class InvalidEvent(ValueError):
    pass


@cache
def _validator() -> jsonschema.protocols.Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def validate(data: dict[str, Any]) -> None:
    """Raise InvalidEvent unless ``data`` follows the v1 contract."""
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<event>"
        raise InvalidEvent(f"{where}: {first.message}")


def utc_text(moment: datetime) -> str:
    """``2026-10-18T15:04:37Z`` (seconds precision, UTC)."""
    if moment.tzinfo is None:
        raise ValueError("times must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def new_event_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class CameraRef:
    id: str
    name: str
    channel: int


@dataclass(frozen=True)
class ZoneRef:
    id: str
    name: str


@dataclass(frozen=True)
class ObjectRef:
    vehicle_class: str  # "car" or "motorcycle"
    with_rider: bool
    track_id: str
    confidence: float


@dataclass(frozen=True)
class SnapshotRef:
    file: str
    width: int
    height: int


@dataclass(frozen=True)
class DwellEvent:
    type: str
    event_id: str
    occurred_at: datetime
    camera: CameraRef
    zone: ZoneRef
    object: ObjectRef
    since: datetime
    seconds: int
    snapshot: SnapshotRef
    started_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": SCHEMA_ID,
            "event_id": self.event_id,
            "type": self.type,
            "occurred_at": utc_text(self.occurred_at),
            "camera": {
                "id": self.camera.id,
                "name": self.camera.name,
                "channel": self.camera.channel,
            },
            "zone": {"id": self.zone.id, "name": self.zone.name},
            "object": {
                "class": self.object.vehicle_class,
                "with_rider": self.object.with_rider,
                "track_id": self.object.track_id,
                "confidence": round(self.object.confidence, 2),
            },
            "dwell": {"since": utc_text(self.since), "seconds": self.seconds},
            "snapshot": {
                "file": self.snapshot.file,
                "width": self.snapshot.width,
                "height": self.snapshot.height,
            },
        }
        if self.started_event_id is not None:
            data["started_event_id"] = self.started_event_id
        return data

    def to_json(self) -> str:
        """The validated JSON payload; raises InvalidEvent instead of producing a bad one."""
        data = self.to_dict()
        validate(data)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DwellEvent:
        validate(data)
        return cls(
            type=data["type"],
            event_id=data["event_id"],
            occurred_at=parse_utc(data["occurred_at"]),
            camera=CameraRef(**data["camera"]),
            zone=ZoneRef(**data["zone"]),
            object=ObjectRef(
                vehicle_class=data["object"]["class"],
                with_rider=data["object"]["with_rider"],
                track_id=data["object"]["track_id"],
                confidence=data["object"]["confidence"],
            ),
            since=parse_utc(data["dwell"]["since"]),
            seconds=data["dwell"]["seconds"],
            snapshot=SnapshotRef(**data["snapshot"]),
            started_event_id=data.get("started_event_id"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> DwellEvent:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as error:
            raise InvalidEvent(f"not JSON: {error}") from None
        if not isinstance(data, dict):
            raise InvalidEvent("<event>: must be an object")
        return cls.from_dict(data)

    def ended(self, occurred_at: datetime, seconds: int) -> DwellEvent:
        """The ``dwell.ended`` event closing this ``dwell.started``."""
        return DwellEvent(
            type=DWELL_ENDED,
            event_id=new_event_id(),
            occurred_at=occurred_at,
            camera=self.camera,
            zone=self.zone,
            object=self.object,
            since=self.since,
            seconds=seconds,
            snapshot=self.snapshot,
            started_event_id=self.event_id,
        )
