import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentinel.control import contract
from sentinel.control.contract import (
    CameraState,
    ControlState,
    InvalidRequest,
    InvalidState,
    LastAlert,
    Request,
)

CHANGES = Path(__file__).parent.parent / "openspec" / "changes"


def design_examples() -> list[dict]:
    # The design moves under changes/archive/ when the change is archived.
    design = next(CHANGES.glob("**/*watch-schedule/design.md"))
    blocks = re.findall(r"```json\n(.*?)\n```", design.read_text(encoding="utf-8"), re.S)
    return [json.loads(block) for block in blocks]


def watching() -> ControlState:
    return ControlState(
        written_at=datetime(2026, 10, 19, 22, 31, 5, tzinfo=UTC),
        mode=contract.TIMED,
        until=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
        phase=contract.WATCHING,
        detail=None,
        power=contract.AC,
        kept_awake=True,
        cameras=(
            CameraState("garagem", "Garagem", 1, "online"),
            CameraState("rua", "Rua", 2, "online"),
        ),
        applied_request="r-7",
        refused_request=None,
        last_alert=LastAlert(
            event_id="8b84df78-6a1e-4f1c-9a52-1d6c2f0b7e31",
            title="Moto parada em frente de casa",
            message="Garagem · Calçada · parada há 30 s · 20:15",
            occurred_at=datetime(2026, 10, 19, 0, 15, 24, tzinfo=UTC),
            camera_id="garagem",
            camera_name="Garagem",
            camera_channel=1,
            zone="Calçada",
            snapshot="/Users/me/Library/Application Support/HomeView Sentinel/snapshots/"
            "garagem/2026-10-19T00-15-24Z-garagem-1.jpg",
        ),
    )


def test_the_design_has_the_state_example():
    assert [example["schema"] for example in design_examples()] == [contract.STATE_SCHEMA_ID]


def test_the_design_example_validates_and_round_trips():
    example = design_examples()[0]
    contract.validate_state(example)
    assert ControlState.from_dict(example).to_dict() == example


def test_state_serializes_to_the_schema():
    data = json.loads(watching().to_json())
    assert data["schema"] == "sentinel.control-state/v1"
    assert data["until"] == "2026-10-20T02:00:00Z"
    assert data["durations_hours"] == [1, 2, 4, 8, 12]
    assert data["last_alert"]["camera"] == {"id": "garagem", "name": "Garagem", "channel": 1}
    assert ControlState.from_json(watching().to_json()) == watching()


def test_state_off_has_no_end_time_and_no_alert():
    from dataclasses import replace

    off = replace(
        watching(), mode=contract.OFF, until=None, phase=contract.STOPPED, last_alert=None
    )
    assert json.loads(off.to_json())["until"] is None
    with pytest.raises(InvalidState, match="until"):
        replace(off, until=datetime(2026, 10, 20, tzinfo=UTC)).to_json()
    with pytest.raises(InvalidState):
        replace(watching(), until=None).to_json()


def test_state_refuses_an_unknown_power_source():
    from dataclasses import replace

    with pytest.raises(InvalidState, match="power"):
        replace(watching(), power="ups").to_json()


@pytest.mark.parametrize(
    "request_",
    [
        Request("r-1", "off"),
        Request("r-2", "always"),
        *(Request("r-3", "timed", h) for h in (1, 2, 4, 8, 12)),
    ],
    ids=lambda r: f"{r.mode}-{r.hours}",
)
def test_requests_round_trip(request_):
    assert Request.from_json(request_.to_json()) == request_


def test_a_uuid_request_from_homeview():
    text = (
        '{"schema": "sentinel.control-request/v1", '
        '"id": "0d7c3f0e-5b1a-4c8e-9f0d-6a2b1c3d4e5f", "mode": "timed", "hours": 2}'
    )
    assert Request.from_json(text) == Request("0d7c3f0e-5b1a-4c8e-9f0d-6a2b1c3d4e5f", "timed", 2)


@pytest.mark.parametrize("hours", [0, 3, 6, 24, 2.5, "2"])
def test_other_durations_are_refused(hours):
    text = json.dumps(
        {"schema": contract.REQUEST_SCHEMA_ID, "id": "r-7", "mode": "timed", "hours": hours}
    )
    with pytest.raises(InvalidRequest) as refused:
        Request.from_json(text)
    assert refused.value.request_id == "r-7"
    assert "hours" in refused.value.reason


@pytest.mark.parametrize(
    "data, problem",
    [
        ({"schema": "sentinel.control-request/v1", "id": "r-8", "mode": "timed"}, "hours"),
        ({"schema": "sentinel.control-request/v1", "id": "r-8", "mode": "always", "hours": 2}, ""),
        ({"schema": "sentinel.control-request/v1", "id": "r-8", "mode": "sometimes"}, "mode"),
        ({"schema": "sentinel.control-request/v2", "id": "r-8", "mode": "off"}, "schema"),
        ({"schema": "sentinel.control-request/v1", "id": "r-8", "mode": "off", "x": 1}, ""),
    ],
)
def test_requests_outside_the_schema_are_refused_with_their_id(data, problem):
    with pytest.raises(InvalidRequest) as refused:
        Request.from_json(json.dumps(data))
    assert refused.value.request_id == "r-8"
    assert problem in refused.value.reason


def test_bad_json_is_refused():
    with pytest.raises(InvalidRequest) as refused:
        Request.from_json('{"schema": "sentinel.control-request/v1", "id": "r-9", "mode": ')
    assert refused.value.request_id == "r-9"
    assert refused.value.reason.startswith("not JSON")
    with pytest.raises(InvalidRequest) as refused:
        Request.from_json(b"\xff not json")
    assert refused.value.request_id == contract.UNKNOWN_REQUEST
    with pytest.raises(InvalidRequest) as refused:
        Request.from_json("[1, 2]")
    assert refused.value.request_id == contract.UNKNOWN_REQUEST
