"""Spike 2.3: run the detector's FFmpeg command, count frames, sample CPU and memory."""
import subprocess, sys, threading, time
from sentinel.detector.frames import ffmpeg_command

SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 60
cmd = ffmpeg_command("rtsp://127.0.0.1:8554/garagem", 5)
if sys.argv[1] == "software":
    i = cmd.index("-hwaccel"); del cmd[i:i + 2]
size = 1280 * 720 * 3
start = time.monotonic()
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
samples = []
def sample():
    while proc.poll() is None:
        out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(proc.pid)], capture_output=True, text=True).stdout.split()
        if out: samples.append((float(out[0]), int(out[1]) / 1024))
        time.sleep(2)
threading.Thread(target=sample, daemon=True).start()
first = None; frames = 0
while time.monotonic() - start < SECONDS:
    data = proc.stdout.read(size)
    if len(data) < size: break
    frames += 1
    if first is None: first = time.monotonic() - start
proc.kill(); proc.wait()
steady = samples[2:] or samples
print(f"{sys.argv[1]}: first frame {first:.2f} s, {frames} frames in {SECONDS:.0f} s "
      f"({frames / (SECONDS - first):.2f} fps after the first), "
      f"ffmpeg CPU avg {sum(c for c, _ in steady) / len(steady):.1f}% max {max(c for c, _ in steady):.1f}%, "
      f"RSS max {max(m for _, m in steady):.0f} MB")
print("stderr:", proc.stderr.read().decode()[-300:].strip() or "(none)")
