"""Replay a recorded clip through the detection pipeline, for calibration.

``make replay CLIP=clips/garagem-noite.mp4 CAMERA=garagem`` decodes the clip
with FFmpeg at the configured sampling rate, runs the same pipeline as the
service as fast as the model allows, with the clip's own timestamps as the
clock, and prints the events instead of publishing them. Snapshots go to a
folder next to the clip, so they can be looked at.

``make record CAMERA=garagem MINUTES=10`` saves a clip of a camera from the
gateway (see ``record_command``).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from sentinel import config as config_module
from sentinel.bus.events import DWELL_STARTED, DwellEvent
from sentinel.config import Camera, Config
from sentinel.detector.frames import HEIGHT, WIDTH, Frame
from sentinel.detector.inference import CoreMLBackend, InferenceBackend, model_path
from sentinel.detector.pipeline import CameraPipeline
from sentinel.gateway.go2rtc import local_url

CLIPS_DIR = Path("clips")


def decode_command(clip: Path, fps: float, width: int = WIDTH, height: int = HEIGHT) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "videotoolbox",
        "-i",
        str(clip),
        "-vf",
        f"fps={fps:g},scale={width}:{height}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def record_command(camera_id: str, minutes: float, output: Path, started: datetime) -> list[str]:
    """Copy the camera's stream from the gateway (no re-encoding) into an MP4."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        local_url(camera_id),
        "-t",
        f"{minutes * 60:g}",
        "-c",
        "copy",
        "-tag:v",
        "hvc1",  # lets QuickTime play the H.265 main streams
        "-metadata",
        f"creation_time={started.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
        str(output),
    ]


def clip_start(clip: Path) -> datetime:
    """When the clip was recorded: its creation_time tag, or the file's time."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags=creation_time",
                "-of",
                "json",
                str(clip),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        text = json.loads(result.stdout).get("format", {}).get("tags", {}).get("creation_time")
        if text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return datetime.fromtimestamp(clip.stat().st_mtime, UTC)


def clip_frames(
    clip: Path,
    camera_id: str,
    fps: float,
    start: datetime,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Iterator[Frame]:
    """The clip's frames at the sampling rate; frame ``i`` is at ``i / fps`` seconds."""
    size = width * height * 3
    process = subprocess.Popen(decode_command(clip, fps, width, height), stdout=subprocess.PIPE)
    try:
        index = 0
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            t = index / fps
            image = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            yield Frame(camera_id, image, t, start + timedelta(seconds=t))
            index += 1
    finally:
        process.kill()
        process.wait()


def clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def describe(event: DwellEvent, clip_start_at: datetime) -> str:
    """One line per event: time in the clip, type, vehicle, zone, seconds stopped."""
    offset = (event.occurred_at - clip_start_at).total_seconds()
    vehicle = event.object.vehicle_class
    if vehicle == "motorcycle":
        vehicle += " with rider" if event.object.with_rider else " without rider"
    line = (
        f"{clock(offset)}  {event.type:<13}  {event.object.track_id:<12}  {vehicle:<24}  "
        f"{event.zone.name}  {event.seconds} s"
    )
    if event.type == DWELL_STARTED:
        line += f"  {event.snapshot.file}"
    return line


def replay(
    config: Config,
    camera: Camera,
    frames: Iterable[Frame],
    backend: InferenceBackend,
    snapshots_root: Path,
    clip_start_at: datetime,
    out: Callable[[str], None] = print,
) -> list[DwellEvent]:
    """Run the pipeline over the frames and print each event; nothing is published."""
    pipeline = CameraPipeline(config, camera, backend, snapshots_root)
    events: list[DwellEvent] = []
    last_t = 0.0
    for frame in frames:
        last_t = frame.monotonic
        for event in pipeline.process(frame):
            events.append(event)
            out(describe(event, clip_start_at))
    # The clip ends: let the end grace pass so vehicles still there are reported.
    for event in pipeline.tick(last_t + config.dwell.end_grace_seconds + 1):
        events.append(event)
        out(describe(event, clip_start_at) + "  (clip ended)")
    out(f"{len(events)} events in {clock(last_t)} of video")
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.detector.replay")
    parser.add_argument("--config", type=Path, default=config_module.DEFAULT_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    play = commands.add_parser("replay", help="run a clip through the pipeline")
    play.add_argument("--clip", type=Path, required=True)
    play.add_argument("--camera", required=True)
    rec = commands.add_parser("record", help="save a clip of a camera from the gateway")
    rec.add_argument("--camera", required=True)
    rec.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    try:
        config = config_module.load(args.config)
        camera = config.camera(args.camera)
    except config_module.ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    except KeyError:
        print(f"camera {args.camera!r} is not in {args.config}", file=sys.stderr)
        return 1

    if args.command == "record":
        started = datetime.now(UTC)
        CLIPS_DIR.mkdir(exist_ok=True)
        output = CLIPS_DIR / f"{camera.id}-{started.astimezone().strftime('%Y%m%d-%H%M')}.mp4"
        print(f"recording {args.minutes:g} min of {camera.name} to {output}")
        return subprocess.run(record_command(camera.id, args.minutes, output, started)).returncode

    if not args.clip.is_file():
        print(f"{args.clip} not found", file=sys.stderr)
        return 1
    backend = CoreMLBackend(model_path(config.model.name), config.model.min_confidence)
    start = clip_start(args.clip)
    snapshots_root = args.clip.with_suffix("")  # clips/garagem-noite/
    replay(
        config,
        camera,
        clip_frames(args.clip, camera.id, config.sampling.fps, start),
        backend,
        snapshots_root,
        start,
    )
    print(f"snapshots in {snapshots_root}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
