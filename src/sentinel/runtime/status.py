"""``make status``: the watch mode, and each component's launchd state, MQTT
status, uptime and CPU.

- watch mode: from the control agent's ``state.json`` (off, 24/7, or until when
  and the time left), its phase and the power source;
- launchd: running (with pid) or not, and the last exit code;
- MQTT: the retained status each component publishes (gateway, detector and
  each camera, notifier), with its detail;
- uptime: since the MQTT status changed, or the process's elapsed time;
- CPU and memory: the process for native components, the container for the
  broker, ntfy and the gateway (sampled over one second);
- Tailscale: whether the Mac is connected and ntfy answers at its tailnet
  address. Without it the iPhone still gets the wake-up from ntfy.sh but cannot
  fetch the alert, so the notification opens nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel import secrets
from sentinel.bus import client as bus
from sentinel.bus.client import Status
from sentinel.control import client as control
from sentinel.control.contract import ALWAYS, BATTERY, OFF, PROBLEM, ControlState
from sentinel.runtime import container, launchd

TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
CONTAINERS = {"broker": "sentinel-broker", "ntfy": "sentinel-ntfy", "gateway": "sentinel-gateway"}


@dataclass(frozen=True)
class Agent:
    running: bool
    pid: int | None
    last_exit: str | None


@dataclass(frozen=True)
class Usage:
    cpu_percent: float
    memory_mb: float


def agent(component: launchd.Component, run: launchd.Runner = launchd._run) -> Agent | None:
    """launchd's view of the agent, or None when it is not loaded."""
    result = run(["launchctl", "print", f"{launchd.domain()}/{launchd.label(component)}"])
    if result.returncode != 0:
        return None
    text = result.stdout
    pid = re.search(r"^\s*pid = (\d+)", text, re.MULTILINE)
    state = re.search(r"^\s*state = (\w[\w ]*)", text, re.MULTILINE)
    last = re.search(r"^\s*last exit code = (.+)$", text, re.MULTILINE)
    return Agent(
        running=bool(state and state.group(1).strip() == "running"),
        pid=int(pid.group(1)) if pid else None,
        last_exit=last.group(1).strip() if last else None,
    )


def statuses(timeout: float = 2.0) -> dict[str, Status] | None:
    """The retained statuses, or None when the broker cannot be reached."""
    password = secrets.get(secrets.mqtt_item("admin"))
    if not password:
        return None
    found: dict[str, Status] = {}
    lock = threading.Lock()

    def on_status(topic: str, payload: bytes) -> None:
        try:
            status = Status.from_json(payload)
        except (ValueError, KeyError):
            return
        with lock:
            found[topic.rsplit("/", 1)[-1]] = status

    client = bus.BusClient(f"status-{time.monotonic_ns()}", "admin", password)
    client.subscribe(bus.STATUS_ALL, on_status)
    client.start()
    try:
        if not client.wait_connected(timeout):
            return None
        time.sleep(0.5)  # retained messages arrive right after subscribing
    finally:
        # Disconnect without publishing a status of its own.
        client._client.disconnect()
        client._client.loop_stop()
    with lock:
        return {k: v for k, v in found.items() if not k.startswith(("status-", "test-"))}


def process_table() -> list[tuple[int, int, float, float]]:
    """(pid, parent pid, CPU %, memory MB) of every process."""
    result = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,%cpu=,rss="], capture_output=True, text=True, check=False
    )
    table = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 4:
            table.append((int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3]) / 1024))
    return table


def process_usage(pids: list[int], table=None) -> dict[int, Usage]:
    """Usage of each process together with all its descendants (the detector's
    FFmpeg readers, for example)."""
    table = process_table() if table is None else table
    children: dict[int, list[int]] = {}
    own: dict[int, Usage] = {}
    for pid, parent, cpu, memory in table:
        children.setdefault(parent, []).append(pid)
        own[pid] = Usage(cpu, memory)
    usage = {}
    for root in pids:
        if root not in own:
            continue
        cpu = memory = 0.0
        stack = [root]
        while stack:
            pid = stack.pop()
            cpu += own[pid].cpu_percent
            memory += own[pid].memory_mb
            stack.extend(children.get(pid, []))
        usage[root] = Usage(cpu, memory)
    return usage


def _container_sample() -> dict[str, dict]:
    result = subprocess.run(
        [container.CONTAINER, "stats", "--no-stream", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        return {item["id"]: item for item in json.loads(result.stdout)}
    except (ValueError, KeyError, TypeError):
        return {}


def container_usage(interval: float = 1.0) -> dict[str, Usage]:
    """CPU over ``interval`` seconds (from cumulative CPU time) and memory now."""
    try:
        first = _container_sample()
        start = time.monotonic()
        time.sleep(interval)
        second = _container_sample()
        elapsed = time.monotonic() - start
    except (OSError, subprocess.TimeoutExpired):
        return {}
    usage = {}
    for name, now in second.items():
        before = first.get(name)
        if before is None:
            continue
        cpu = (now["cpuUsageUsec"] - before["cpuUsageUsec"]) / 1e6 / elapsed * 100
        usage[name] = Usage(max(cpu, 0.0), now["memoryUsageBytes"] / 1024 / 1024)
    return usage


def duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def process_text(agent_info: Agent | None) -> str:
    if agent_info is None:
        return "not loaded"
    if agent_info.running:
        return f"running {agent_info.pid}"
    return f"stopped (exit {agent_info.last_exit})"


def row(
    name: str,
    process: str,
    status: Status | None,
    usage: Usage | None,
    now: datetime,
) -> str:
    state = status.state if status else "-"
    since = duration((now - status.since).total_seconds()) if status and status.since else "-"
    cpu = f"{usage.cpu_percent:5.1f}%" if usage else "    -"
    memory = f"{usage.memory_mb:6.0f} MB" if usage else "      -"
    detail = status.detail if status and status.detail and state != bus.ONLINE else ""
    return f"{name:<18} {process:<22} {state:<13} {since:>8}  {cpu} {memory}  {detail}".rstrip()


def local_time(moment: datetime, now: datetime) -> str:
    """``20:00`` today, ``21/10 08:00`` another day, in the Mac's time zone."""
    local, today = moment.astimezone(), now.astimezone()
    return local.strftime("%H:%M" if local.date() == today.date() else "%d/%m %H:%M")


def mode_text(state: ControlState, now: datetime) -> str:
    if state.mode == OFF:
        return "off"
    if state.mode == ALWAYS:
        return "watching 24/7"
    left = duration((state.until - now).total_seconds())
    return f"watching until {local_time(state.until, now)} ({left} left)"


def watch_lines(state: ControlState | None, now: datetime) -> list[str]:
    """The watch mode and what the components are doing, for ``make status``
    and after ``make up``, ``make watch`` and ``make down``."""
    if state is None:
        return [f"{'watch mode':<18} unknown (no control state: run `make install`)"]
    phase = state.phase if state.phase != PROBLEM else f"problem: {state.detail}"
    lines = [f"{'watch mode':<18} {mode_text(state, now)}", f"{'  phase':<18} {phase}"]
    if state.mode != OFF:
        if state.power == BATTERY:
            lines.append(f"{'  power':<18} battery: the Mac may sleep and stop watching")
        else:
            awake = "kept awake" if state.kept_awake else "NOT kept awake"
            lines.append(f"{'  power':<18} power adapter, {awake}")
    if control.stale(state, now):
        age = duration((now - state.written_at).total_seconds())
        lines.append(f"(the control agent is not running: its state is {age} old)")
    return lines


HEADER = f"{'component':<18} {'launchd':<22} {'status':<13} {'for':>8}  {'cpu':>6} {'memory':>9}"


def report(
    agents: dict[str, Agent | None],
    retained: dict[str, Status] | None,
    usage: dict[str, Usage],
    camera_ids: list[str],
    now: datetime,
) -> list[str]:
    lines = [HEADER]
    retained = retained or {}
    for component in (launchd.CONTROL, *launchd.COMPONENTS):
        name = component.name
        process = process_text(agents.get(name))
        lines.append(row(name, process, retained.get(name), usage.get(name), now))
        if name == "detector":
            for camera_id in camera_ids:
                status = retained.get(bus.camera_component(camera_id))
                lines.append(row(f"  camera {camera_id}", "", status, None, now))
    total = sum(u.cpu_percent for u in usage.values())
    memory = sum(u.memory_mb for u in usage.values())
    lines.append(f"{'total':<18} {'':<22} {'':<13} {'':>8}  {total:5.1f}% {memory:6.0f} MB")
    return lines


def tailscale_cli() -> str:
    return shutil.which("tailscale") or TAILSCALE_APP_CLI


def _run_tailscale(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)


def tailscale_state(run: launchd.Runner = _run_tailscale) -> str:
    """Tailscale's backend state on this Mac: Running, Stopped, NeedsLogin…"""
    try:
        result = run([tailscale_cli(), "status", "--json"])
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unknown ({error})"
    try:
        return json.loads(result.stdout)["BackendState"]
    except (ValueError, KeyError, TypeError):
        lines = (result.stderr or result.stdout).strip().splitlines()
        return f"unknown ({lines[0] if lines else f'exit code {result.returncode}'})"


def ntfy_problem(
    base_url: str, open_url=urllib.request.urlopen, timeout: float = 5.0
) -> str | None:
    """None when ntfy answers its health check at the tailnet address, else why not."""
    try:
        with open_url(f"{base_url.rstrip('/')}/v1/health", timeout=timeout) as response:
            if json.loads(response.read()).get("healthy") is True:
                return None
            return "answers but is not healthy"
    except urllib.error.HTTPError as error:
        return f"{error.code} {error.reason}"
    except (urllib.error.URLError, OSError) as error:
        return str(getattr(error, "reason", error))
    except ValueError:
        return "answers with something that is not ntfy"


def tailscale_lines(state: str, base_url: str | None, problem: str | None) -> list[str]:
    connected = state == "Running"
    lines = [f"{'tailscale':<18} {'connected' if connected else state}"]
    if base_url:
        reach = "reachable" if problem is None else f"NOT reachable ({problem})"
        lines.append(f"{'  ntfy on tailnet':<18} {reach}  {base_url}")
    if not connected or problem is not None:
        lines.append(
            "(the iPhone gets the wake-up but cannot fetch the alerts: "
            "open Tailscale on the Mac and connect)"
        )
    return lines


def collect(
    camera_ids: list[str],
    ntfy_base_url: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
):
    agents = {c.name: agent(c) for c in (launchd.CONTROL, *launchd.COMPONENTS)}
    retained = statuses()
    native = {
        name: info.pid
        for name, info in agents.items()
        if info and info.pid and name not in CONTAINERS
    }
    by_pid = process_usage(list(native.values()))
    usage = {name: by_pid[pid] for name, pid in native.items() if pid in by_pid}
    by_container = container_usage()
    for name, container_name in CONTAINERS.items():
        if container_name in by_container:
            usage[name] = by_container[container_name]
    lines = report(agents, retained, usage, camera_ids, now())
    if retained is None:
        lines.append("(the broker is not reachable: no MQTT statuses)")
    problem = ntfy_problem(ntfy_base_url) if ntfy_base_url else None
    lines += tailscale_lines(tailscale_state(), ntfy_base_url, problem)
    return lines
