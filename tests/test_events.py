import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentinel.bus import events
from sentinel.bus.events import DwellEvent, InvalidEvent

DESIGN = (
    Path(__file__).parent.parent / "openspec" / "changes" / "stopped-vehicle-alerts" / "design.md"
)


def design_examples() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)\n```", DESIGN.read_text(encoding="utf-8"), re.S)
    return [json.loads(block) for block in blocks]


def started() -> DwellEvent:
    return DwellEvent(
        type=events.DWELL_STARTED,
        event_id="5f0c1c0e-8a43-4d43-9a7e-2f1b6c3d9e10",
        occurred_at=datetime(2026, 10, 18, 15, 4, 37, tzinfo=UTC),
        camera=events.CameraRef("garagem", "Garagem", 1),
        zone=events.ZoneRef("calcada", "Calçada"),
        object=events.ObjectRef("motorcycle", True, "garagem-42", 0.8123),
        since=datetime(2026, 10, 18, 15, 4, 7, tzinfo=UTC),
        seconds=30,
        snapshot=events.SnapshotRef("garagem/2026-10-18T15-04-37Z-garagem-42.jpg", 1280, 720),
    )


def test_design_has_both_examples():
    assert [example["type"] for example in design_examples()] == ["dwell.started", "dwell.ended"]


@pytest.mark.parametrize("example", design_examples(), ids=lambda e: e["type"])
def test_every_design_example_validates(example):
    events.validate(example)
    assert DwellEvent.from_dict(example).to_dict() == example


def test_model_serializes_to_the_design_example():
    assert started().to_dict() == design_examples()[0]
    assert json.loads(started().to_json()) == design_examples()[0]


def test_ended_event_refers_to_the_start():
    end = started().ended(datetime(2026, 10, 18, 15, 11, 7, tzinfo=UTC), 420)
    data = json.loads(end.to_json())
    assert data["type"] == "dwell.ended"
    assert data["started_event_id"] == started().event_id
    assert data["event_id"] != started().event_id
    assert data["dwell"] == {"since": "2026-10-18T15:04:07Z", "seconds": 420}


def test_round_trip_through_json():
    assert DwellEvent.from_json(started().to_json()) == DwellEvent.from_dict(started().to_dict())


def test_times_are_written_in_utc():
    from zoneinfo import ZoneInfo

    local = datetime(2026, 10, 18, 11, 4, 37, tzinfo=ZoneInfo("Etc/GMT+4"))
    assert events.utc_text(local) == "2026-10-18T15:04:37Z"
    with pytest.raises(ValueError):
        events.utc_text(datetime(2026, 10, 18, 15, 4, 37))


def mutate(change):
    data = copy.deepcopy(design_examples()[0])
    change(data)
    return data


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(schema="sentinel.event/v2"),
        lambda d: d.update(type="dwell.paused"),
        lambda d: d.update(event_id="uuid"),
        lambda d: d.update(occurred_at="2026-10-18T11:04:37-04:00"),
        lambda d: d.pop("snapshot"),
        lambda d: d["snapshot"].update(data="/9j/4AAQSkZJRg=="),
        lambda d: d["snapshot"].update(file="/Users/x/garagem/a.jpg"),
        lambda d: d["object"].update({"class": "bicycle"}),
        lambda d: d["object"].update(confidence=1.5),
        lambda d: d["camera"].update(channel=9),
        lambda d: d["camera"].update(password="secret"),
        lambda d: d["dwell"].update(seconds=-1),
        lambda d: d.update(started_event_id="5f0c1c0e-8a43-4d43-9a7e-2f1b6c3d9e10"),
        lambda d: d["object"].update({"class": "car", "with_rider": True}),
        lambda d: d.update(type="dwell.ended"),  # without started_event_id
    ],
    ids=[
        "other version",
        "unknown type",
        "bad event id",
        "not utc",
        "no snapshot",
        "embedded image",
        "absolute snapshot path",
        "other class",
        "confidence over 1",
        "channel 9",
        "extra field",
        "negative seconds",
        "start with started_event_id",
        "car with rider",
        "end without started_event_id",
    ],
)
def test_invalid_events_are_rejected(change):
    with pytest.raises(InvalidEvent):
        events.validate(mutate(change))


def test_model_refuses_to_serialize_an_invalid_event():
    bad = DwellEvent(**{**started().__dict__, "seconds": -5})
    with pytest.raises(InvalidEvent, match="dwell/seconds"):
        bad.to_json()


def test_from_json_rejects_garbage():
    with pytest.raises(InvalidEvent):
        DwellEvent.from_json(b"not json")
    with pytest.raises(InvalidEvent):
        DwellEvent.from_json("[]")
