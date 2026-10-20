import subprocess

from sentinel.control import awake
from sentinel.control.awake import KeepAwake


class FakeProcess:
    def __init__(self, command):
        self.command = command
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def kill(self):
        self.code = -9

    def wait(self, timeout=None):
        return self.code


class Spawner:
    def __init__(self):
        self.processes = []

    def __call__(self, command):
        process = FakeProcess(command)
        self.processes.append(process)
        return process


def test_hold_runs_caffeinate_tied_to_the_agent():
    spawn = Spawner()
    keep = KeepAwake(spawn, pid=4242)
    assert not keep.held
    keep.hold()
    keep.hold()
    assert len(spawn.processes) == 1
    assert spawn.processes[0].command == ["/usr/bin/caffeinate", "-s", "-w", "4242"]
    assert keep.held


def test_release_ends_the_assertion():
    spawn = Spawner()
    keep = KeepAwake(spawn, pid=1)
    keep.hold()
    keep.release()
    assert spawn.processes[0].terminated
    assert not keep.held
    keep.release()  # harmless when not held


def test_hold_starts_caffeinate_again_if_it_ended():
    spawn = Spawner()
    keep = KeepAwake(spawn, pid=1)
    keep.hold()
    spawn.processes[0].code = 1
    assert not keep.held
    keep.hold()
    assert len(spawn.processes) == 2 and keep.held


def test_hold_survives_a_missing_caffeinate():
    def missing(command):
        raise FileNotFoundError(command[0])

    keep = KeepAwake(missing, pid=1)
    keep.hold()
    assert not keep.held


def pmset(text):
    return lambda command: subprocess.CompletedProcess(command, 0, text, "")


def test_power_source_from_pmset():
    on_ac = (
        "Now drawing from 'AC Power'\n"
        " -InternalBattery-0 (id=1234)\t100%; charged; 0:00 remaining present: true\n"
    )
    on_battery = (
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1234)\t83%; discharging; 9:12 remaining present: true\n"
    )
    assert awake.power_source(pmset(on_ac)) == "ac"
    assert awake.power_source(pmset(on_battery)) == "battery"
    assert awake.power_source(pmset("Now drawing from 'AC Power'\n")) == "ac"  # no battery


def test_power_source_when_pmset_fails():
    def fails(command):
        raise subprocess.TimeoutExpired(command, 10)

    assert awake.power_source(fails) == "ac"
