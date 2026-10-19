"""launchd agents for every component, and ``make install``.

One agent per component, ``com.carloseduardo.homeview-sentinel.<component>``,
in ``~/Library/LaunchAgents``:

- ``container-system`` runs ``container system start`` once at login (its
  services are transient and do not survive a logout or reboot);
- ``broker``, ``ntfy`` and ``gateway`` run their container launchers;
- ``notifier`` and ``detector`` run natively.

The Python components start at login (``RunAtLoad``) and are restarted 10 s
after a failed exit (``KeepAlive`` with ``SuccessfulExit = false``). A component
that exits cleanly (stopped on purpose, invalid configuration, missing secrets,
a rejected DVR login) stays stopped until the user acts. Each reads its own
secrets from the Keychain at start; the agents hold no secrets.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sentinel import paths

PREFIX = "com.carloseduardo.homeview-sentinel"
SEARCH_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
THROTTLE_SECONDS = 10
EXIT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Component:
    name: str
    module: str | None  # python -m <module>; None for the container system
    description: str


# In start order: the broker and ntfy before their clients.
COMPONENTS = (
    Component("container-system", None, "Apple container services"),
    Component("broker", "sentinel.bus.broker", "MQTT broker (Mosquitto container)"),
    Component("ntfy", "sentinel.notifier.ntfy_server", "push server (ntfy container)"),
    Component("gateway", "sentinel.gateway", "stream gateway (go2rtc container)"),
    Component("notifier", "sentinel.notifier", "notifier"),
    Component("detector", "sentinel.detector", "detector"),
)


def label(component: Component) -> str:
    return f"{PREFIX}.{component.name}"


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(component: Component, directory: Path | None = None) -> Path:
    return (directory or agents_dir()) / f"{label(component)}.plist"


def agent(
    component: Component,
    repository: Path,
    python: Path,
    container_bin: str,
    logs: Path,
) -> dict:
    """The launchd property list of one component."""
    environment = {
        "PATH": SEARCH_PATH,
        "PYTHONUNBUFFERED": "1",
        "SENTINEL_CONTAINER_BIN": container_bin,
    }
    log_file = str(logs / f"{component.name}.launchd.log")
    plist: dict = {
        "Label": label(component),
        "WorkingDirectory": str(repository),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "StandardOutPath": log_file,
        "StandardErrorPath": log_file,
        "ProcessType": "Standard",
    }
    if component.module is None:
        plist["ProgramArguments"] = [container_bin, "system", "start", "--disable-kernel-install"]
        plist["KeepAlive"] = False
    else:
        plist["ProgramArguments"] = [str(python), "-m", component.module]
        plist["KeepAlive"] = {"SuccessfulExit": False}
        plist["ThrottleInterval"] = THROTTLE_SECONDS
        plist["ExitTimeOut"] = EXIT_TIMEOUT_SECONDS
    return plist


def render(repository: Path, python: Path, container_bin: str, logs: Path) -> dict[str, bytes]:
    """label -> plist bytes, for every component."""
    return {
        label(c): plistlib.dumps(agent(c, repository, python, container_bin, logs))
        for c in COMPONENTS
    }


# --- launchctl ----------------------------------------------------------------

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def domain() -> str:
    return f"gui/{os.getuid()}"


def loaded(component: Component, run: Runner = _run) -> bool:
    return run(["launchctl", "print", f"{domain()}/{label(component)}"]).returncode == 0


def start(component: Component, run: Runner = _run, directory: Path | None = None) -> None:
    """Enable the agent (also at future logins) and load it, which starts it."""
    target = f"{domain()}/{label(component)}"
    run(["launchctl", "enable", target])
    if not loaded(component, run):
        result = run(["launchctl", "bootstrap", domain(), str(plist_path(component, directory))])
        if result.returncode != 0 and not loaded(component, run):
            raise RuntimeError(f"could not load {label(component)}: {result.stderr.strip()}")


def unload(
    component: Component,
    run: Runner = _run,
    timeout: float = EXIT_TIMEOUT_SECONDS + 5,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Unload the agent (launchd sends SIGTERM) and wait until it is gone:
    ``bootout`` returns while the job is still exiting."""
    if not loaded(component, run):
        return True
    run(["launchctl", "bootout", f"{domain()}/{label(component)}"])
    deadline = time.monotonic() + timeout
    while loaded(component, run):
        if time.monotonic() >= deadline:
            return False
        sleep(0.2)
    return True


def stop(component: Component, run: Runner = _run, sleep: Callable[[float], None] = time.sleep):
    """Unload the agent and keep it off at future logins."""
    unload(component, run, sleep=sleep)
    run(["launchctl", "disable", f"{domain()}/{label(component)}"])


def install(
    repository: Path,
    run: Runner = _run,
    directory: Path | None = None,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write every agent and (re)load it, so the new definitions take effect."""
    python = repository / ".venv" / "bin" / "python"
    container_bin = shutil.which("container", path=SEARCH_PATH) or "/opt/homebrew/bin/container"
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    directory = directory or agents_dir()
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render(repository, python, container_bin, logs)
    for component in reversed(COMPONENTS):
        if not unload(component, run, sleep=sleep):
            raise RuntimeError(f"{label(component)} did not stop")
    for component in COMPONENTS:
        plist_path(component, directory).write_bytes(rendered[label(component)])
    for component in COMPONENTS:
        start(component, run, directory)
        say(f"  started {component.name:<17} {component.description}")
