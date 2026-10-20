"""launchd agents for every component, and ``make install``.

One agent per component, ``com.carloseduardo.homeview-sentinel.<component>``:

- ``container-system`` runs ``container system start`` once when loaded;
- ``broker``, ``ntfy`` and ``gateway`` run their container launchers;
- ``notifier`` and ``detector`` run natively.

The component plists live in Sentinel's Application Support folder
(``launchd/``), not in ``~/Library/LaunchAgents``, so launchd never loads them
at login by itself: they are loaded on demand, in order, with ``launchctl
bootstrap`` when watching starts and unloaded in reverse with ``bootout`` when
it stops. While loaded, a component is restarted 10 s after a failed exit
(``KeepAlive`` with ``SuccessfulExit = false``); one that exits cleanly (stopped
on purpose, invalid configuration, missing secrets, a rejected DVR login) stays
stopped. Each reads its own secrets from the Keychain at start; the agents hold
no secrets.
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
    """Where launchd looks at login."""
    return Path.home() / "Library" / "LaunchAgents"


def components_dir() -> Path:
    """The component plists, loaded only on demand."""
    return paths.data_dir() / "launchd"


def plist_path(component: Component, directory: Path | None = None) -> Path:
    return (directory or components_dir()) / f"{label(component)}.plist"


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
        "SENTINEL_LOG_FILE": "1",  # log to <component>.log, rotated daily
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
    """Load the agent, which starts it. ``enable`` first clears a ``disable``
    left by an older ``make down``, which would make ``bootstrap`` fail."""
    run(["launchctl", "enable", f"{domain()}/{label(component)}"])
    if not loaded(component, run):
        result = run(["launchctl", "bootstrap", domain(), str(plist_path(component, directory))])
        if result.returncode != 0 and not loaded(component, run):
            raise RuntimeError(f"could not load {label(component)}: {result.stderr.strip()}")


def enable(component: Component, run: Runner = _run) -> None:
    run(["launchctl", "enable", f"{domain()}/{label(component)}"])


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


def start_components(
    run: Runner = _run,
    directory: Path | None = None,
    say: Callable[[str], None] = lambda _: None,
) -> None:
    """Load every component in start order (the broker and ntfy before their clients)."""
    for component in COMPONENTS:
        start(component, run, directory)
        say(f"  started {component.name:<17} {component.description}")


def stop_components(run: Runner = _run, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Unload every component in reverse order, each gone before the next;
    returns the ones that did not stop."""
    return [c.name for c in reversed(COMPONENTS) if not unload(c, run, sleep=sleep)]


def remove_legacy(run: Runner = _run, directory: Path | None = None) -> list[str]:
    """Delete the component agents that older versions installed in
    ``~/Library/LaunchAgents`` (where they started at every login)."""
    removed = []
    for component in COMPONENTS:
        path = plist_path(component, directory or agents_dir())
        if path.exists():
            path.unlink()
            removed.append(component.name)
        enable(component, run)  # do not leave a disabled entry behind
    return removed


def install(
    repository: Path,
    run: Runner = _run,
    directory: Path | None = None,
    legacy_directory: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Unload every component, remove the old login agents and write the
    component plists, so the new definitions take effect at the next start.
    Returns the old agents removed."""
    python = repository / ".venv" / "bin" / "python"
    container_bin = shutil.which("container", path=SEARCH_PATH) or "/opt/homebrew/bin/container"
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    directory = directory or components_dir()
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render(repository, python, container_bin, logs)
    left = stop_components(run, sleep)
    if left:
        raise RuntimeError(f"{', '.join(left)} did not stop")
    removed = remove_legacy(run, legacy_directory)
    for component in COMPONENTS:
        plist_path(component, directory).write_bytes(rendered[label(component)])
    return removed
