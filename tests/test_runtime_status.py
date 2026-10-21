import subprocess
from datetime import UTC, datetime, timedelta

from sentinel.bus.client import Status
from sentinel.runtime import launchd, status

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)

PRINT_RUNNING = """gui/501/com.carloseduardo.homeview-sentinel.detector = {
\tactive count = 1
\tstate = running
\tprogram = /x/.venv/bin/python
\tpid = 15309
\tlast exit code = (never exited)
}
"""
PRINT_STOPPED = """gui/501/com.carloseduardo.homeview-sentinel.gateway = {
\tstate = not running
\tlast exit code = 0
}
"""


def fake_launchctl(text, code=0):
    return lambda command: subprocess.CompletedProcess(command, code, text, "")


def test_reads_launchd_state():
    detector = next(c for c in launchd.COMPONENTS if c.name == "detector")
    assert status.agent(detector, fake_launchctl(PRINT_RUNNING)) == status.Agent(
        True, 15309, "(never exited)"
    )
    assert status.agent(detector, fake_launchctl(PRINT_STOPPED)) == status.Agent(False, None, "0")
    assert status.agent(detector, fake_launchctl("", 113)) is None


def test_report_lists_every_component_and_camera():
    agents = {
        c.name: status.Agent(True, 100 + i, None)
        for i, c in enumerate((launchd.CONTROL, *launchd.COMPONENTS))
    }
    agents["gateway"] = status.Agent(False, None, "0")
    retained = {
        "gateway": Status(
            "offline", NOW - timedelta(minutes=5), "Usuário ou senha do DVR incorretos"
        ),
        "detector": Status("online", NOW - timedelta(hours=2, minutes=3)),
        "camera-garagem": Status("online", NOW - timedelta(hours=2)),
        "camera-rua": Status("reconnecting", NOW - timedelta(seconds=40), "no frame for 10 s"),
        "notifier": Status("online", NOW - timedelta(days=1, hours=1)),
    }
    usage = {"detector": status.Usage(12.5, 512), "gateway": status.Usage(1.3, 25)}
    lines = status.report(agents, retained, usage, ["garagem", "rua"], NOW)
    text = "\n".join(lines)
    names = [line.split()[0] for line in lines[1:]]
    assert names == [
        "control",
        "container-system",
        "broker",
        "ntfy",
        "gateway",
        "notifier",
        "detector",
        "camera",
        "camera",
        "total",
    ]
    detector = next(line for line in lines if line.startswith("detector"))
    assert "running 106" in detector and "online" in detector and "2h 3m" in detector
    assert "12.5%" in detector and "512 MB" in detector
    gateway = next(line for line in lines if line.startswith("gateway"))
    assert "stopped (exit 0)" in gateway and "Usuário ou senha do DVR incorretos" in gateway
    assert "reconnecting" in text and "no frame for 10 s" in text
    assert "1d 1h" in text
    assert lines[-1].split()[1] == "13.8%"


def test_container_cpu_comes_from_two_samples(monkeypatch):
    samples = iter(
        [
            {"sentinel-gateway": {"cpuUsageUsec": 1_000_000, "memoryUsageBytes": 0}},
            {"sentinel-gateway": {"cpuUsageUsec": 1_020_000, "memoryUsageBytes": 25 * 2**20}},
        ]
    )
    monkeypatch.setattr(status, "_container_sample", lambda: next(samples))
    monkeypatch.setattr(status.time, "sleep", lambda s: None)
    times = iter([0.0, 1.0])
    monkeypatch.setattr(status.time, "monotonic", lambda: next(times))
    usage = status.container_usage()["sentinel-gateway"]
    assert round(usage.cpu_percent, 1) == 2.0
    assert usage.memory_mb == 25


def test_duration():
    assert status.duration(42) == "42s"
    assert status.duration(125) == "2m 5s"
    assert status.duration(3 * 3600 + 120) == "3h 2m"
    assert status.duration(2 * 86400 + 3600) == "2d 1h"


def test_native_usage_includes_child_processes():
    table = [
        (1, 0, 0.0, 10.0),
        (500, 1, 9.0, 180.0),  # detector
        (501, 500, 4.5, 60.0),  # its ffmpeg readers
        (502, 500, 4.0, 58.0),
        (600, 1, 0.1, 30.0),  # notifier
    ]
    usage = status.process_usage([500, 600, 999], table)
    assert usage[500] == status.Usage(17.5, 298.0)
    assert usage[600] == status.Usage(0.1, 30.0)
    assert 999 not in usage


def test_tailscale_state_comes_from_the_cli():
    running = fake_launchctl('{"BackendState": "Running", "Self": {}}')
    assert status.tailscale_state(running) == "Running"
    assert status.tailscale_state(fake_launchctl('{"BackendState": "Stopped"}')) == "Stopped"

    def gui(command):
        return subprocess.CompletedProcess(command, 1, "", "GUI failed to start\n")

    assert status.tailscale_state(gui) == "unknown (GUI failed to start)"


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_ntfy_problem_checks_health_at_the_tailnet_address():
    asked = []

    def healthy(url, timeout):
        asked.append(url)
        return FakeResponse(b'{"healthy":true}')

    assert status.ntfy_problem("https://mac.tailnet.ts.net/", healthy) is None
    assert asked == ["https://mac.tailnet.ts.net/v1/health"]

    def unreachable(url, timeout):
        raise status.urllib.error.URLError("nodename nor servname provided")

    assert status.ntfy_problem("https://mac.tailnet.ts.net", unreachable) == (
        "nodename nor servname provided"
    )


def test_tailscale_lines_warn_only_when_something_is_wrong():
    url = "https://mac.tailnet.ts.net"
    ok = status.tailscale_lines("Running", url, None)
    assert ok[0].split() == ["tailscale", "connected"]
    assert "reachable" in ok[1] and url in ok[1]
    assert len(ok) == 2
    down = status.tailscale_lines("Stopped", url, "timed out")
    assert down[0].split() == ["tailscale", "Stopped"]
    assert "NOT reachable (timed out)" in down[1]
    assert "open Tailscale on the Mac" in down[2]


def control_state(**changes):
    from dataclasses import replace

    from sentinel.control.contract import ControlState

    base = ControlState(
        written_at=NOW,
        mode="timed",
        until=NOW + timedelta(hours=1, minutes=59),
        phase="watching",
        detail=None,
        power="ac",
        kept_awake=True,
        cameras=(),
        applied_request=None,
        refused_request=None,
        last_alert=None,
    )
    return replace(base, **changes)


def test_watch_lines_show_the_mode_and_time_left():
    lines = status.watch_lines(control_state(), NOW)
    until = status.local_time(NOW + timedelta(hours=1, minutes=59), NOW)
    assert lines[0].split(None, 2) == ["watch", "mode", f"watching until {until} (1h 59m left)"]
    assert lines[1].split() == ["phase", "watching"]
    assert "power adapter, kept awake" in lines[2]
    assert len(lines) == 3


def test_watch_lines_for_24_7_off_battery_and_problems():
    assert "watching 24/7" in status.watch_lines(control_state(mode="always", until=None), NOW)[0]
    off = status.watch_lines(control_state(mode="off", until=None, phase="stopped"), NOW)
    assert off[0].endswith("off") and len(off) == 2
    battery = status.watch_lines(control_state(power="battery", kept_awake=False), NOW)
    assert "battery: the Mac may sleep" in battery[2]
    problem = status.watch_lines(
        control_state(phase="problem", detail="Usuário ou senha do DVR incorretos"), NOW
    )
    assert problem[1].split(None, 1)[1] == "problem: Usuário ou senha do DVR incorretos"


def test_watch_lines_when_the_agent_is_not_running():
    stale = status.watch_lines(control_state(written_at=NOW - timedelta(seconds=40)), NOW)
    assert stale[-1] == "(the control agent is not running: its state is 40s old)"
    assert "make install" in status.watch_lines(None, NOW)[0]


def test_local_time_names_the_day_when_it_is_not_today():
    from datetime import datetime

    now = datetime.now().astimezone()
    assert status.local_time(now, now) == now.strftime("%H:%M")
    assert status.local_time(now + timedelta(days=1), now) == (now + timedelta(days=1)).strftime(
        "%d/%m %H:%M"
    )
