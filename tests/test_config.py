from datetime import time
from pathlib import Path

import pytest
import yaml

from sentinel import config
from sentinel.config import ConfigError

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"


def example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def error_for(data) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        config.from_dict(data)
    return caught.value


def test_example_file_is_valid():
    cfg = config.load(EXAMPLE)
    assert cfg.dvr.host == "192.0.2.10"
    assert cfg.sampling.fps == 5
    assert cfg.model.min_confidence == 0.4
    assert cfg.dwell.thresholds == {"motorcycle": 30, "car": 120}
    assert cfg.notifications.quiet_hours == config.QuietHours(time(23, 0), time(6, 0))
    garagem = cfg.camera("garagem")
    assert garagem.channel == 1
    assert garagem.subtype == 0
    assert garagem.zones[0].id == "calcada"
    assert garagem.zones[0].name == "Calçada"
    assert len(garagem.zones[0].polygon) == 4


def test_defaults_fill_missing_sections():
    cfg = config.from_dict(
        {
            "cameras": [
                {
                    "id": "rua",
                    "channel": 3,
                    "zones": [
                        {"id": "frente", "name": "Frente", "polygon": [[0, 0], [1, 0], [1, 1]]}
                    ],
                }
            ]
        }
    )
    assert cfg.dwell.end_grace_seconds == 10
    assert cfg.notifications.quiet_hours is None
    assert cfg.notifications.send_ended is False
    assert cfg.camera("rua").name == "rua"
    assert cfg.camera("rua").stream == "main"


def test_camera_threshold_override():
    data = example()
    data["cameras"][0]["thresholds"] = {"car": 90}
    cfg = config.from_dict(data)
    garagem = cfg.camera("garagem")
    assert cfg.threshold(garagem, "car") == 90
    assert cfg.threshold(garagem, "motorcycle") == 30


def test_zone_with_two_points_names_the_field():
    data = example()
    data["cameras"][0]["zones"][0]["polygon"] = [[0.1, 0.1], [0.9, 0.9]]
    error = error_for(data)
    assert error.field == "cameras[garagem].zones[calcada].polygon"
    assert "at least 3 points" in str(error)


@pytest.mark.parametrize(
    ("change", "field", "problem"),
    [
        (lambda d: d.pop("cameras"), "cameras", "is required"),
        (lambda d: d.update(cameras=[]), "cameras", "at least one camera"),
        (lambda d: d["cameras"][0].pop("channel"), "cameras[garagem].channel", "is required"),
        (lambda d: d["cameras"][0].update(channel=9), "cameras[garagem].channel", "at most 8"),
        (lambda d: d["cameras"][0].update(channel="1"), "cameras[garagem].channel", "whole number"),
        (lambda d: d["cameras"][0].update(stream="hd"), "cameras[garagem].stream", "main or sub"),
        (lambda d: d["cameras"][0].update(id="Garagem!"), "cameras[Garagem!].id", "lowercase"),
        (lambda d: d["cameras"][0].update(zones=[]), "cameras[garagem].zones", "at least one zone"),
        (
            lambda d: d["cameras"][0]["zones"][0].update(polygon=[[0, 0], [1.2, 0], [1, 1]]),
            "cameras[garagem].zones[calcada].polygon[1]",
            "between 0 and 1",
        ),
        (
            lambda d: d["cameras"][0]["zones"][0].update(polygon=[[0, 0], [0.5, 0.5], [1, 1]]),
            "cameras[garagem].zones[calcada].polygon",
            "enclose an area",
        ),
        (
            lambda d: d["cameras"][0]["zones"][0].update(polygon=[[0, 0], [1], [1, 1]]),
            "cameras[garagem].zones[calcada].polygon[1]",
            "[x, y] pair",
        ),
        (
            lambda d: d["cameras"][0]["zones"][0].pop("name"),
            "cameras[garagem].zones[calcada].name",
            "is required",
        ),
        (
            lambda d: d["cameras"][0].update(thresholds={"bicycle": 10}),
            "cameras[garagem].thresholds.bicycle",
            "unknown field",
        ),
        (lambda d: d["dwell"]["thresholds"].update(car=0), "dwell.thresholds.car", "at least 1"),
        (lambda d: d["sampling"].update(fps="fast"), "sampling.fps", "must be a number"),
        (lambda d: d["model"].update(min_confidence=1.5), "model.min_confidence", "at most 1"),
        (
            lambda d: d["notifications"].update(send_ended="yes"),
            "notifications.send_ended",
            "true or false",
        ),
        (
            lambda d: d["notifications"].update(timezone="Mars/Base"),
            "notifications.timezone",
            "unknown time zone",
        ),
        (
            lambda d: d["notifications"].update(ntfy_topic="a b"),
            "notifications.ntfy_topic",
            "letters",
        ),
        (
            lambda d: d["notifications"].update(ntfy_base_url="http://mac.local:2586"),
            "notifications.ntfy_base_url",
            "https://",
        ),
        (
            lambda d: d["notifications"]["quiet_hours"].update(start=1380),
            "notifications.quiet_hours.start",
            "quoted",
        ),
        (
            lambda d: d["notifications"]["quiet_hours"].update(end="23:00"),
            "notifications.quiet_hours.end",
            "differ from start",
        ),
        (lambda d: d["dvr"].update(port=70000), "dvr.port", "at most 65535"),
        (lambda d: d.update(extra=1), "extra", "unknown field"),
        (lambda d: d.update(sampling=[5]), "sampling", "must be a mapping"),
    ],
)
def test_each_kind_of_error_names_the_field(change, field, problem):
    data = example()
    change(data)
    error = error_for(data)
    assert error.field == field
    assert problem in error.problem


def test_duplicate_camera_id():
    data = example()
    data["cameras"].append({**data["cameras"][0], "channel": 2})
    assert error_for(data).field == "cameras[garagem].id"


def test_same_channel_twice():
    data = example()
    data["cameras"].append({**data["cameras"][0], "id": "outra"})
    error = error_for(data)
    assert error.field == "cameras[outra].channel"
    assert "already camera garagem" in error.problem


def test_duplicate_zone_id():
    data = example()
    zones = data["cameras"][0]["zones"]
    zones.append(dict(zones[0]))
    assert error_for(data).field == "cameras[garagem].zones[calcada].id"


def test_item_without_id_is_named_by_index():
    data = example()
    data["cameras"][0].pop("id")
    assert error_for(data).field == "cameras[0].id"


def test_invalid_yaml():
    with pytest.raises(ConfigError, match="not valid YAML"):
        config.parse("cameras: [", source="sentinel.yaml")


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        config.load(tmp_path / "sentinel.yaml")


@pytest.mark.parametrize(
    ("start", "end", "inside", "outside"),
    [
        (
            time(23),
            time(6),
            [time(23), time(3, 10), time(5, 59)],
            [time(6), time(12), time(22, 59)],
        ),
        (time(13), time(15), [time(13), time(14, 30)], [time(15), time(12, 59)]),
    ],
)
def test_quiet_hours_contains(start, end, inside, outside):
    quiet = config.QuietHours(start, end)
    assert all(quiet.contains(t) for t in inside)
    assert not any(quiet.contains(t) for t in outside)


def test_ntfy_base_url_is_optional_and_loses_its_trailing_slash():
    data = example()
    assert config.from_dict(data).notifications.ntfy_base_url is None
    data["notifications"]["ntfy_base_url"] = "https://mac.tail1234.ts.net/"
    assert config.from_dict(data).notifications.ntfy_base_url == "https://mac.tail1234.ts.net"
