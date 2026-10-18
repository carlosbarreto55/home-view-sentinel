# Spike notes

Findings from the spikes of the `stopped-vehicle-alerts` change (tasks 2.1–2.5).
They feed the decisions recorded in design.md (task 2.6).

## 2.1 Apple `container` under launchd

Date: 2026-10-18. Mac: Apple M5, macOS 26.6.2. `container` 1.5.0 (Homebrew),
image `alexxit/go2rtc:1.9.10`, DVR at 192.0.2.10:554.

Result: **Apple `container` works for the gateway, with two workarounds in the
launcher** (signals, leftover containers).

### Setup

- `brew install container`, then `container system start --enable-kernel-install`
  installs the recommended kernel (about 1 minute, once).
- `container system start` registers its services (`com.apple.container.apiserver`
  and others) as transient launchd jobs, with no plist: they do not come back
  after a logout or reboot. A login agent that runs `container system start
  --disable-kernel-install` is needed before the container agents, as planned.
- Pulling `alexxit/go2rtc:1.9.10` (185 MB) took about 50 s. The first `container
  run` also downloads the init image (66 MB, about 10 s).

### Network

- A container reaches the DVR: `nc -z 192.0.2.10 554` from inside succeeds.
  Containers sit on `192.168.64.0/24` (gateway and DNS 192.168.64.1) behind NAT.
- `-p 127.0.0.1:8554:8554` listens on the Mac's loopback only (`lsof` shows
  `127.0.0.1:8554`), and `ffprobe rtsp://127.0.0.1:8554/garagem` from macOS reads
  the camera through go2rtc. The Mac's LAN address (192.0.2.20) refuses 8554.
- Risk: the Mac has IP forwarding on (`net.inet.ip.forwarding: 1`) and the
  container's own address (e.g. 192.168.64.6:8554) answers from the Mac. A LAN
  device that adds a route to 192.168.64.0/24 through the Mac might reach a
  container directly, around the loopback publish. Not tested from another
  device. go2rtc's RTSP server has no login, so the gateway should either set an
  RTSP user and password or block forwarding into the container subnet (pf).
- The container's address changes on every start (.6, .10, .11, .13…).
- Container to container:
  - By the other container's IP: works, but the IP is not stable.
  - By name: no DNS name by default (`nslookup spike-go2rtc` fails,
    `container system dns list` is empty). Local DNS needs a one-time
    `sudo container system dns create <domain>`.
  - Through a port published on 127.0.0.1: fails (that is the Mac's loopback,
    not the container's).
  - Through a port published on the host's bridge address
    (`-p 192.168.64.1:9999:9999`): works from other containers, and stays closed
    on the LAN address.
  - Simplest of all: only the services (go2rtc, Mosquitto, ntfy) run in
    containers and every client (detector, notifier, launchers) runs natively and
    uses the loopback ports. Then no container talks to another.

### Signals and launchd

- `container run` 1.5.0 does not forward SIGTERM: it prints `failed to send
  signal: ["signal": 15, "error": invalidArgument: "missing signal in xpc
  message"]` and both the client and the container keep running.
- Killing the `container run` client leaves the container running, so a restarted
  agent would hit the name in use.
- What works (the spike launcher, run by a LaunchAgent with `KeepAlive`):
  1. On start: `container stop <name>; container delete <name>` (both quick, and
     harmless when nothing is there).
  2. Run `container run --rm --name <name> …` as a child (not `exec`).
  3. Trap SIGTERM/SIGINT: `container stop <name>`, wait for the child, exit 0.
  4. When the child exits on its own, exit non-zero so launchd restarts.
- Results with that launcher:
  - `container kill` from outside: launchd restarted it, go2rtc listening again
    after about 1–2 s (exit code 137, `runs = 2`).
  - `kill -9` of the launcher: the new launcher stopped the leftover container
    and started a fresh one; no orphan process.
  - `launchctl bootout`: the trap stopped the container; nothing left after
    about 2 s.
- The DVR login reached go2rtc only through the environment (`-e DVR_USER -e
  DVR_PASSWORD` with values exported by the launcher from the Keychain). It never
  appears on a command line (`pgrep -fl` shows only the names).

### Timings

- Container start with the image cached: 0.6–0.8 s (`alpine`, `go2rtc -version`).
- `container stop`: about 0.2 s.
- Launcher start to go2rtc listening: 1.7 s on restarts. The first start after
  loading the agent took 10 s; I could not reproduce that (Keychain read 0.13 s,
  `uv run` 0.12 s, container start 0.7 s each when measured separately).
- `container system start` when the system was stopped: 0.3 s. The start after
  a real reboot is measured with the launchd agents (task 8.3).

### Other

- Each container gets 4 CPUs and 1 GB by default (`container list`); set `-c`
  and `-m` per service.
- Pulling `alpine:3.22` unpacked every platform of the index (including s390x),
  not just arm64.
- Channel 1's main stream through go2rtc: HEVC, 960x1080, 30 fps. That is not
  16:9 (see 2.3).

## 2.2 go2rtc against the DVR

Date: 2026-10-18. go2rtc 1.9.10 in a container (the 2.1 launcher), user
`sentinel` (live view only), stream `garagem` = channel 1, main stream
(`subtype=0`).

Result: **one DVR session serves every local consumer, and the DVR serves
HomeView's 8 substreams at the same time.**

- go2rtc connects to the DVR only when the first consumer asks for the stream.
  With two FFmpeg readers on `rtsp://127.0.0.1:8554/garagem` (and later a third),
  `netstat` inside the container showed exactly one connection to
  192.0.2.10:554 and one local connection per reader.
- The connection to the DVR goes through the containers' NAT and does not appear
  in the Mac's `netstat`/`lsof`; count it from inside the container
  (`container exec <name> netstat -tn`).
- With HomeView's grid open (8 substream sessions, owned by HomeView in `lsof`)
  plus go2rtc's main stream, the DVR held 9 sessions:
  - The restream delivered the full 30 fps (259 frames in 10 s, speed 1.03x);
    a new reader got its first frame in about 1 s. The two background readers
    logged no errors over several minutes.
  - HomeView's 8 tiles kept playing. Closing and reopening the panel while
    go2rtc held its session brought all 8 back.
- HomeView's tiles looked a little laggy, but just as laggy with Sentinel fully
  stopped (no container, no readers, no DVR connection from Sentinel), so it is
  not caused by Sentinel. It may be worth a look in HomeView later.
- The Mac stayed light: each FFmpeg reader decoding the main stream in software
  used about 7% CPU; load average about 2.

## 2.3 FFmpeg with VideoToolbox

Date: 2026-10-18, at night (about 19:30 local). FFmpeg 9.0.2 (Homebrew),
channel 1's main stream through go2rtc, the detector's exact command
(`frames.ffmpeg_command`: `-hwaccel videotoolbox`, `fps=5,scale=1280:720`,
`bgr24` raw pipe). Measured with `scripts/spike-decode.py` (60 s per run).

Result: **VideoToolbox decodes the main stream at 5 fps for about 9% of one
core, and 1280x720 gives the right 16:9 picture.**

| Decoder | First frame | Frames in 60 s | FFmpeg CPU (avg / max) | FFmpeg memory |
| --- | --- | --- | --- | --- |
| VideoToolbox | 3.8 s | 283 (5.04 fps) | 8.6% / 11.7% | 97 MB |
| Software | 3.6 s | 284 (5.03 fps) | 13.8% / 19.7% | 84 MB |

- VideoToolbox is really used: FFmpeg's verbose log says "Selecting decoder
  'hevc' because of requested hwaccel method videotoolbox", with no fallback
  message and 0 decode errors.
- Software decoding would also be affordable for one camera; VideoToolbox saves
  about a third of the CPU, which matters with more cameras.
- Time to first frame: about 3.7 s when go2rtc has to open its DVR session,
  about 2 s when go2rtc is already connected. The keyframe interval is 2 s, so
  a new reader waits up to 2 s for one.
- The main stream is HEVC Main, 960x1080, 30 fps, about 1.6 Mbit/s, and
  signals no sample aspect ratio. At 960x1080 the picture is squeezed
  horizontally (round wheels look tall); scaled to 1280x720 the proportions are
  right. So it is a half-width ("1080N") 16:9 picture, and the detector's
  `scale=1280:720` is correct. Zones are normalized, so they are drawn on the
  1280x720 frame.
- The DVR's on-screen clock was about 25 minutes ahead of the Mac (20:01 shown
  at about 19:36 local). Sentinel uses the Mac's clock everywhere.
