import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.bus import client as bus
from sentinel.config import Camera, Notifications
from sentinel.control import contract, modes
from sentinel.control.agent import Agent, Supervisor
from sentinel.control.awake import KeepAwake
from sentinel.control.contract import ControlState, Request
from sentinel.control.files import ControlFiles
from sentinel.control.follow import Follower
from sentinel.control.modes import Mode, ModeMachine
from test_control_awake import Spawner
from test_control_follow import all_online, status

START = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)
CAMERAS = (
    Camera("garagem", "Garagem", 1, "main", ()),
    Camera("rua", "Rua", 2, "main", ()),
)


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Components:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")
        return []


class World:
    def __init__(self, tmp_path, mode=modes.OFF_MODE, power="ac"):
        self.clock = Clock()
        self.folder = tmp_path / "control"
        self.files = ControlFiles(self.folder, self.clock)
        self.files.prepare()
        self.components = Components()
        self.supervisor = Supervisor(self.components.start, self.components.stop, self.clock)
        self.follower = Follower(
            CAMERAS, Notifications(), tmp_path / "snapshots", self.files.alert_path
        )
        self.follower.connected = lambda: True
        self.spawn = Spawner()
        self.power = power
        self.agent = Agent(
            self.files,
            ModeMachine(mode, self.clock),
            self.supervisor,
            self.follower,
            KeepAwake(self.spawn, pid=1),
            lambda: self.power,
            self.clock,
        )

    def request(self, text):
        temporary = self.folder / ".request.json.client"
        temporary.write_text(text)
        temporary.rename(self.folder / "request.json")

    def tick(self, seconds=1):
        self.clock.advance(seconds)
        self.agent.tick()
        self.supervisor.step()

    def state(self) -> ControlState:
        return ControlState.from_json(self.files.state_path.read_text())


def ask(world, request_id, mode, hours=None):
    world.request(Request(request_id, mode, hours).to_json())


def test_homeview_asks_for_2_hours(tmp_path):
    world = World(tmp_path)
    world.tick()
    ask(world, "r-7", "timed", 2)
    world.tick()  # the agent looks once a second: applied within 2 s
    state = world.state()
    assert state.applied_request == "r-7"
    assert state.refused_request is None
    assert state.mode == "timed"
    assert state.until == world.clock.now.replace(microsecond=0) + timedelta(hours=2)
    assert not (world.folder / "request.json").exists()
    assert world.components.calls == ["stop", "start"]
    assert modes.load(world.files.mode_path) == Mode("timed", state.until)


def test_a_malformed_request_changes_nothing(tmp_path):
    world = World(tmp_path)
    world.tick()
    world.request('{"schema": "sentinel.control-request/v1", "id": "r-8", "mode": "always"')
    world.tick()
    state = world.state()
    assert state.mode == "off"
    assert state.refused_request == "r-8"
    assert state.detail.startswith("Pedido inválido (not JSON")
    world.request("garbage")
    world.tick()
    assert world.state().refused_request == contract.UNKNOWN_REQUEST


def test_a_duration_that_is_not_offered_is_refused(tmp_path):
    world = World(tmp_path, mode=Mode("always"))
    world.tick()
    world.request(
        json.dumps({"schema": contract.REQUEST_SCHEMA_ID, "id": "r-3", "mode": "timed", "hours": 3})
    )
    world.tick()
    state = world.state()
    assert (state.mode, state.refused_request) == ("always", "r-3")
    assert "hours" in state.detail
    # The next applied request clears the reason.
    ask(world, "r-4", "off")
    world.tick()
    state = world.state()
    assert (state.mode, state.applied_request, state.refused_request) == ("off", "r-4", "r-3")
    assert state.detail is None


def test_the_last_request_written_wins(tmp_path):
    world = World(tmp_path)
    ask(world, "r-1", "always")
    ask(world, "r-2", "timed", 1)  # replaces r-1 before the agent looked
    world.tick()
    assert (world.state().applied_request, world.state().mode) == ("r-2", "timed")


def test_state_file_is_private_and_atomic(tmp_path):
    world = World(tmp_path)
    world.tick()
    assert stat.S_IMODE(world.files.state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(world.folder.stat().st_mode) == 0o700
    assert sorted(p.name for p in world.folder.iterdir()) == ["mode.json", "state.json"]


def test_prepare_tightens_an_existing_folder(tmp_path):
    folder = tmp_path / "control"
    folder.mkdir(mode=0o755)
    ControlFiles(folder, Clock()).prepare()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_state_is_rewritten_on_every_change_and_every_5_seconds(tmp_path):
    world = World(tmp_path)
    world.tick()
    world.tick()  # "stopping" at the first tick (the stop at start), then "stopped"
    first = world.state().written_at
    for _ in range(4):
        world.tick()
    assert world.state().written_at == first  # nothing changed for 4 s
    world.tick()
    assert world.state().written_at == first + timedelta(seconds=5)
    ask(world, "r-9", "always")
    world.tick()
    assert world.state().written_at == first + timedelta(seconds=6)


def test_a_stale_state_tells_readers_the_agent_stopped(tmp_path):
    world = World(tmp_path)
    world.tick()
    written = world.state().written_at
    reader_now = written + timedelta(seconds=30)  # the agent has not run since
    assert reader_now - world.state().written_at > timedelta(seconds=15)


def test_reading_the_state_while_watching_on_power_adapter(tmp_path):
    world = World(tmp_path)
    ask(world, "r-1", "timed", 4)
    world.tick()
    all_online(world.follower)
    world.tick()
    data = json.loads(world.files.state_path.read_text())
    contract.validate_state(data)
    assert data["mode"] == "timed" and data["until"] == "2026-10-19T22:00:01Z"
    assert data["phase"] == "watching"
    assert [c["state"] for c in data["cameras"]] == ["online", "online"]
    assert data["power"] == "ac" and data["kept_awake"] is True
    assert "password" not in json.dumps(data).lower()


def test_on_battery_the_state_says_so(tmp_path):
    world = World(tmp_path, mode=Mode("always"), power="battery")
    world.tick()
    state = world.state()
    assert state.power == "battery"
    assert state.kept_awake is False  # the assertion has no effect on battery
    assert world.agent.keep_awake.held


def test_the_power_source_is_read_every_30_seconds(tmp_path):
    world = World(tmp_path, mode=Mode("always"))
    world.tick()
    world.power = "battery"
    world.tick(20)
    assert world.state().power == "ac"
    world.tick(10)
    assert world.state().power == "battery"


def test_a_problem_is_reported_with_its_detail(tmp_path):
    world = World(tmp_path, mode=Mode("always"))
    world.tick()
    status(world.follower, "gateway", bus.OFFLINE, "Usuário ou senha do DVR incorretos")
    world.tick()
    state = world.state()
    assert (state.phase, state.detail) == ("problem", "Usuário ou senha do DVR incorretos")


def test_a_timed_watch_ends_and_releases_everything(tmp_path):
    world = World(tmp_path)
    ask(world, "r-1", "timed", 1)
    world.tick()
    assert world.agent.keep_awake.held
    world.tick(3600)
    assert world.state().phase == "stopping"
    world.tick()
    state = world.state()
    assert (state.mode, state.until, state.phase) == ("off", None, "stopped")
    assert world.components.calls[-1] == "stop"
    assert not world.agent.keep_awake.held
    assert modes.load(world.files.mode_path) == Mode("off")


def test_switching_to_24_7_does_not_restart_anything(tmp_path):
    world = World(tmp_path)
    ask(world, "r-1", "timed", 4)
    world.tick()
    ask(world, "r-2", "always")
    world.tick()
    world.tick(4 * 3600)
    assert world.components.calls == ["start"]
    assert world.state().mode == "always"
    assert len(world.spawn.processes) == 1


def test_phase_while_starting_and_stopping(tmp_path):
    world = World(tmp_path)
    ask(world, "r-1", "always")
    world.clock.advance(1)
    world.agent.tick()  # the supervisor has not acted yet
    assert world.state().phase == "starting"
    world.supervisor.step()
    ask(world, "r-2", "off")
    world.clock.advance(1)
    world.agent.tick()
    assert world.state().phase == "stopping"
    world.supervisor.step()
    world.tick()
    assert world.state().phase == "stopped"


class FailingStart(Components):
    def __init__(self):
        super().__init__()
        self.failures = 1

    def start(self):
        self.calls.append("start")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("could not load com.carloseduardo.homeview-sentinel.broker")


def test_a_failed_start_is_reported_and_retried_after_30_seconds(tmp_path):
    clock = Clock()
    components = FailingStart()
    supervisor = Supervisor(components.start, components.stop, clock)
    supervisor.want(True)
    supervisor.step()
    run = supervisor.run()
    assert run.error.startswith("Não foi possível iniciar o Sentinel")
    clock.advance(10)
    supervisor.step()
    assert components.calls == ["start"]
    clock.advance(20)
    supervisor.step()
    assert components.calls == ["start", "start"]
    assert supervisor.run().error is None and not supervisor.run().busy


@pytest.mark.parametrize("left", [[], ["gateway"]])
def test_stopping_reports_components_that_did_not_stop(left):
    clock = Clock()
    supervisor = Supervisor(lambda: None, lambda: left, clock)
    supervisor.want(False)
    supervisor.step()
    assert supervisor.run().busy == bool(left)
