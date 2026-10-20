"""``python -m sentinel.runtime install|up|watch|down|status|uninstall``: the ``make`` targets.

``up``, ``watch`` and ``down`` are requests to the control agent, which owns the
mode; they wait for its answer and print the state.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.control import client as control
from sentinel.control.contract import ALWAYS, DURATIONS_HOURS, OFF, STOPPED, TIMED, ControlState
from sentinel.detector.inference import model_path
from sentinel.notifier import ntfy_server
from sentinel.runtime import container, launchd, status

REPOSITORY = Path(__file__).resolve().parents[3]
DOWN_TIMEOUT = 10.0


def problems(repository: Path) -> list[str]:
    """What is missing before the agents can run."""
    found = []
    if not (repository / ".venv" / "bin" / "python").exists():
        found.append("no .venv: run `make setup`")
    try:
        config = config_module.load(repository / config_module.DEFAULT_PATH)
    except config_module.ConfigError as error:
        found.append(f"config/sentinel.yaml: {error}")
        config = None
    if config is not None:
        if not model_path(config.model.name).exists():
            found.append(f"model {config.model.name} not exported: run `make setup`")
        if not config.notifications.ntfy_base_url:
            found.append(
                "notifications.ntfy_base_url is not set in config/sentinel.yaml "
                "(the Mac's https://<mac>.<tailnet>.ts.net address)"
            )
    items = [secrets.DVR_USER, secrets.DVR_PASSWORD, secrets.NTFY_PHONE_PASSWORD]
    items += [secrets.NTFY_NOTIFIER_TOKEN] + [secrets.mqtt_item(u) for u in secrets.MQTT_USERS]
    missing = [item for item in items if not secrets.get(item)]
    if missing:
        found.append("not in the Keychain: " + ", ".join(missing) + " (run `make secrets`)")
    return found


def tailscale_serve() -> str:
    """Serve ntfy over HTTPS on the tailnet. The Tailscale Mac app's CLI only
    works from a terminal, so this runs here, in the foreground, not in an agent."""
    command = [
        status.tailscale_cli(),
        "serve",
        "--bg",
        "--https=443",
        f"http://127.0.0.1:{ntfy_server.PORT}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"could not run Tailscale ({error}); run by hand: {' '.join(command)}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        hint = detail[0] if detail else f"exit code {result.returncode}"
        return (
            f"Tailscale Serve failed ({hint}). Open Tailscale on the Mac, connect, "
            f"then run `make install` again or: {' '.join(command)}"
        )
    return "Tailscale Serve forwards https://<this Mac>.ts.net to ntfy"


def install() -> int:
    found = problems(REPOSITORY)
    if found:
        print("Cannot install yet:")
        for problem in found:
            print(f"  - {problem}")
        return 1
    removed = launchd.install(REPOSITORY)
    if removed:
        print(f"Removed the old login agents from {launchd.agents_dir()}: {', '.join(removed)}")
    print(f"Wrote the component agents to {launchd.components_dir()}.")
    print(f"Installed the control agent in {launchd.agents_dir()}; it keeps the current mode.")
    print(tailscale_serve())
    settings = config_module.load(REPOSITORY / config_module.DEFAULT_PATH).notifications
    print(
        f"\niPhone: in the ntfy app, add the server {settings.ntfy_base_url} with user "
        f"{secrets.NTFY_PHONE_USER} (password from `make secrets`) and subscribe to "
        f"{settings.ntfy_topic!r}. `make up` starts watching, `make status` shows each component."
    )
    return 0


def installed() -> bool:
    plists = [launchd.control_plist_path()] + [launchd.plist_path(c) for c in launchd.COMPONENTS]
    return all(path.exists() for path in plists)


def ask(mode: str, hours: int | None = None) -> ControlState | None:
    """Send a request to the control agent and wait for its answer; prints the
    outcome. Returns the state when the request was applied."""
    if not installed():
        print("The control agent is not installed: run `make install`.")
        return None
    directory = paths.control_dir()
    request = control.new_request(mode, hours)
    control.send(directory, request)
    state = control.wait_for(directory, request.id)
    if state is None:
        print(
            f"The control agent did not answer in {control.ANSWER_TIMEOUT:g} s. "
            "Is it running? `make status` shows it; `make install` starts it again."
        )
        return None
    if not control.applied(state, request.id):
        print(f"Refused: {state.detail}")
        return None
    for line in status.watch_lines(state, datetime.now(UTC)):
        print(line)
    return state


def up() -> int:
    """Watch 24/7."""
    if paths.gateway_stop_marker().exists():
        print(
            "Note: the DVR rejected the last login, so the gateway stays stopped. "
            "Run `make secrets` with the right password first."
        )
    return 0 if ask(ALWAYS) else 1


def watch(hours: int) -> int:
    """Watch for ``hours`` hours, then turn off by itself."""
    return 0 if ask(TIMED, hours) else 1


def down(timeout: float = DOWN_TIMEOUT) -> int:
    """Stop watching, and check that nothing but the control agent is left."""
    if ask(OFF) is None:
        return 1
    deadline = time.monotonic() + timeout
    left: list[str] = []
    while time.monotonic() < deadline:
        state = control.read_state(paths.control_dir())
        left = leftovers()
        if not left and state is not None and state.phase == STOPPED:
            break
        time.sleep(0.5)
    if left:
        print(f"Still running after {timeout:g} s: {', '.join(left)}")
        return 1
    print("Stopped: no Sentinel process or container is running except the control agent.")
    return 0


def leftovers() -> list[str]:
    """Sentinel processes or containers still running."""
    found = [
        component.name
        for component in launchd.COMPONENTS
        if component.module and _pids(f"python.* -m {component.module}$")
    ]
    result = subprocess.run(
        [container.CONTAINER, "list", "--quiet"], capture_output=True, text=True, check=False
    )
    found += [name for name in result.stdout.split() if name.startswith("sentinel-")]
    return found


def _pids(pattern: str) -> list[str]:
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False)
    return result.stdout.split()


def uninstall() -> int:
    left = launchd.uninstall()
    left += [name for name in leftovers() if name not in left]
    print("Removed the control agent and the component agents. Data and logs are kept.")
    if left:
        print(f"Still running: {', '.join(left)}")
        return 1
    return 0


def show_status() -> int:
    for line in status.watch_lines(control.read_state(paths.control_dir()), datetime.now(UTC)):
        print(line)
    print()
    try:
        config = config_module.load(REPOSITORY / config_module.DEFAULT_PATH)
        cameras = [c.id for c in config.cameras]
        ntfy_base_url = config.notifications.ntfy_base_url
    except config_module.ConfigError:
        cameras, ntfy_base_url = [], None
    for line in status.collect(cameras, ntfy_base_url):
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="install the control agent and the component agents")
    commands.add_parser("up", help="watch 24/7")
    timed = commands.add_parser("watch", help="watch for some hours, then stop by itself")
    timed.add_argument("--hours", type=int, choices=DURATIONS_HOURS, required=True)
    commands.add_parser("down", help="stop watching (only the control agent keeps running)")
    commands.add_parser("status", help="state, uptime and CPU of each component")
    commands.add_parser("uninstall", help="stop and remove every launchd agent")
    args = parser.parse_args(argv)
    actions = {
        "install": install,
        "up": up,
        "watch": lambda: watch(args.hours),
        "down": down,
        "status": show_status,
        "uninstall": uninstall,
    }
    return actions[args.command]()


if __name__ == "__main__":
    sys.exit(main())
