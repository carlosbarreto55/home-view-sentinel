import plistlib
import subprocess
from pathlib import Path

from sentinel.runtime import launchd

REPO = Path("/Users/me/homeview-sentinel")
PYTHON = REPO / ".venv" / "bin" / "python"
LOGS = Path("/Users/me/Library/Logs/HomeView Sentinel")


def rendered():
    return {
        label: plistlib.loads(data)
        for label, data in launchd.render(REPO, PYTHON, "/opt/homebrew/bin/container", LOGS).items()
    }


def test_one_agent_per_component():
    assert sorted(rendered()) == sorted(
        f"com.carloseduardo.homeview-sentinel.{name}"
        for name in ("container-system", "broker", "ntfy", "gateway", "notifier", "detector")
    )


def test_components_restart_only_after_a_failed_exit():
    agents = rendered()
    for name, module in [
        ("broker", "sentinel.bus.broker"),
        ("ntfy", "sentinel.notifier.ntfy_server"),
        ("gateway", "sentinel.gateway"),
        ("notifier", "sentinel.notifier"),
        ("detector", "sentinel.detector"),
    ]:
        plist = agents[f"com.carloseduardo.homeview-sentinel.{name}"]
        assert plist["ProgramArguments"] == [str(PYTHON), "-m", module]
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] == {"SuccessfulExit": False}
        assert plist["ThrottleInterval"] == 10
        assert plist["WorkingDirectory"] == str(REPO)
        assert plist["StandardErrorPath"] == str(LOGS / f"{name}.launchd.log")


def test_the_container_system_is_started_once_at_login():
    plist = rendered()["com.carloseduardo.homeview-sentinel.container-system"]
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/container",
        "system",
        "start",
        "--disable-kernel-install",
    ]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is False


def test_agents_hold_no_secrets():
    for plist in rendered().values():
        assert set(plist["EnvironmentVariables"]) == {
            "PATH",
            "PYTHONUNBUFFERED",
            "SENTINEL_CONTAINER_BIN",
            "SENTINEL_LOG_FILE",
        }
        assert "/opt/homebrew/bin" in plist["EnvironmentVariables"]["PATH"]


class FakeLaunchctl:
    """Like launchctl: ``bootout`` returns while the job is still listed for a moment."""

    def __init__(self, loaded=(), lingering_prints=2):
        self.loaded = set(loaded)
        self.calls = []
        self.lingering = {}
        self.lingering_prints = lingering_prints

    def __call__(self, command):
        self.calls.append(command[1:])
        verb, target = command[1], command[-1]
        name = target.rsplit("/", 1)[-1].removesuffix(".plist")
        code = 0
        if verb == "print":
            if self.lingering.get(name):
                self.lingering[name] -= 1
                code = 0
            else:
                code = 0 if name in self.loaded else 113
        elif verb == "bootstrap":
            self.loaded.add(name)
        elif verb == "bootout":
            self.loaded.discard(name)
            self.lingering[name] = self.lingering_prints
        return subprocess.CompletedProcess(command, code, "", "")


NAMES = ["container-system", "broker", "ntfy", "gateway", "notifier", "detector"]


def names(launchctl, verb):
    return [
        c[-1].rsplit("/", 1)[-1].removesuffix(".plist").rsplit(".", 1)[-1]
        for c in launchctl.calls
        if c[0] == verb
    ]


def test_component_plists_live_in_application_support(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path))
    detector = next(c for c in launchd.COMPONENTS if c.name == "detector")
    assert launchd.plist_path(detector) == (
        tmp_path / "launchd" / "com.carloseduardo.homeview-sentinel.detector.plist"
    )
    assert launchd.components_dir() != launchd.agents_dir()


def test_the_control_agent_starts_at_login_and_always_restarts():
    plist = launchd.control_agent(REPO, PYTHON, "/opt/homebrew/bin/container", LOGS)
    assert plist["Label"] == "com.carloseduardo.homeview-sentinel.control"
    assert plist["ProgramArguments"] == [str(PYTHON), "-m", "sentinel.control"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 10
    assert plist["WorkingDirectory"] == str(REPO)
    assert plist["StandardErrorPath"] == str(LOGS / "control.launchd.log")


def test_install_puts_only_the_control_agent_in_launch_agents(tmp_path):
    components, agents = tmp_path / "launchd", tmp_path / "LaunchAgents"
    agents.mkdir()
    for name in ("broker", "detector"):
        (agents / f"com.carloseduardo.homeview-sentinel.{name}.plist").write_bytes(b"old")
    unrelated = agents / "com.example.other.plist"
    unrelated.write_bytes(b"keep")
    launchctl = FakeLaunchctl(
        loaded={
            "com.carloseduardo.homeview-sentinel.control",
            "com.carloseduardo.homeview-sentinel.detector",
        }
    )
    removed = launchd.install(
        REPO, run=launchctl, directory=components, agents_directory=agents, sleep=lambda s: None
    )
    assert removed == ["broker", "detector"]
    assert sorted(p.name for p in components.glob("*.plist")) == sorted(
        f"{label}.plist" for label in rendered()
    )
    assert sorted(p.name for p in agents.iterdir()) == [
        "com.carloseduardo.homeview-sentinel.control.plist",
        unrelated.name,
    ]
    control = plistlib.loads(
        (agents / "com.carloseduardo.homeview-sentinel.control.plist").read_bytes()
    )
    assert control["KeepAlive"] is True
    # The control agent goes first (or it would start the components again),
    # then the running detector, so the new definitions take effect.
    assert names(launchctl, "bootout") == ["control", "detector"]
    # Only the control agent is loaded; it starts the components if the mode says so.
    assert names(launchctl, "bootstrap") == ["control"]
    assert launchctl.loaded == {"com.carloseduardo.homeview-sentinel.control"}
    # A component disabled by an older `make down` is enabled again.
    assert [n for n in names(launchctl, "enable") if n != "control"] == NAMES


def test_uninstall_removes_every_agent(tmp_path):
    components, agents = tmp_path / "launchd", tmp_path / "LaunchAgents"
    launchctl = FakeLaunchctl()
    launchd.install(
        REPO, run=launchctl, directory=components, agents_directory=agents, sleep=lambda s: None
    )
    launchctl.loaded |= {f"com.carloseduardo.homeview-sentinel.{n}" for n in NAMES}
    launchctl.calls.clear()
    left = launchd.uninstall(
        run=launchctl, directory=components, agents_directory=agents, sleep=lambda s: None
    )
    assert left == []
    assert names(launchctl, "bootout") == ["control", *reversed(NAMES)]
    assert not launchctl.loaded
    assert list(components.iterdir()) == [] and list(agents.iterdir()) == []


def test_start_components_loads_them_in_order_from_application_support(tmp_path):
    launchctl = FakeLaunchctl(loaded={"com.carloseduardo.homeview-sentinel.broker"})
    launchd.start_components(run=launchctl, directory=tmp_path)
    bootstrapped = [c[-1] for c in launchctl.calls if c[0] == "bootstrap"]
    assert all(path.startswith(str(tmp_path)) for path in bootstrapped)
    assert names(launchctl, "bootstrap") == [n for n in NAMES if n != "broker"]
    assert len(launchctl.loaded) == 6


def test_stop_components_unloads_in_reverse_waiting_for_each():
    launchctl = FakeLaunchctl(loaded={f"com.carloseduardo.homeview-sentinel.{n}" for n in NAMES})
    assert launchd.stop_components(run=launchctl, sleep=lambda s: None) == []
    assert names(launchctl, "bootout") == list(reversed(NAMES))
    assert not launchctl.loaded
    # Each bootout is followed by prints until the job is gone, before the next bootout.
    verbs = [c[0] for c in launchctl.calls]
    first, second = [i for i, v in enumerate(verbs) if v == "bootout"][:2]
    assert verbs[first + 1 : second].count("print") >= launchctl.lingering_prints + 1
    assert "disable" not in verbs


def test_stop_components_reports_one_that_does_not_go(monkeypatch):
    launchctl = FakeLaunchctl(
        loaded={"com.carloseduardo.homeview-sentinel.gateway"}, lingering_prints=10**9
    )
    times = iter(range(0, 10**6, 5))
    monkeypatch.setattr(launchd.time, "monotonic", lambda: next(times))
    assert launchd.stop_components(run=launchctl, sleep=lambda s: None) == ["gateway"]
