"""Read a camera's frames from the gateway through an FFmpeg subprocess.

FFmpeg decodes with VideoToolbox, drops frames to the sampling rate and scales
to 1280x720 (which also stretches 1080N's 960x1080 to its intended 16:9), and
writes raw BGR frames to a pipe. The reader cuts the pipe into exact frames.

When FFmpeg exits or no frame arrives for 10 s, the reader kills it, reports the
camera as reconnecting, and starts again after 5 s, doubling up to 5 minutes,
back to 5 s after the next frame. Only the gateway talks to the DVR, so these
retries never send a login to it.
"""

from __future__ import annotations

import logging
import os
import selectors
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1280, 720
STALL_SECONDS = 10.0
FIRST_RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 300.0


@dataclass(frozen=True)
class Frame:
    camera_id: str
    image: np.ndarray  # HEIGHT x WIDTH x 3, BGR
    monotonic: float  # seconds, for durations
    wall: datetime  # UTC, for event times


def ffmpeg_command(url: str, fps: float, width: int = WIDTH, height: int = HEIGHT) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-hwaccel",
        "videotoolbox",
        "-i",
        url,
        "-vf",
        f"fps={fps:g},scale={width}:{height}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


class Process(Protocol):
    """The part of ``subprocess.Popen`` the reader uses."""

    def output_fd(self) -> int: ...
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...


class FFmpegProcess:
    def __init__(self, command: list[str]):
        self._popen = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # Drain stderr so FFmpeg never blocks on it, and log what it says.
        threading.Thread(target=self._log_errors, daemon=True).start()

    def _log_errors(self):
        for line in self._popen.stderr:
            log.warning("ffmpeg: %s", line.decode(errors="replace").rstrip())

    def output_fd(self) -> int:
        return self._popen.stdout.fileno()

    def poll(self) -> int | None:
        return self._popen.poll()

    def kill(self) -> None:
        if self._popen.poll() is None:
            self._popen.kill()
        self._popen.wait()
        self._popen.stdout.close()


def next_delay(current: float | None) -> float:
    """5 s, then doubled after each failure, at most 5 minutes."""
    if current is None:
        return FIRST_RETRY_SECONDS
    return min(current * 2, MAX_RETRY_SECONDS)


class FrameReader:
    """Reads one camera until ``stop`` is set.

    ``on_frame(frame)`` gets every frame; ``on_state(state, detail)`` gets
    ``"online"`` at the first frame after a (re)start and ``"reconnecting"``
    when the stream is lost (the pipeline then discards the camera's tracks).
    """

    def __init__(
        self,
        camera_id: str,
        url: str,
        fps: float,
        on_frame: Callable[[Frame], None],
        on_state: Callable[[str, str | None], None],
        spawn: Callable[[list[str]], Process] = FFmpegProcess,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
        stall_seconds: float = STALL_SECONDS,
        width: int = WIDTH,
        height: int = HEIGHT,
    ):
        self.camera_id = camera_id
        self.command = ffmpeg_command(url, fps, width, height)
        self.on_frame = on_frame
        self.on_state = on_state
        self.spawn = spawn
        self.monotonic = monotonic
        self.wall = wall
        self.stall_seconds = stall_seconds
        self.width, self.height = width, height
        self.frame_size = width * height * 3
        self.delay: float | None = None  # current retry delay; None after a frame

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            got_frames, reason = self._read_once(stop)
            if stop.is_set():
                return
            if got_frames:
                self.delay = None
            self.delay = next_delay(self.delay)
            log.warning("%s: %s; retrying in %g s", self.camera_id, reason, self.delay)
            self.on_state("reconnecting", f"{reason}; retrying in {self.delay:g} s")
            stop.wait(self.delay)

    def _read_once(self, stop: threading.Event) -> tuple[bool, str]:
        """Run FFmpeg until it fails; returns (whether any frame arrived, why it stopped)."""
        process = self.spawn(self.command)
        got_frames = False
        buffer = bytearray()
        selector = selectors.DefaultSelector()
        fd = process.output_fd()
        selector.register(fd, selectors.EVENT_READ)
        last_frame = self.monotonic()
        try:
            while not stop.is_set():
                waited = self.monotonic() - last_frame
                if waited >= self.stall_seconds:
                    return got_frames, f"no frame for {self.stall_seconds:g} s"
                # Wake up at least every second to notice ``stop``.
                if not selector.select(timeout=min(1.0, self.stall_seconds - waited)):
                    continue
                chunk = os.read(fd, self.frame_size - len(buffer))
                if not chunk:
                    code = process.poll()
                    return got_frames, f"ffmpeg exited ({code if code is not None else 'closed'})"
                buffer += chunk
                if len(buffer) < self.frame_size:
                    continue
                image = np.frombuffer(bytes(buffer), dtype=np.uint8).reshape(
                    self.height, self.width, 3
                )
                buffer.clear()
                last_frame = self.monotonic()
                if not got_frames:
                    got_frames = True
                    self.delay = None
                    self.on_state("online", None)
                self.on_frame(Frame(self.camera_id, image, last_frame, self.wall()))
            return got_frames, "stopped"
        finally:
            selector.close()
            process.kill()
