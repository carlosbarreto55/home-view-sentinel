import json
import stat
from datetime import UTC, datetime, timedelta

from sentinel.control import client
from sentinel.control.contract import Request
from sentinel.control.files import ControlFiles
from test_runtime_status import control_state

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)


def test_send_writes_a_private_request(tmp_path):
    request = client.new_request("timed", 2)
    client.send(tmp_path / "control", request)
    path = tmp_path / "control" / "request.json"
    assert json.loads(path.read_text()) == request.to_dict()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(request.id) == 36  # a UUID
    assert client.new_request("off").id != request.id


def test_the_agent_reads_what_the_client_sends(tmp_path):
    files = ControlFiles(tmp_path, lambda: NOW)
    request = client.new_request("always")
    client.send(tmp_path, request)
    assert files.take_request() == request


def write_state(directory, **changes):
    files = ControlFiles(directory, lambda: NOW)
    files.write_state(control_state(**changes))


def test_read_state(tmp_path):
    assert client.read_state(tmp_path) is None
    (tmp_path / "state.json").write_text("{")
    assert client.read_state(tmp_path) is None
    write_state(tmp_path)
    assert client.read_state(tmp_path).mode == "timed"


class Time:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_for_an_applied_request(tmp_path):
    time = Time()
    writes = {1.0: dict(applied_request="other"), 2.0: dict(applied_request="r-1")}

    def sleep(seconds):
        time.sleep(seconds)
        if time.now in writes:
            write_state(tmp_path, **writes[time.now])

    state = client.wait_for(tmp_path, "r-1", sleep=sleep, monotonic=time)
    assert state.applied_request == "r-1" and client.applied(state, "r-1")
    assert time.now == 2.0


def test_wait_for_a_refused_request(tmp_path):
    write_state(tmp_path, refused_request="r-2", detail="Pedido inválido")
    state = client.wait_for(tmp_path, "r-2", sleep=lambda s: None, monotonic=Time())
    assert not client.applied(state, "r-2")


def test_wait_gives_up_after_15_seconds(tmp_path):
    time = Time()
    write_state(tmp_path)
    assert client.wait_for(tmp_path, "r-3", sleep=time.sleep, monotonic=time) is None
    assert 15 <= time.now < 15.5


def test_stale():
    state = control_state()
    assert not client.stale(state, NOW + timedelta(seconds=15))
    assert client.stale(state, NOW + timedelta(seconds=16))


def test_request_is_the_contract_model():
    assert isinstance(client.new_request("off"), Request)
