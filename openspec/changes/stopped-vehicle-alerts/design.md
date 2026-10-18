## Context

The project folder has only OpenSpec scaffolding; this change creates the service. What is known about the DVR comes from the user's HomeView project (`../home-view`, read only), which plays every channel of the DVR at 192.0.2.10 over RTSP with Digest auth and documents its findings in `docs/spike-notes.md`: substreams are H.264 352x240 at 7 fps, main streams are H.265 at 960x1080 (1080N, shown at 16:9; channel 8 at 1280x720), the DVR accepted 8 concurrent sessions, it locks a user after about 5 failed logins, and its clock runs about 20 minutes ahead.

The service runs on the user's MacBook Air (Apple silicon, macOS 26). The user knows and accepts that the Mac sleeping, rebooting for an update or leaving the house stops the service. The phone is an iPhone. A Samsung TV (Tizen 6.5) will later show cameras and alerts, so the design keeps detection and delivery apart.

The project is personal: built and run locally, never distributed.

## Goals / Non-Goals

**Goals:**
- A push on the iPhone, with a picture, within 15 seconds of a vehicle reaching its dwell time in a zone.
- Few false alarms: passing traffic, a car stopping briefly at the curb, shadows and rain must not notify.
- Recover by itself: after a crash, a lost stream, a DVR reboot or a Mac restart (once the user logs in), without the user touching anything.
- Never lock the DVR user, never leak credentials, never expose a port outside the Mac and the tailnet.
- Decoupled: the detector publishes events and knows nothing about phones or TVs; a new consumer needs no change to the detector.
- Cheap enough to leave running: under 25% of one core and 1.5 GB of RAM with two watched cameras at 5 fps.
- Pure logic (dwell rules, zones, event contract, message text, credential probe) covered by unit tests.

**Non-Goals:**
- The TV app and TV notifications (a later change that subscribes to the same events).
- Recording, timeline, plates, recognizing known vehicles, people on foot.
- A native iPhone app or APNs certificates.
- Running anywhere but this Mac (kept possible, not done).
- Keeping the Mac awake: sleep is an accepted limitation.

## Decisions

### 1. Repository layout
```
homeview-sentinel/
  pyproject.toml, uv.lock     Python 3.12 project "sentinel" (uv, pytest, ruff)
  Makefile                    setup, secrets, up, down, status, logs, test, replay, zone-preview
  config/
    sentinel.example.yaml     committed example; the real sentinel.yaml is git-ignored
  contracts/
    event.v1.schema.json      the event contract (source of truth for every consumer)
  src/sentinel/
    config.py                 load and validate sentinel.yaml
    secrets.py                read/write Keychain items (security CLI)
    rtsp_probe.py             one authenticated DESCRIBE, classified result
    gateway/                  go2rtc config rendering and launcher
    detector/                 frame reader, inference backends, association, tracking,
                              zones, dwell state machine, snapshots, pipeline, replay
    bus/                      event models, MQTT client wrapper, status/LWT
    notifier/                 subscription, rules (quiet hours, offline), ntfy client, pt-BR text
  containers/                 go2rtc/, mosquitto/, ntfy/, notifier/ (Containerfile + configs)
  launchd/                    plist templates rendered by `make install`
  tests/                      pytest
  docs/                       spike-notes.md, verificacao-manual.md
```

### 2. Containers with Apple's `container`, supervised by launchd
go2rtc, Mosquitto, ntfy and the notifier only need the network, so they run as Linux containers with Apple's `container` tool (one lightweight VM per container, no Docker Desktop). The detector runs natively because no container on macOS can reach VideoToolbox or the Neural Engine.

Each container runs in the foreground (`container run --rm …`) under its own launchd agent with `KeepAlive`, so launchd restarts it if it exits, exactly like the native detector. A first agent runs `container system start` at login and the others wait for it. Ports are published on 127.0.0.1 only; ntfy is additionally reached from the tailnet through Tailscale Serve (decision 11). Containers reach each other through the host's published loopback ports or the `container` network's DNS, whichever the spike shows to be reliable.

*Alternatives:* Docker Desktop or Colima (heavier, another VM manager, licensing for Docker Desktop); everything native with Homebrew services (simpler, but loses the isolation and makes the later move to a Linux mini PC a rewrite instead of a copy). The spike (task 2.1) confirms foreground supervision, restart, port publishing on loopback and LAN access to the DVR from inside a container before anything else depends on it.

### 3. Stream gateway: go2rtc with a dedicated DVR user
go2rtc holds one RTSP session per configured stream to the DVR and serves any number of local consumers from it (`rtsp://127.0.0.1:8554/<camera_id>`). Sentinel uses its own DVR user (`sentinel`, live view only, created by hand on the DVR), separate from HomeView's `macApp`, so a mistake in one can never lock the other.

The go2rtc config in the repo has no secrets: the stream source is written as `rtsp://${DVR_USER}:${DVR_PASSWORD}@192.0.2.10:554/cam/realmonitor?channel=N&subtype=S`, and the launcher reads the user and password from the Keychain and passes them to the container as environment variables at start. Credentials are percent-encoded the same way HomeView's `StreamURL` does (only RFC 3986 unreserved characters kept), which HomeView proved FFmpeg decodes correctly.

Before starting go2rtc, the launcher runs `rtsp_probe` (a Python port of HomeView's `RTSPProbe` behaviour): one `DESCRIBE`, answer the Digest challenge once, classify as ok / wrong credentials / not found. The launcher runs on the Mac (it needs the Keychain), starts the container as its child process, and stays alive as the gateway's MQTT client, publishing its status and Last Will. On wrong credentials the launcher exits with a clear message and launchd is told not to restart it (exit code mapped to "do not relaunch" via a marker file the launcher checks), so a bad password costs exactly one failed login. go2rtc's API and WebRTC ports are disabled or bound to loopback.

Only the cameras listed in `sentinel.yaml` are configured in go2rtc; other channels are never requested.

### 4. Which stream feeds the detector
The substream (352x240) leaves a motorcycle across the street only a few pixels tall, too few for a reliable detection. The detector therefore reads the **main stream** of each watched camera and samples it down. HEVC decoding is done by FFmpeg with VideoToolbox, so the 30 fps decode costs little; frames are dropped to the sampling rate before they reach Python.

If the spike (2.4) shows that detection on the substream is good enough after all, or the user raises the substream resolution on the DVR, `sentinel.yaml` can point a camera at `subtype=1`; nothing else changes.

Total sessions: HomeView's own (up to 8, only while its panel is open) plus one per watched camera from go2rtc. The spike (2.2) checks that the DVR serves HomeView's 8 substreams while go2rtc holds the watched main streams.

### 5. Frame reader: an FFmpeg subprocess per camera
```
ffmpeg -rtsp_transport tcp -hwaccel videotoolbox -i rtsp://127.0.0.1:8554/<camera_id>
       -vf fps=5,scale=1280:720 -pix_fmt bgr24 -f rawvideo pipe:1
```
The reader reads exactly 1280×720×3 bytes per frame from the pipe into a NumPy array. `scale=1280:720` both shrinks the frame and stretches 960x1080 to its intended 16:9 shape (channel 8, already 16:9, is only scaled). Each frame carries the Mac's monotonic time and wall-clock time at read.

When FFmpeg exits or no frame arrives for 10 s, the reader kills the process, publishes the camera as `reconnecting`, and starts it again after 5 s, doubling up to 5 minutes, back to 5 s after the first frame. The gateway, not the reader, talks to the DVR, so these retries never reach the DVR with a login.

*Alternatives:* OpenCV `VideoCapture` (no VideoToolbox for HEVC in the pip wheels, so software decoding); PyAV (hardware decoding is possible but less proven than the FFmpeg CLI on macOS); go2rtc's JPEG snapshot API (re-encodes every frame, too slow for 5 fps).

### 6. Inference behind a backend interface
```python
class InferenceBackend(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]  # class, confidence, box (pixels)
```
The CoreML backend loads an Ultralytics YOLO model exported with `yolo export format=coreml nms=True` (model size, nano or small, chosen in the spike from accuracy on the real cameras at night and latency) and keeps only `car`, `motorcycle` and `person` (COCO classes; `truck` and `bus` count as `car`). A fake backend replays scripted detections in tests. An OpenVINO backend for a future mini PC is a new class, nothing else.

The exported model file is not committed; `make setup` exports it once and records the Ultralytics version and model name in `models/README.md`. Ultralytics is AGPL-3.0, acceptable for a project that is never distributed.

### 7. Motorcyclist association
A `person` whose box's bottom half overlaps a `motorcycle` box by at least 30% of the person box's area is attached to that motorcycle as its rider, in the same frame. The tracked motorcycle is "with rider" when a rider was attached in at least half of its last 10 frames. Persons attached to a motorcycle are not tracked on their own. Pure function, unit tested.

### 8. Tracking and zones
`supervision`'s ByteTrack assigns track IDs across frames (one tracker per camera, `lost_track_buffer` of 3 s at the sampling rate). Zones are polygons in normalized coordinates (0–1) of the 16:9 frame, written in `sentinel.yaml`:

```yaml
cameras:
  - id: garagem
    name: Garagem
    channel: 1
    stream: main
    zones:
      - id: calcada
        name: Calçada
        polygon: [[0.05, 0.62], [0.95, 0.58], [0.98, 0.95], [0.02, 0.97]]
```
An object is in a zone when the bottom center of its box (where it touches the ground) is inside the polygon. Objects outside every zone are ignored. `make zone-preview CAMERA=garagem` saves the current frame with zones and live detections drawn to `docs/zone-preview-garagem.png` (git-ignored) to adjust the polygons.

### 9. The dwell state machine
Each tracked vehicle in a zone runs a small automaton, driven only by observations (frame time, box) and an injected clock, so it is pure and fully unit tested:

```
            enter zone                  still ≥ threshold                 leave/lost > end grace
 (absent) ───────────► MOVING ──still──► STOPPED(since) ─────────────► ALERTED ─────────────────► ENDED
                         ▲                   │ moves (> grace)              (dwell.started)        (dwell.ended)
                         └───────────────────┘
```
- **Still**: the box's center moved less than 2% of the frame diagonal over the last 5 s window (configurable).
- **Movement grace**: movement or a missing detection shorter than 3 s does not reset `since` (a rider shifting, a pedestrian passing in front).
- **Thresholds**: per class, overridable per camera: motorcycle 30 s, car 120 s.
- **End grace**: an alerted object is ended after it leaves the zone or has been lost for 10 s; only alerted objects produce `dwell.ended`.
- **Cooldown**: after a `dwell.started` in a camera's zone, another one for the same class in the same zone is not emitted for 10 minutes, unless the first one has ended (so a car that leaves and another that arrives still alerts; a tracker that loses and re-finds the same parked car does not).

ByteTrack can give a parked car a new ID after a long occlusion. To avoid a second alert for the same car, a new track that appears still within 5% of the diagonal of a recently ended or lost alerted track (same class, same zone, within 2 minutes) inherits its `since` and alert state.

### 10. Event bus and contract
Mosquitto (container) on `127.0.0.1:1883`, authentication required (one user per component, passwords in the Keychain, rendered into a password file at start), persistence on.

Topics (versioned prefix, so a breaking change goes to `v2` while `v1` consumers keep working):
- `sentinel/v1/events/<camera_id>`: QoS 1, not retained. `dwell.started` and `dwell.ended`.
- `sentinel/v1/status/<component>`: retained; `{"state": "online" | "reconnecting" | "offline", "since": …, "detail": …}`; the broker publishes `offline` as Last Will when a component disconnects.

Event (`contracts/event.v1.schema.json`):
```json
{
  "schema": "sentinel.event/v1",
  "event_id": "5f0c1c0e-8a43-4d43-9a7e-2f1b6c3d9e10",
  "type": "dwell.started",
  "occurred_at": "2026-10-18T15:04:37Z",
  "camera": {"id": "garagem", "name": "Garagem", "channel": 1},
  "zone": {"id": "calcada", "name": "Calçada"},
  "object": {"class": "motorcycle", "with_rider": true, "track_id": "garagem-42", "confidence": 0.81},
  "dwell": {"since": "2026-10-18T15:04:07Z", "seconds": 30},
  "snapshot": {"file": "garagem/2026-10-18T15-04-37Z-garagem-42.jpg", "width": 1280, "height": 720}
}
```
`dwell.ended` carries the same fields, with `dwell.seconds` as the total and the start's `event_id` in `started_event_id`:
```json
{
  "schema": "sentinel.event/v1",
  "event_id": "b2d7e5a4-3c1f-4e8b-9d6a-7f0e1c2b3a49",
  "type": "dwell.ended",
  "occurred_at": "2026-10-18T15:11:07Z",
  "camera": {"id": "garagem", "name": "Garagem", "channel": 1},
  "zone": {"id": "calcada", "name": "Calçada"},
  "object": {"class": "motorcycle", "with_rider": true, "track_id": "garagem-42", "confidence": 0.81},
  "dwell": {"since": "2026-10-18T15:04:07Z", "seconds": 420},
  "snapshot": {"file": "garagem/2026-10-18T15-04-37Z-garagem-42.jpg", "width": 1280, "height": 720},
  "started_event_id": "5f0c1c0e-8a43-4d43-9a7e-2f1b6c3d9e10"
}
```
The detector validates every event against the schema before publishing (tests too). Times are the Mac's clock in UTC; consumers format them in local time. The snapshot is referenced by file name relative to the snapshots folder, never embedded.

Consumers use persistent sessions (`clean_session=false`, fixed client id), so a notifier restart does not lose events published meanwhile.

### 11. Phone delivery: self-hosted ntfy, upstream ntfy.sh, Tailscale Serve
iOS only wakes an app through APNs, so a self-hosted ntfy server must set `upstream-base-url: https://ntfy.sh`: for each message, ntfy.sh receives only a poll request (topic hash and message id), the iPhone app wakes up and fetches the message and attachment from the home server. The server (container) has `auth-default-access: deny-all`, one user for the phone (read on its topic) and a token for the notifier (write), an attachment cache in Application Support, and `base-url` set to the Mac's Tailscale HTTPS name.

Tailscale Serve (`tailscale serve --bg --https=443 http://127.0.0.1:2586`) gives the server a valid HTTPS certificate on `https://<mac>.<tailnet>.ts.net`, reachable from the iPhone at home and on mobile data, and nowhere else. The spike (2.5) confirms the iOS app subscribes to it, receives instantly with the screen locked, and shows the attachment.

*Alternatives:* public ntfy.sh topics (simplest, but the snapshot of the street would be uploaded to a third party); Pushover (paid, third-party server); a Telegram bot (a chat, easy to mute and ignore); a native app with APNs (needs the paid Apple Developer Program; later, maybe).

### 12. The notifier
A small Python service (container) subscribes to `sentinel/v1/events/+` and `sentinel/v1/status/detector`, and sends to ntfy with `PUT /<topic>` carrying the snapshot as the body and the message in headers (`Title`, `Message`, `Priority`, `Tags`, `Filename`). Message text and rules are pure functions with tests:
- `dwell.started`, motorcycle with rider: title "Moto com piloto parada em frente de casa", priority high.
- `dwell.started`, motorcycle without rider: "Moto parada em frente de casa", priority default.
- `dwell.started`, car: "Carro parado em frente de casa", priority default.
- Body: "Garagem · Calçada · parado há 2 min · 15:04" ("parada" for a motorcycle; local time, Etc/GMT+4).
- `dwell.ended`: not sent by default; when enabled, priority low: "Saiu após 7 min".
- Quiet hours (config, default off): notifications still go out, at priority low (no sound).
- Detector offline for more than 2 minutes: "O Sentinel parou de vigiar as câmeras"; back online: "O Sentinel voltou a vigiar". If the whole Mac is asleep, nothing can be sent: that is the accepted limitation, written in the README.

The notifier knows nothing about detection; the TV later gets its own consumer, not a branch in this one.

### 13. Configuration and secrets
`config/sentinel.yaml` (git-ignored; `sentinel.example.yaml` committed) holds cameras, zones, thresholds, sampling rate, model, quiet hours and the ntfy topic. It is validated at start with clear errors naming the field. Secrets never go in it: `make secrets` asks for the DVR user and password, and generates the MQTT and ntfy credentials, and stores them as generic passwords in the login Keychain under the service `com.carloseduardo.homeview-sentinel` (account = item name) with the `security` CLI. The launchers read them at start and pass them as environment variables only to the process that needs each one.

### 14. Runtime, logs and data
`make install` renders the launchd agents (`com.carloseduardo.homeview-sentinel.*`) into `~/Library/LaunchAgents` and loads them; `make up` / `make down` load and unload them; `make status` prints each component's retained MQTT status plus `launchctl` state; `make logs` tails the logs. Logs go to `~/Library/Logs/HomeView Sentinel/<component>.log`, rotated daily, 7 days kept; stream URLs are logged without credentials. Data (snapshots, Mosquitto persistence, ntfy cache and user database) goes to `~/Library/Application Support/HomeView Sentinel/`; snapshots older than 14 days are deleted once a day.

### 15. Replay for calibration
`make replay CLIP=clips/garagem-noite.mp4 CAMERA=garagem` runs the same pipeline over a recorded clip, as fast as the backend allows, with the clock taken from the clip's timestamps, and prints the events instead of publishing them. `make record CAMERA=garagem MINUTES=10` saves a clip from the gateway (git-ignored `clips/`). Tuning zones and thresholds on real day and night clips comes before the service is trusted.

### 16. Testing
pytest for: config validation, Keychain helper (test-only service name, cleaned up), RTSP probe classification over a fake server (Digest without qop, wrong credentials, timeout), go2rtc config rendering (no secrets in the file), frame reader framing and backoff with a fake process, rider association, zone geometry, the dwell state machine (every scenario in the dwell-alerts spec, with an injected clock), track re-identification, event serialization against the JSON Schema, notifier text and rules, ntfy request building over a fake HTTP server. A broker integration test runs against the real Mosquitto container and is skipped when it is not running. Real-world behaviour (cameras, iPhone, Tailscale, restarts) is checked by hand with `docs/verificacao-manual.md`.

### 17. Ready for the TV
The TV integration, later, will add consumers and nothing else: a TV agent subscribing to `sentinel/v1/events/+` that opens the camera app on the TV through the TV's local WebSocket API, and go2rtc gaining an HLS output for the Tizen app. Neither touches the detector or the contract.

## Risks / Trade-offs

- [The Mac sleeps, reboots or leaves home, and nothing is watched] → Accepted by the user. The README says so plainly; the offline alert covers a detector crash, not a sleeping Mac. Moving to an always-on mini PC later is a copy of the containers plus a new inference backend.
- [Apple's `container` is young: foreground supervision, restarts, loopback publishing or LAN access may misbehave] → Spike 2.1 before anything depends on it. Fallback: the same images and configs under Colima, with no change to the code.
- [The DVR refuses extra sessions when HomeView's 8 substreams and go2rtc's main streams run together] → Spike 2.2. Fallback: watch fewer cameras, or have HomeView-like consumers read through go2rtc in a future change (HomeView itself is not modified here).
- [A wrong DVR password locks the Sentinel user] → One probe before go2rtc starts, no restart on wrong credentials, a dedicated user so HomeView is never affected.
- [Night (infrared, black and white), rain and headlights cause missed detections or false stops] → Model size chosen on night clips (spike 2.4), a minimum confidence, the movement grace, and calibration by replay before trusting it. The first weeks run with `dwell.ended` notifications on to see what triggers.
- [Objects far across the street are too small] → Main stream at 1280x720, zones limited to the near side, model `small` if `nano` misses them.
- [ByteTrack ID switches cause duplicate or missed alerts] → Cooldown and re-identification of a still track near a recent alerted one (decision 9), covered by tests.
- [ntfy.sh is down or slow, delaying the iOS wake-up] → Messages still reach the app when it is opened; acceptable for a personal tool. Pushover can be added as a second adapter later.
- [Ultralytics licensing (AGPL-3.0)] → Fine for a personal, undistributed project; the backend interface keeps a switch to another model runtime contained.
- [Continuous HEVC decode and inference drain the battery when unplugged] → Accepted; `make status` shows CPU use; the sampling rate is configurable.

## Open Questions

- Which channels face the street, and where exactly the zones go: decided with the user during calibration (task 6.6), not in this design.
- Whether to also watch the substream of other cameras later, for people on foot: out of scope here.
- Whether the user will raise the substream resolution on the DVR (it would make the substream usable for detection and lighten decoding): optional, decided after spike 2.4; no DVR setting is changed by this change.
