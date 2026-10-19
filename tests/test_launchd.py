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


def test_install_writes_the_agents_and_reloads_running_ones_in_order(tmp_path):
    launchctl = FakeLaunchctl(loaded={"com.carloseduardo.homeview-sentinel.detector"})
    launchd.install(
        REPO, run=launchctl, directory=tmp_path, say=lambda _: None, sleep=lambda s: None
    )
    written = sorted(p.name for p in tmp_path.glob("*.plist"))
    assert written == sorted(f"{label}.plist" for label in rendered())
    verbs = [(c[0], c[-1].rsplit("/", 1)[-1]) for c in launchctl.calls if c[0] != "print"]
    assert verbs[0] == ("bootout", "com.carloseduardo.homeview-sentinel.detector")
    bootstrapped = [t.removesuffix(".plist") for v, t in verbs if v == "bootstrap"]
    assert [b.rsplit(".", 1)[-1] for b in bootstrapped] == [
        "container-system",
        "broker",
        "ntfy",
        "gateway",
        "notifier",
        "detector",
    ]
    assert len(launchctl.loaded) == 6


def test_stop_unloads_and_keeps_the_agent_off_at_login():
    launchctl = FakeLaunchctl(loaded={"com.carloseduardo.homeview-sentinel.gateway"})
    gateway = next(c for c in launchd.COMPONENTS if c.name == "gateway")
    launchd.stop(gateway, run=launchctl, sleep=lambda s: None)
    assert [c[0] for c in launchctl.calls if c[0] != "print"] == ["bootout", "disable"]
    assert not launchctl.loaded
