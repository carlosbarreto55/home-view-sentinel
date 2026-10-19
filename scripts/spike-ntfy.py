"""Spike 2.5: send test pushes through the notifier's real code.

    uv run python scripts/spike-ntfy.py [plain|snapshot|accents ...]

Uses ntfy on 127.0.0.1:2586 with the notifier token from the Keychain.
``snapshot`` grabs a live frame of camera 2 from the gateway, draws a box and a
zone on it like the detector does, and sends the "Moto com piloto" push with it.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from sentinel import config as config_module
from sentinel import secrets
from sentinel.bus.events import DWELL_STARTED, CameraRef, DwellEvent, ObjectRef, ZoneRef
from sentinel.detector import snapshots
from sentinel.notifier import messages
from sentinel.notifier.ntfy import NtfyClient

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "clips" / "spike25-snapshots"
POLYGON = [(0.45, 0.35), (0.70, 0.35), (0.75, 0.95), (0.40, 0.95)]


def live_frame(stream: str = "ch2_main") -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            f"rtsp://127.0.0.1:8554/{stream}",
            "-frames:v",
            "1",
            "-vf",
            "scale=1280:720",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(720, 1280, 3)


def snapshot_push(settings) -> messages.Push:
    now = datetime.now(UTC).replace(microsecond=0)
    ref = snapshots.save(
        SNAPSHOTS, "rua", now, "rua-1", live_frame(), (560.0, 300.0, 760.0, 640.0), POLYGON
    )
    event = DwellEvent(
        type=DWELL_STARTED,
        event_id=str(uuid.uuid4()),
        occurred_at=now,
        camera=CameraRef("rua", "Rua", 2),
        zone=ZoneRef("calcada", "Calçada"),
        object=ObjectRef("motorcycle", True, "rua-1", 0.82),
        since=now - timedelta(seconds=45),
        seconds=45,
        snapshot=ref,
    )
    return messages.for_event(event, settings)


def main(argv: list[str]) -> int:
    settings = config_module.load(ROOT / "config" / "sentinel.example.yaml").notifications
    client = NtfyClient(
        "http://127.0.0.1:2586",
        settings.ntfy_topic,
        secrets.require(secrets.NTFY_NOTIFIER_TOKEN),
        SNAPSHOTS,
        retry_delays=(),
    )
    now = datetime.now(UTC)
    pushes = {
        "plain": lambda: messages.detector_online(now, settings),
        "snapshot": lambda: snapshot_push(settings),
        "accents": lambda: messages.detector_offline(now, settings),
    }
    for name in argv or list(pushes):
        push = pushes[name]()
        ok = client.send(push)
        print(
            f"{name}: {'sent' if ok else 'FAILED'} | {push.title} | {push.message} | {push.priority}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
