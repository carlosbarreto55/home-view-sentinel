"""Load and validate config/sentinel.yaml.

Every error names the field it is about, with list items named by their id,
e.g. ``cameras[garagem].zones[calcada].polygon: needs at least 3 points``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULT_PATH = Path("config/sentinel.yaml")

VEHICLE_CLASSES = ("motorcycle", "car")
STREAMS = {"main": 0, "sub": 1}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(ValueError):
    """Invalid configuration. ``field`` is the path of the offending field."""

    def __init__(self, field: str, problem: str):
        self.field = field
        self.problem = problem
        super().__init__(f"{field}: {problem}" if field else problem)


@dataclass(frozen=True)
class DVR:
    host: str = "192.0.2.10"
    port: int = 554


@dataclass(frozen=True)
class Sampling:
    fps: float = 5


@dataclass(frozen=True)
class Model:
    name: str = "yolo11n"
    min_confidence: float = 0.4


@dataclass(frozen=True)
class Dwell:
    thresholds: dict[str, float] = field(default_factory=lambda: {"motorcycle": 30.0, "car": 120.0})
    still_window_seconds: float = 5
    still_max_movement: float = 0.02
    movement_grace_seconds: float = 3
    end_grace_seconds: float = 10
    cooldown_seconds: float = 600
    reid_max_distance: float = 0.05
    reid_max_age_seconds: float = 120


@dataclass(frozen=True)
class Snapshots:
    retention_days: int = 14


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        """Whether local time ``t`` is inside the quiet hours (which may cross midnight)."""
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


@dataclass(frozen=True)
class Notifications:
    ntfy_topic: str = "sentinel"
    timezone: str = "Etc/GMT+4"
    send_ended: bool = False
    quiet_hours: QuietHours | None = None


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    channel: int
    stream: str
    zones: tuple[Zone, ...]
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def subtype(self) -> int:
        return STREAMS[self.stream]


@dataclass(frozen=True)
class Config:
    cameras: tuple[Camera, ...]
    dvr: DVR = DVR()
    sampling: Sampling = Sampling()
    model: Model = Model()
    dwell: Dwell = Dwell()
    snapshots: Snapshots = Snapshots()
    notifications: Notifications = Notifications()

    def camera(self, camera_id: str) -> Camera:
        for camera in self.cameras:
            if camera.id == camera_id:
                return camera
        raise KeyError(camera_id)

    def threshold(self, camera: Camera, vehicle_class: str) -> float:
        """Dwell threshold in seconds for a class on a camera (camera override first)."""
        return camera.thresholds.get(vehicle_class, self.dwell.thresholds[vehicle_class])


def load(path: Path | str = DEFAULT_PATH) -> Config:
    """Read and validate a configuration file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            "", f"{path} not found (copy config/sentinel.example.yaml to start)"
        ) from None
    return parse(text, source=str(path))


def parse(text: str, source: str = "<config>") -> Config:
    """Validate the YAML text of a configuration."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError("", f"{source} is not valid YAML: {error}") from None
    return from_dict(data)


def from_dict(data: Any) -> Config:
    root = _Section(data, "")
    config = Config(
        dvr=_dvr(root.section("dvr")),
        sampling=_sampling(root.section("sampling")),
        model=_model(root.section("model")),
        dwell=_dwell(root.section("dwell")),
        snapshots=_snapshots(root.section("snapshots")),
        notifications=_notifications(root.section("notifications")),
        cameras=_cameras(root.required("cameras"), "cameras"),
    )
    root.no_unknown_keys()
    return config


# --- sections ---------------------------------------------------------------


def _dvr(s: _Section) -> DVR:
    value = DVR(host=s.string("host", DVR.host), port=s.integer("port", DVR.port, 1, 65535))
    s.no_unknown_keys()
    return value


def _sampling(s: _Section) -> Sampling:
    value = Sampling(fps=s.number("fps", Sampling.fps, minimum=0.5, maximum=30))
    s.no_unknown_keys()
    return value


def _model(s: _Section) -> Model:
    value = Model(
        name=s.string("name", Model.name),
        min_confidence=s.number("min_confidence", Model.min_confidence, minimum=0, maximum=1),
    )
    s.no_unknown_keys()
    return value


def _dwell(s: _Section) -> Dwell:
    d = Dwell()
    thresholds = dict(d.thresholds)
    thresholds.update(_thresholds(s.section("thresholds")))
    value = Dwell(
        thresholds=thresholds,
        still_window_seconds=s.number("still_window_seconds", d.still_window_seconds, 0.5),
        still_max_movement=s.number("still_max_movement", d.still_max_movement, 0, 1),
        movement_grace_seconds=s.number("movement_grace_seconds", d.movement_grace_seconds, 0),
        end_grace_seconds=s.number("end_grace_seconds", d.end_grace_seconds, 0),
        cooldown_seconds=s.number("cooldown_seconds", d.cooldown_seconds, 0),
        reid_max_distance=s.number("reid_max_distance", d.reid_max_distance, 0, 1),
        reid_max_age_seconds=s.number("reid_max_age_seconds", d.reid_max_age_seconds, 0),
    )
    s.no_unknown_keys()
    return value


def _thresholds(s: _Section) -> dict[str, float]:
    values = {}
    for vehicle_class in VEHICLE_CLASSES:
        if s.has(vehicle_class):
            values[vehicle_class] = s.number(vehicle_class, 0, minimum=1)
    s.no_unknown_keys()
    return values


def _snapshots(s: _Section) -> Snapshots:
    value = Snapshots(
        retention_days=s.integer("retention_days", Snapshots.retention_days, minimum=1)
    )
    s.no_unknown_keys()
    return value


def _notifications(s: _Section) -> Notifications:
    n = Notifications()
    topic = s.string("ntfy_topic", n.ntfy_topic)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
        raise ConfigError(s.path("ntfy_topic"), "use letters, digits, - or _ (up to 64)")
    timezone = s.string("timezone", n.timezone)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(s.path("timezone"), f"unknown time zone {timezone!r}") from None
    quiet = None
    if s.has("quiet_hours") and s.raw("quiet_hours") is not None:
        q = s.section("quiet_hours")
        quiet = QuietHours(start=q.clock("start"), end=q.clock("end"))
        if quiet.start == quiet.end:
            raise ConfigError(q.path("end"), "must differ from start")
        q.no_unknown_keys()
    value = Notifications(
        ntfy_topic=topic,
        timezone=timezone,
        send_ended=s.boolean("send_ended", n.send_ended),
        quiet_hours=quiet,
    )
    s.no_unknown_keys()
    return value


def _cameras(data: Any, path: str) -> tuple[Camera, ...]:
    if not isinstance(data, list):
        raise ConfigError(path, "must be a list")
    if not data:
        raise ConfigError(path, "needs at least one camera")
    cameras: list[Camera] = []
    for index, item in enumerate(data):
        camera = _camera(item, _item_path(path, item, index))
        for other in cameras:
            if other.id == camera.id:
                raise ConfigError(f"{path}[{camera.id}].id", "duplicate camera id")
            if (other.channel, other.stream) == (camera.channel, camera.stream):
                raise ConfigError(
                    f"{path}[{camera.id}].channel",
                    f"channel {camera.channel} ({camera.stream}) is already camera {other.id}",
                )
        cameras.append(camera)
    return tuple(cameras)


def _camera(data: Any, path: str) -> Camera:
    s = _Section(data, path)
    camera_id = s.identifier("id")
    stream = s.string("stream", "main")
    if stream not in STREAMS:
        raise ConfigError(s.path("stream"), "must be main or sub")
    camera = Camera(
        id=camera_id,
        name=s.string("name", camera_id),
        channel=s.integer("channel", None, 1, 8),
        stream=stream,
        thresholds=_thresholds(s.section("thresholds")),
        zones=_zones(s.required("zones"), s.path("zones")),
    )
    s.no_unknown_keys()
    return camera


def _zones(data: Any, path: str) -> tuple[Zone, ...]:
    if not isinstance(data, list):
        raise ConfigError(path, "must be a list")
    if not data:
        raise ConfigError(path, "needs at least one zone")
    zones: list[Zone] = []
    for index, item in enumerate(data):
        s = _Section(item, _item_path(path, item, index))
        zone = Zone(
            id=s.identifier("id"),
            name=s.string("name", None),
            polygon=_polygon(s.required("polygon"), s.path("polygon")),
        )
        s.no_unknown_keys()
        if any(z.id == zone.id for z in zones):
            raise ConfigError(f"{path}[{zone.id}].id", "duplicate zone id")
        zones.append(zone)
    return tuple(zones)


def _polygon(data: Any, path: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(data, list):
        raise ConfigError(path, "must be a list of [x, y] points")
    if len(data) < 3:
        raise ConfigError(path, f"needs at least 3 points, has {len(data)}")
    points = []
    for index, point in enumerate(data):
        if not isinstance(point, list) or len(point) != 2 or not all(_is_number(v) for v in point):
            raise ConfigError(f"{path}[{index}]", "must be an [x, y] pair of numbers")
        x, y = float(point[0]), float(point[1])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ConfigError(f"{path}[{index}]", "coordinates must be between 0 and 1")
        points.append((x, y))
    if _area(points) < 1e-6:
        raise ConfigError(path, "points must enclose an area")
    return tuple(points)


# --- helpers ----------------------------------------------------------------


def _item_path(path: str, item: Any, index: int) -> str:
    """Name a list item by its id when it has a usable one, by its index otherwise."""
    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
        return f"{path}[{item['id']}]"
    return f"{path}[{index}]"


def _area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


_MISSING = object()


class _Section:
    """A mapping being validated, which remembers its path and the keys read."""

    def __init__(self, data: Any, path: str):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(path or "<root>", "must be a mapping")
        self.data = data
        self.prefix = path
        self.read: set[str] = set()

    def path(self, key: str) -> str:
        return f"{self.prefix}.{key}" if self.prefix else key

    def has(self, key: str) -> bool:
        return key in self.data

    def raw(self, key: str, default: Any = _MISSING) -> Any:
        self.read.add(key)
        if key in self.data:
            return self.data[key]
        if default is _MISSING:
            raise ConfigError(self.path(key), "is required")
        return default

    def required(self, key: str) -> Any:
        return self.raw(key)

    def section(self, key: str) -> _Section:
        return _Section(self.raw(key, None), self.path(key))

    def string(self, key: str, default: str | None) -> str:
        value = self.raw(key) if default is None else self.raw(key, default)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(self.path(key), "must be a non-empty text")
        return value

    def identifier(self, key: str) -> str:
        value = self.string(key, None)
        if not _ID.match(value):
            raise ConfigError(
                self.path(key),
                "use lowercase letters, digits and -, starting with a letter or digit",
            )
        return value

    def number(
        self,
        key: str,
        default: float | None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        value = self.raw(key) if default is None else self.raw(key, default)
        if not _is_number(value):
            raise ConfigError(self.path(key), "must be a number")
        if minimum is not None and value < minimum:
            raise ConfigError(self.path(key), f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigError(self.path(key), f"must be at most {maximum}")
        return float(value)

    def integer(
        self,
        key: str,
        default: int | None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self.raw(key) if default is None else self.raw(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(self.path(key), "must be a whole number")
        if minimum is not None and value < minimum:
            raise ConfigError(self.path(key), f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigError(self.path(key), f"must be at most {maximum}")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.raw(key, default)
        if not isinstance(value, bool):
            raise ConfigError(self.path(key), "must be true or false")
        return value

    def clock(self, key: str) -> time:
        value = self.raw(key)
        match = _HHMM.match(value) if isinstance(value, str) else None
        if not match:
            raise ConfigError(self.path(key), 'must be a time like "23:00" (quoted)')
        return time(int(match[1]), int(match[2]))

    def no_unknown_keys(self) -> None:
        for key in self.data:
            if key not in self.read:
                raise ConfigError(self.path(str(key)), "unknown field")
