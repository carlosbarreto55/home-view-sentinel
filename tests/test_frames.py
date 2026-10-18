import os
import threading
import time

import numpy as np

from sentinel.detector import frames
from sentinel.detector.frames import FrameReader

W, H = 4, 2
SIZE = W * H * 3


class FakeProcess:
    """Writes a script to a pipe: bytes are written, ("sleep", s) waits, "exit"
    closes the pipe (FFmpeg exiting), "hang" keeps it open and silent."""

    def __init__(self, script):
        self.read_fd, self.write_fd = os.pipe()
        self.killed = threading.Event()
        self.code = None
        threading.Thread(target=self._play, args=(script,), daemon=True).start()

    def _play(self, script):
        for step in script:
            if self.killed.is_set():
                break
            if step == "exit":
                self.code = 1
                break
            if step == "hang":
                self.killed.wait()
                break
            if isinstance(step, tuple):
                time.sleep(step[1])
            else:
                try:
                    os.write(self.write_fd, step)
                except OSError:
                    break
        os.close(self.write_fd)

    def output_fd(self):
        return self.read_fd

    def poll(self):
        return self.code

    def kill(self):
        self.killed.set()
        os.close(self.read_fd)


class Spawner:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return FakeProcess(self.scripts.pop(0) if self.scripts else ["exit"])


class FakeStop:
    """A stop event whose waits return at once; it is set after ``waits`` retries."""

    def __init__(self, waits):
        self.waits = waits
        self.delays = []
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, delay):
        self.delays.append(delay)
        if len(self.delays) >= self.waits:
            self._set = True


def frame_bytes(value):
    return bytes([value]) * SIZE


def make_reader(spawner, stall=0.3):
    got, states = [], []
    reader = FrameReader(
        "garagem",
        "rtsp://127.0.0.1:8554/garagem",
        5,
        on_frame=got.append,
        on_state=lambda state, detail: states.append((state, detail)),
        spawn=spawner,
        stall_seconds=stall,
        width=W,
        height=H,
    )
    return reader, got, states


def test_ffmpeg_command():
    assert frames.ffmpeg_command("rtsp://127.0.0.1:8554/garagem", 5) == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-hwaccel",
        "videotoolbox",
        "-i",
        "rtsp://127.0.0.1:8554/garagem",
        "-vf",
        "fps=5,scale=1280:720",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def test_frames_are_cut_exactly_even_when_split_across_reads():
    data = frame_bytes(1) + frame_bytes(2) + frame_bytes(3)
    script = [data[:5], ("sleep", 0.02), data[5:30], data[30:], "exit"]
    reader, got, states = make_reader(Spawner(script))
    reader.run(FakeStop(waits=1))
    assert [int(f.image[0, 0, 0]) for f in got] == [1, 2, 3]
    assert all(f.image.shape == (H, W, 3) and f.image.dtype == np.uint8 for f in got)
    assert all(f.camera_id == "garagem" and f.wall.tzinfo is not None for f in got)
    assert states[0] == ("online", None)
    assert states[1][0] == "reconnecting"
    assert "ffmpeg exited" in states[1][1]


def test_partial_frame_at_exit_is_dropped():
    reader, got, _ = make_reader(Spawner([frame_bytes(1) + b"\x00" * 7, "exit"]))
    reader.run(FakeStop(waits=1))
    assert len(got) == 1


def test_stall_is_detected_and_process_killed():
    spawner = Spawner([frame_bytes(1), "hang"])
    reader, got, states = make_reader(spawner, stall=0.2)
    start = time.monotonic()
    reader.run(FakeStop(waits=1))
    assert time.monotonic() - start < 1.5
    assert len(got) == 1
    assert states[-1][0] == "reconnecting"
    assert "no frame for 0.2 s" in states[-1][1]


def test_backoff_doubles_up_to_five_minutes_and_resets_after_a_frame():
    failures = [["exit"]] * 8
    spawner = Spawner(*failures, [frame_bytes(9), "exit"], ["exit"])
    reader, _, states = make_reader(spawner)
    stop = FakeStop(waits=10)
    reader.run(stop)
    assert stop.delays == [5, 10, 20, 40, 80, 160, 300, 300, 5, 10]
    assert ("online", None) in states
    assert len(spawner.commands) == 10


def test_stop_ends_the_loop_without_reporting_reconnecting():
    stop = threading.Event()
    spawner = Spawner(["hang"])
    reader, _, states = make_reader(spawner, stall=30)
    thread = threading.Thread(target=reader.run, args=(stop,))
    thread.start()
    time.sleep(0.1)
    stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert states == []


def test_next_delay():
    delays = [frames.next_delay(None)]
    for _ in range(8):
        delays.append(frames.next_delay(delays[-1]))
    assert delays == [5, 10, 20, 40, 80, 160, 300, 300, 300]


def test_ffmpeg_missing_is_retried_like_a_failure():
    def missing(command):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    reader, _, states = make_reader(missing)
    stop = FakeStop(waits=2)
    reader.run(stop)
    assert stop.delays == [5, 10]
    assert states[0][0] == "reconnecting"
    assert "could not start ffmpeg" in states[0][1]
