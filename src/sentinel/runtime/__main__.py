"""``python -m sentinel.runtime install|up|down|status|uninstall``: the ``make`` targets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sentinel import config as config_module
from sentinel import paths, secrets
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
    cli = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    command = [cli, "serve", "--bg", "--https=443", f"http://127.0.0.1:{ntfy_server.PORT}"]
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
    print(f"Installing the launchd agents in {launchd.agents_dir()}:")
    launchd.install(REPOSITORY)
    print(tailscale_serve())
    settings = config_module.load(REPOSITORY / config_module.DEFAULT_PATH).notifications
    print(
        f"\niPhone: in the ntfy app, add the server {settings.ntfy_base_url} with user "
        f"{secrets.NTFY_PHONE_USER} (password from `make secrets`) and subscribe to "
        f"{settings.ntfy_topic!r}. `make status` shows each component."
    )
    return 0


def installed() -> bool:
    return all(launchd.plist_path(c).exists() for c in launchd.COMPONENTS)


def up() -> int:
    if not installed():
        print("The launchd agents are not installed: run `make install`.")
        return 1
    if paths.gateway_stop_marker().exists():
        print(
            "Note: the DVR rejected the last login, so the gateway stays stopped. "
            "Run `make secrets` with the right password first."
        )
    for component in launchd.COMPONENTS:
        launchd.start(component)
        print(f"  started {component.name:<17} {component.description}")
    return 0


def down(timeout: float = DOWN_TIMEOUT) -> int:
    """Stop every component, also at future logins, and check nothing is left."""
    for component in reversed(launchd.COMPONENTS):
        launchd.stop(component)
    deadline = time.monotonic() + timeout
    left: list[str] = []
    while time.monotonic() < deadline:
        left = leftovers()
        if not left:
            break
        time.sleep(0.5)
    if left:
        print(f"Still running after {timeout:g} s: {', '.join(left)}")
        return 1
    print("Stopped: no Sentinel process or container is running (none at login either).")
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
    code = down()
    for component in launchd.COMPONENTS:
        launchd.plist_path(component).unlink(missing_ok=True)
        launchd.enable(component)  # do not leave a disabled entry behind
    print(f"Removed the launchd agents from {launchd.agents_dir()}. Data and logs are kept.")
    return code


def show_status() -> int:
    try:
        cameras = [
            c.id for c in config_module.load(REPOSITORY / config_module.DEFAULT_PATH).cameras
        ]
    except config_module.ConfigError:
        cameras = []
    for line in status.collect(cameras):
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="install and start the launchd agents")
    commands.add_parser("up", help="start every component (and at login)")
    commands.add_parser("down", help="stop every component (and not at login)")
    commands.add_parser("status", help="state, uptime and CPU of each component")
    commands.add_parser("uninstall", help="stop and remove the launchd agents")
    args = parser.parse_args(argv)
    actions = {
        "install": install,
        "up": up,
        "down": down,
        "status": show_status,
        "uninstall": uninstall,
    }
    return actions[args.command]()


if __name__ == "__main__":
    sys.exit(main())
