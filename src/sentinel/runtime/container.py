"""Run one Linux container in the foreground with Apple's ``container`` tool.

``container run`` 1.5 does not forward SIGTERM to the container, and killing the
client leaves the container running (spike 2.1). So the launcher:

1. waits for the container system to be running (started at login by its own
   launchd agent);
2. removes a leftover container with the same name;
3. runs ``container run --rm --name <name> …`` as a child, never ``exec``;
4. on SIGTERM or SIGINT stops the container with ``container stop`` and waits;
5. reports whether the container was stopped on purpose or exited on its own,
   so the caller exits for launchd to restart it only in the second case.

Secrets are passed as ``-e NAME`` only: the value is inherited from the
launcher's environment and never appears on a command line.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CONTAINER = os.environ.get("SENTINEL_CONTAINER_BIN", "container")


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    command: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()  # values come from the launcher's environment
    publish: tuple[str, ...] = ()  # "127.0.0.1:8554:8554"
    mounts: tuple[tuple[Path, str], ...] = ()  # (folder on the Mac, path in the container)
    cpus: int = 1
    memory: str = "256M"
    extra: tuple[str, ...] = field(default=())

    def run_command(self) -> list[str]:
        command = [CONTAINER, "run", "--rm", "--name", self.name]
        command += ["--cpus", str(self.cpus), "--memory", self.memory]
        for name in self.env_names:
            command += ["--env", name]
        for port in self.publish:
            command += ["--publish", port]
        for host, inside in self.mounts:
            command += ["--volume", f"{host}:{inside}"]
        return [*command, *self.extra, self.image, *self.command]


@dataclass(frozen=True)
class RunResult:
    stopped: bool  # stopped by a signal to the launcher
    exit_code: int | None


def _quiet(command: list[str], timeout: float = 30) -> int:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def system_running() -> bool:
    return _quiet([CONTAINER, "system", "status"], timeout=10) == 0


def wait_for_system(
    timeout: float = 120,
    check: Callable[[], bool] = system_running,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for ``container system start`` (run at login by its agent)."""
    deadline = time.monotonic() + timeout
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(2)


def remove(name: str) -> None:
    """Stop and delete a container; harmless when there is none."""
    _quiet([CONTAINER, "stop", name])
    _quiet([CONTAINER, "delete", name])


def run(
    spec: ContainerSpec,
    env: Mapping[str, str] | None = None,
    on_line: Callable[[str], None] = print,
) -> RunResult:
    """Run the container until it exits or the launcher is told to stop."""
    remove(spec.name)
    process = subprocess.Popen(
        spec.run_command(),
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
    stopping = threading.Event()

    def on_signal(signum, frame):
        if not stopping.is_set():
            stopping.set()
            log.info("stopping container %s", spec.name)
            threading.Thread(target=_quiet, args=([CONTAINER, "stop", spec.name],)).start()

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert process.stdout is not None
        for line in process.stdout:
            on_line(line.rstrip("\n"))
        code = process.wait()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    if not stopping.is_set():
        log.warning("container %s exited on its own (exit code %s)", spec.name, code)
    return RunResult(stopped=stopping.is_set(), exit_code=code)
