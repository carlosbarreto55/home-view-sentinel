"""The gateway launcher: check the DVR login once, then run go2rtc.

``python -m sentinel.gateway`` (run by launchd):

1. If the stop marker is there (the DVR rejected the login before), stay
   stopped without trying again: only ``make secrets`` removes it.
2. Read the DVR user and password from the Keychain.
3. Send one authenticated DESCRIBE (``rtsp_probe``):
   - wrong credentials: log and publish "Usuário ou senha do DVR incorretos",
     write the stop marker, exit so launchd does not restart it;
   - DVR not found: report it and exit so launchd tries again later;
   - accepted: render go2rtc.yaml and run go2rtc in its container.
4. While go2rtc runs, stay connected to the broker as the ``gateway``
   component, and pass go2rtc's output to the log with credentials redacted.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.bus import client as bus
from sentinel.config import Config
from sentinel.gateway import go2rtc
from sentinel.rtsp_probe import ProbeResult, probe
from sentinel.runtime import EXIT_DONE, EXIT_RESTART, container, logs
from sentinel.runtime.redact import redact

log = logging.getLogger("sentinel.gateway")

WRONG_LOGIN = "Usuário ou senha do DVR incorretos"
IMAGE = "alexxit/go2rtc:1.9.10"
CONTAINER_NAME = "sentinel-gateway"
CONFIG_INSIDE = "/config"


class StatusBus(Protocol):
    def start(self) -> None: ...
    def wait_connected(self, timeout: float | None = None) -> bool: ...
    def set_status(self, state: str, detail: str | None = None, component: str | None = None): ...
    def stop(self, detail: str = "stopped") -> None: ...


class NoBus:
    """Used when the broker credentials are missing: the gateway still serves."""

    def start(self) -> None:
        pass

    def wait_connected(self, timeout: float | None = None) -> bool:
        return False

    def set_status(self, state: str, detail: str | None = None, component: str | None = None):
        return None

    def stop(self, detail: str = "stopped") -> None:
        pass


def container_spec(config_dir: Path) -> container.ContainerSpec:
    return container.ContainerSpec(
        name=CONTAINER_NAME,
        image=IMAGE,
        command=("go2rtc", "-config", f"{CONFIG_INSIDE}/go2rtc.yaml"),
        env_names=("DVR_USER", "DVR_PASSWORD"),
        publish=(f"127.0.0.1:{go2rtc.RTSP_PORT}:{go2rtc.RTSP_PORT}",),
        mounts=((config_dir, CONFIG_INSIDE),),
        cpus=1,
        memory="256M",
    )


@dataclass
class Gateway:
    config: Config
    bus: StatusBus
    config_dir: Path
    stop_marker: Path
    read_secret: Callable[[str], str | None] = secrets.get
    check_login: Callable[..., ProbeResult] = probe
    run_container: Callable[..., container.RunResult] = container.run
    wait_for_system: Callable[[], bool] = container.wait_for_system
    add_secrets: Callable[..., None] = lambda *values: None

    def run(self) -> int:
        dvr = f"{self.config.dvr.host}:{self.config.dvr.port}"
        if self.stop_marker.exists():
            log.error(
                "%s: the gateway stays stopped. Run `make secrets` with the right "
                "password, then `make up`.",
                WRONG_LOGIN,
            )
            return self._finish(EXIT_DONE, WRONG_LOGIN)

        user = self.read_secret(secrets.DVR_USER)
        password = self.read_secret(secrets.DVR_PASSWORD)
        if not user or not password:
            log.error("the DVR user or password is not in the Keychain; run `make secrets`")
            return self._finish(EXIT_DONE, "DVR login missing (make secrets)")
        environment = go2rtc.environment(user, password)
        self.add_secrets(password, environment["DVR_PASSWORD"])

        camera = self.config.cameras[0]
        result = self.check_login(
            self.config.dvr.host,
            self.config.dvr.port,
            user,
            password,
            path=self.config.dvr.path(camera.channel, camera.subtype),
        )
        if result is ProbeResult.WRONG_CREDENTIALS:
            self.stop_marker.parent.mkdir(parents=True, exist_ok=True)
            self.stop_marker.write_text(WRONG_LOGIN + "\n", encoding="utf-8")
            log.error("%s (DVR at %s). The gateway will not try again.", WRONG_LOGIN, dvr)
            return self._finish(EXIT_DONE, WRONG_LOGIN)
        if result is ProbeResult.NOT_FOUND:
            log.error("DVR not found at %s; launchd will try again", dvr)
            return self._finish(EXIT_RESTART, f"DVR not found at {dvr}")
        log.info("the DVR at %s accepted the login", dvr)

        if not self.wait_for_system():
            log.error("the container system is not running; launchd will try again")
            return self._finish(EXIT_RESTART, "container system not running")
        go2rtc.write(self.config, self.config_dir / "go2rtc.yaml")
        self.bus.set_status(bus.ONLINE)
        log.info(
            "serving %s",
            ", ".join(f"{c.id} (channel {c.channel}, {c.stream})" for c in self.config.cameras),
        )
        outcome = self.run_container(
            container_spec(self.config_dir), environment, on_line=self._go2rtc_line
        )
        if outcome.stopped:
            return self._finish(EXIT_DONE, "stopped")
        return self._finish(EXIT_RESTART, f"go2rtc exited ({outcome.exit_code})")

    def _go2rtc_line(self, line: str) -> None:
        # The log filter redacts again, with the Keychain values; this keeps
        # go2rtc's URLs clean even if the line is used elsewhere.
        log.info("go2rtc: %s", redact(line))

    def _finish(self, code: int, detail: str) -> int:
        self.bus.wait_connected(timeout=5)
        self.bus.stop(detail)
        return code


def main(argv: list[str] | None = None) -> int:
    redacting = logs.setup("gateway")
    try:
        config = config_module.load(config_module.DEFAULT_PATH)
    except config_module.ConfigError as error:
        log.error("%s", error)
        return EXIT_DONE
    try:
        broker_password = secrets.get(secrets.mqtt_item("gateway"))
    except secrets.KeychainError as error:
        log.error("%s", error)
        return EXIT_RESTART
    if broker_password:
        status_bus: StatusBus = bus.BusClient("gateway", "gateway", broker_password)
    else:
        log.warning("no broker password for the gateway; its status will not be published")
        status_bus = NoBus()
    status_bus.start()
    gateway = Gateway(
        config,
        status_bus,
        config_dir=paths.gateway_dir(),
        stop_marker=paths.gateway_stop_marker(),
        add_secrets=redacting.add,
    )
    try:
        return gateway.run()
    except secrets.KeychainError as error:
        log.error("%s", error)
        status_bus.stop("Keychain error")
        return EXIT_RESTART


if __name__ == "__main__":
    sys.exit(main())
