"""The detector service: ``python -m sentinel.detector`` (run by launchd).

Reads config/sentinel.yaml, the broker password from the environment
(``SENTINEL_MQTT_PASSWORD``, put there by the launcher from the Keychain) or
the Keychain itself, and watches every configured camera through the gateway.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from functools import partial
from pathlib import Path

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.bus import client as bus
from sentinel.detector.frames import FrameReader
from sentinel.detector.inference import CoreMLBackend, model_path
from sentinel.detector.pipeline import Detector
from sentinel.gateway.go2rtc import local_url
from sentinel.runtime import EXIT_DONE

log = logging.getLogger("sentinel.detector")


def make_reader(camera, on_frame, on_state, fps: float) -> FrameReader:
    return FrameReader(camera.id, local_url(camera.id), fps, on_frame=on_frame, on_state=on_state)


def broker_password() -> str:
    return os.environ.get("SENTINEL_MQTT_PASSWORD") or secrets.require(
        secrets.mqtt_item("detector")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.detector")
    parser.add_argument("--config", type=Path, default=config_module.DEFAULT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        config = config_module.load(args.config)
        backend = CoreMLBackend(model_path(config.model.name), config.model.min_confidence)
        password = broker_password()
    except (config_module.ConfigError, FileNotFoundError, secrets.KeychainError) as error:
        # Stays stopped (launchd restarts only failed exits) until the user fixes it.
        log.error("%s", error)
        return EXIT_DONE

    publisher = bus.BusClient("detector", "detector", password)
    detector = Detector(
        config,
        backend,
        publisher,
        paths.snapshots_dir(),
        reader_factory=partial(make_reader, fps=config.sampling.fps),
    )
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    publisher.start()
    log.info("watching %s", ", ".join(c.id for c in config.cameras))
    try:
        detector.run(stop)
    finally:
        publisher.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
