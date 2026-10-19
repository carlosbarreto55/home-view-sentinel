"""``make status``: each component's launchd state, MQTT status, uptime and CPU.

- launchd: running (with pid) or not, and the last exit code;
- MQTT: the retained status each component publishes (gateway, detector and
  each camera, notifier), with its detail;
- uptime: since the MQTT status changed, or the process's elapsed time;
- CPU and memory: the process for native components, the container for the
  broker, ntfy and the gateway (sampled over one second).
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel import secrets
from sentinel.bus import client as bus
from sentinel.bus.client import Status
from sentinel.runtime import container, launchd

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
    for component in launchd.COMPONENTS:
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


def collect(camera_ids: list[str], now: Callable[[], datetime] = lambda: datetime.now(UTC)):
    agents = {c.name: agent(c) for c in launchd.COMPONENTS}
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
    return lines
