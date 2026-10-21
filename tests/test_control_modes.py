import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from sentinel.control import modes
from sentinel.control.modes import Mode, ModeMachine, Refused


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def at(self, hour: int, minute: int = 0, day: int = 19) -> None:
        self.now = datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def clock_at(hour: int, minute: int = 0) -> Clock:
    return Clock(datetime(2026, 10, 19, hour, minute, tzinfo=UTC))


def until(hour: int, day: int = 19) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def test_starts_off():
    machine = ModeMachine(clock=clock_at(18))
    assert machine.mode == Mode("off")
    assert not machine.mode.watching


def test_watching_for_4_hours_ends_by_itself():
    clock = clock_at(18)
    machine = ModeMachine(clock=clock)
    assert machine.request("timed", 4) == Mode("timed", until(22))
    assert machine.mode.watching
    clock.at(21, 59)
    assert not machine.tick()
    assert machine.mode.watching
    clock.at(22)
    assert machine.tick()
    assert machine.mode == Mode("off")


@pytest.mark.parametrize("hours", [1, 2, 4, 8, 12])
def test_every_offered_duration(hours):
    clock = clock_at(18)
    machine = ModeMachine(clock=clock)
    assert machine.request("timed", hours).until == clock.now + timedelta(hours=hours)


@pytest.mark.parametrize("hours", [None, 0, 3, 24, -1, True, 2.5])
def test_a_duration_that_is_not_offered_changes_nothing(hours):
    machine = ModeMachine(clock=clock_at(18))
    machine.request("timed", 2)
    with pytest.raises(Refused, match="não oferecida"):
        machine.request("timed", hours)
    assert machine.mode == Mode("timed", until(20))


def test_an_unknown_mode_is_refused():
    machine = ModeMachine(clock=clock_at(18))
    with pytest.raises(Refused):
        machine.request("sometimes")
    assert machine.mode == Mode("off")


def test_a_new_timed_watch_replaces_the_end_time():
    clock = clock_at(18)
    machine = ModeMachine(clock=clock)
    machine.request("timed", 4)
    clock.at(19)
    assert machine.request("timed", 1).until == until(20)
    clock.at(20)
    assert machine.tick() and machine.mode == Mode("off")


def test_switching_to_24_7_during_a_timed_watch():
    clock = clock_at(18)
    machine = ModeMachine(clock=clock)
    machine.request("timed", 4)
    assert machine.request("always") == Mode("always")
    clock.at(22)
    assert not machine.tick()
    clock.at(9, day=25)
    assert not machine.tick()
    assert machine.mode == Mode("always")


def test_off_from_any_mode():
    machine = ModeMachine(clock=clock_at(18))
    for name, hours in [("always", None), ("timed", 2)]:
        machine.request(name, hours)
        assert machine.request("off") == Mode("off")


def test_end_time_has_seconds_precision():
    clock = Clock(datetime(2026, 10, 19, 18, 0, 5, 750_000, tzinfo=UTC))
    machine = ModeMachine(clock=clock)
    assert machine.request("timed", 1).until == datetime(2026, 10, 19, 19, 0, 5, tzinfo=UTC)


def test_the_timer_runs_out_while_the_mac_sleeps():
    clock = clock_at(20)
    machine = ModeMachine(clock=clock)
    machine.request("timed", 2)
    clock.at(21)  # lid closed
    assert not machine.tick()
    clock.at(23)  # lid opened: the next tick sees the clock jump
    assert machine.tick()
    assert machine.mode == Mode("off")


def test_first_mode_after_installation_is_off(tmp_path):
    assert modes.load(tmp_path / "control" / "mode.json") == Mode("off")


@pytest.mark.parametrize("text", ["not json", "[]", '{"mode": "timed"}', '{"mode": "x"}'])
def test_an_unreadable_mode_file_means_off(tmp_path, text):
    path = tmp_path / "mode.json"
    path.write_text(text)
    assert modes.load(path) == Mode("off")


def test_mode_file_is_private_and_round_trips(tmp_path):
    path = tmp_path / "control" / "mode.json"
    modes.save(path, Mode("timed", until(2, day=20)))
    assert json.loads(path.read_text()) == {"mode": "timed", "until": "2026-10-20T02:00:00Z"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert modes.load(path) == Mode("timed", until(2, day=20))
    modes.save(path, Mode("always"))
    assert json.loads(path.read_text()) == {"mode": "always"}
    assert [p.name for p in path.parent.iterdir()] == ["mode.json"]


def test_reboot_during_a_timed_watch_resumes_until_the_same_time(tmp_path):
    path = tmp_path / "mode.json"
    clock = clock_at(18)
    machine = ModeMachine(clock=clock)
    modes.save(path, machine.request("timed", 4))
    clock.at(20, 30)  # the Mac restarted, the user logs in
    resumed = ModeMachine(modes.load(path), clock)
    assert resumed.mode == Mode("timed", until(22))
    assert resumed.mode.watching


def test_restart_after_the_end_time_is_off(tmp_path):
    path = tmp_path / "mode.json"
    clock = clock_at(18)
    modes.save(path, ModeMachine(clock=clock).request("timed", 4))
    clock.at(23)
    assert ModeMachine(modes.load(path), clock).mode == Mode("off")


def test_24_7_survives_a_restart(tmp_path):
    path = tmp_path / "mode.json"
    clock = clock_at(18)
    modes.save(path, ModeMachine(clock=clock).request("always"))
    clock.at(9, day=22)
    assert ModeMachine(modes.load(path), clock).mode == Mode("always")
