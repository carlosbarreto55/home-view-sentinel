"""The control agent: ``python -m sentinel.control`` (run by launchd at login, always).

It applies the saved mode (off after the first install), then ticks every
second until launchd stops it. Stopping the agent leaves the components as they
are: the next agent adopts them, with the same mode.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import UTC, datetime

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.bus.client import BusClient
from sentinel.config import Config, Notifications
from sentinel.control import awake, follow, modes
from sentinel.control.agent import Agent, Supervisor
from sentinel.control.files import ControlFiles
from sentinel.control.follow import Follower
from sentinel.control.modes import ModeMachine
from sentinel.runtime import EXIT_DONE, launchd, logs

log = logging.getLogger("sentinel.control")

TICK_SECONDS = 1


def clock() -> datetime:
    return datetime.now(UTC)


def load_config() -> Config | None:
    try:
        return config_module.load(config_module.DEFAULT_PATH)
    except config_module.ConfigError as error:
        # The agent still runs, so the mode can be turned off; the components
        # report the bad configuration themselves.
        log.error("%s", error)
        return None


class Components:
    """Starting: the bus first (it retries until the broker is up), then the
    agents in order. Stopping: the bus first (before the broker goes), then the
    agents in reverse."""

    def __init__(self, follower: Follower, add_secret):
        self.follower = follower
        self.add_secret = add_secret
        self.client: BusClient | None = None

    def start(self) -> None:
        if self.client is None:
            password = secrets.require(secrets.mqtt_item(follow.USER))
            self.add_secret(password)
            self.follower.reset()
            self.client = follow.connect(self.follower, password)
            self.client.start()
        launchd.start_components()

    def stop(self) -> list[str]:
        self.disconnect()
        self.follower.reset()
        return launchd.stop_components()

    def disconnect(self) -> None:
        if self.client is not None:
            self.client.stop()
            self.client = None


def main() -> int:
    redacting = logs.setup("control")
    config = load_config()
    cameras = config.cameras if config else ()
    settings = config.notifications if config else Notifications()

    files = ControlFiles(paths.control_dir(), clock)
    files.prepare()
    follower = Follower(
        cameras, settings, paths.snapshots_dir(), files.alert_path, paths.gateway_stop_marker()
    )
    components = Components(follower, redacting.add)
    supervisor = Supervisor(components.start, components.stop, clock)
    keep_awake = awake.KeepAwake()
    machine = ModeMachine(modes.load(files.mode_path), clock)
    agent = Agent(files, machine, supervisor, follower, keep_awake, awake.power_source, clock)
    log.info("started with mode %s", machine.mode.to_dict())

    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    # A daemon: a start or stop in progress must not hold up the exit (launchd
    # kills the agent 15 s after SIGTERM).
    worker = threading.Thread(
        target=supervisor.loop, args=(stopping,), name="supervisor", daemon=True
    )
    worker.start()
    try:
        while not stopping.is_set():
            try:
                agent.tick()
            except Exception:
                log.exception("tick failed")
            stopping.wait(TICK_SECONDS)
    finally:
        stopping.set()
        worker.join(timeout=2)
        keep_awake.release()
        components.disconnect()
        log.info("stopped; the components are left as they are")
    return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
