# HomeView Sentinel

An always-on service on a home Mac that watches the street-facing cameras of a
DVR, notices when a **motorcycle or car stops in front of the house and
stays there**, and sends a push notification to the iPhone within seconds, with a
picture of what is there.

Passing traffic is ignored. Only a vehicle that stays stopped inside a zone
(by default 30 s for a motorcycle, 2 min for a car) produces an alert. Video
never leaves the home network.

![The alert snapshot of a real delivery: the motorcycle in red, the zone in yellow](docs/images/alert-snapshot.jpg)

*The first real detection, on 2026-10-18 at 20:15. A food delivery motorcycle
stopped at the curb in front of the garage. This is the snapshot Sentinel
attaches to the push: the vehicle in red, the "Calçada" zone in yellow. People
are pixelated, and everything but the street and the sidewalk is blurred for
privacy.*

The push that arrived on the iPhone, with that picture attached:

> **Moto parada em frente de casa**
> Garagem · Calçada · parada há 30 s · 20:15

---

## How it works

```
                 RTSP (one session per camera)
  DVR ─────────────────────────────────────────▶ go2rtc gateway ──▶ local restreams
                                                  (container)             │
                                                                          ▼
  iPhone ◀── ntfy app ◀── ntfy server ◀── notifier ◀── MQTT ◀── detector (native)
     ▲        (push)      (container)    (native)   (Mosquitto)  FFmpeg + VideoToolbox
     │                         │                                  YOLO on CoreML
     └──── Tailscale ──────────┘                                  tracking, zones, dwell
         (away from home)        ntfy.sh only wakes the phone up
```

1. **Stream gateway.** [go2rtc](https://github.com/AlexxIT/go2rtc) is the only
   component that talks to the DVR. It logs in with a dedicated **live-view-only
   DVR user** and restreams the watched cameras on the Mac. Any number of
   readers share one DVR session per stream.
2. **Detector.** This is a native Python process, because containers on macOS
   cannot use the hardware decoder or the Neural Engine.
   - FFmpeg decodes each camera's main stream with VideoToolbox at 5 fps and
     scales it to 1280x720. The DVR sends a squeezed 960x1080 picture, and this
     restores 16:9.
   - A YOLO11 model exported to CoreML finds cars, motorcycles and people.
   - A person sitting on a motorcycle becomes its rider.
   - Vehicles are tracked across frames and placed in the camera's zones.
3. **Dwell alerts.** A per-vehicle state machine decides when a vehicle has
   *stopped and stayed*, which is different from just passing by.
   - It publishes one `dwell.started` event, with a snapshot, when the vehicle
     has been stopped longer than the threshold.
   - It publishes `dwell.ended` when the vehicle leaves.
   - A cooldown stops the same vehicle from alerting twice.
4. **Event bus.** Components talk only through MQTT, on
   `sentinel/v1/events/<camera>` and `sentinel/v1/status/<component>`. Messages
   follow a versioned JSON contract
   ([`contracts/event.v1.schema.json`](contracts/event.v1.schema.json)). The
   detector never knows who listens, so a TV app or anything else can subscribe
   later without changing detection.
5. **Notifier.** It turns events into pushes in Portuguese. It sends them
   through a self-hosted [ntfy](https://ntfy.sh) server, with the snapshot
   attached, quiet hours, and an alert when the detector goes offline.
   - The public ntfy.sh server is used only as the iOS wake-up relay. The
     message and the picture stay on the Mac.
   - Away from home, the iPhone reaches the server through Tailscale.
     Nothing is port-forwarded.
6. **Control agent.** It is the only part that starts at login, and it uses
   next to nothing. It holds the watch mode (off, a timed watch of 1 to 12
   hours, or 24/7) and starts and stops every other component to match.
   - While watching on the power adapter, it keeps the Mac awake, even with the
     screen locked.
   - Other apps on the Mac can read its state and request a mode through two
     JSON files with versioned schemas in [`contracts/`](contracts/). No
     network port is opened.

### Seeing it work

The courier arriving on the street camera, as each model sees it (yolo11n in
red, yolo11s in blue):

![The delivery motorcycle coming up the street, detected by both models](docs/images/street-arrival.jpg)

The same stop on the garage camera, from arrival to well after the alert:

![Four frames of the stop: arrival, alert at 30 s, still stopped](docs/images/stop-timeline.jpg)

Several other motorcycles drove past during that hour. None stayed in view for
more than a few seconds, so none of them would have produced an alert.

Why the larger model: yolo11n (red) merges or loses the parked cars from frame
to frame, which would break tracks and restart dwell timers. yolo11s (blue)
holds each car steadily. Both take under 3 ms per frame on the Neural Engine.

![Parked cars: yolo11n draws one box around both, yolo11s one box per car](docs/images/models-garage.jpg)

## Status

Sentinel already runs on the real Mac, DVR and iPhone. Every OpenSpec change
is built and covered by more than **420 unit tests**. What is left is measuring
and verifying on the real hardware:

| Change | Tasks done | Left |
| --- | --- | --- |
| [`stopped-vehicle-alerts`](openspec/changes/stopped-vehicle-alerts/tasks.md) | 31 of 35 | calibrating the zones with daytime and nighttime clips, a one-hour resource measurement, the manual verification checklist, the full README |
| [`watch-schedule`](openspec/changes/watch-schedule/tasks.md) | 9 of 10 | the manual checks on the Mac (sleep, reboot, battery, killing the agent) |
| [`configurable-stream-path`](openspec/changes/configurable-stream-path/tasks.md) | 2 of 2 | nothing |

**Built and tested:**
- Configuration and Keychain secrets.
- The stream gateway: the DVR login probe, the go2rtc config and its container.
- The event bus: the Mosquitto container, the event contract and the MQTT client.
- The whole detector: frame reader, CoreML inference, riders, tracking, zones,
  the dwell state machine and snapshots stamped with the Mac's local time.
- Notifications: the ntfy server container, the messages in Portuguese, and the
  notifier service with offline alerts.
- The runtime: launchd agents, `make install` / `up` / `watch` / `down` /
  `status` / `logs`, and daily log rotation.
- The control agent: watch modes, keeping the Mac awake on the power adapter,
  the state and request contract for other apps, and the last alert.
- Clip recording, replay and zone preview tools.

**Validated on the real hardware** (Mac, DVR, iPhone): see
[docs/spike-notes.md](docs/spike-notes.md).
- Apple `container` works under launchd. Two workarounds are needed: `container
  run` does not pass on SIGTERM, and published ports need care.
- go2rtc against the DVR. One DVR session serves any number of readers. go2rtc
  logs the DVR password in the stream URL when a connection fails, so its output
  must be filtered before it reaches a log.
- VideoToolbox decodes a camera's main stream at 5 fps for about 9% of one CPU
  core.
- yolo11s on the main stream was chosen over yolo11n and over the substream.
- Pushes arrive instantly on the locked iPhone, on Wi-Fi and on mobile data,
  with the picture and correct Portuguese accents.
- While watching, all of Sentinel uses about 35% of one CPU core and 1.3 GB of
  RAM on a MacBook Air
  ([measured](openspec/changes/watch-schedule/proposal.md) on 2026-10-19). That
  cost is why the timed watch modes exist.

## Repository layout

| Path | What is in it |
| --- | --- |
| [`openspec/`](openspec/) | The spec-driven plan: [proposal](openspec/changes/stopped-vehicle-alerts/proposal.md), [design](openspec/changes/stopped-vehicle-alerts/design.md), [tasks](openspec/changes/stopped-vehicle-alerts/tasks.md), and one spec per capability (stream gateway, vehicle detection, dwell alerts, event bus, phone notifications, service runtime). |
| `src/sentinel/` | The Python package: `config`, `secrets`, `rtsp_probe`, `gateway/`, `bus/`, `detector/`, `notifier/`, `runtime/` (launchd agents and `make` commands), `control/` (the control agent). |
| `tests/` | Unit tests, including a fake RTSP server and scripted detections replayed through the real pipeline. |
| `contracts/` | The JSON Schemas of the MQTT events and of the control agent's state and requests. |
| `config/sentinel.example.yaml` | Cameras, zones, thresholds, quiet hours. Copy it to `config/sentinel.yaml`, which is not committed. |
| `docs/spike-notes.md` | What was measured on the real Mac, DVR and iPhone, and what it changed. |
| `scripts/` | The spike scripts (decode benchmark, model comparison, test pushes). |
| `models/` | Exported CoreML models (not committed) and their versions. |
| `.claude/`, `.codex/` | OpenSpec commands and skills for Claude Code and Codex. |

## How it was built: OpenSpec

The project is planned with [OpenSpec](https://github.com/Fission-AI/OpenSpec).

- **The changes.** The work is split into three changes, each with a proposal
  (why and what), a design (decisions, risks, open questions), capability specs
  written as requirements with WHEN/THEN scenarios, and a task list:
  - [`stopped-vehicle-alerts`](openspec/changes/stopped-vehicle-alerts/), the
    first version: 17 design decisions and six capability specs;
  - [`watch-schedule`](openspec/changes/watch-schedule/), the control agent and
    the watch modes: a new `watch-control` spec, plus changes to two existing
    ones;
  - [`configurable-stream-path`](openspec/changes/configurable-stream-path/),
    the DVR's RTSP path as a setting, so any DVR maker's path works.
- **One commit per task.** Each task is a single
  [Conventional Commit](https://www.conventionalcommits.org/) containing the
  code, its tests and the ticked box, and every commit passes `make test`.
  Problems found later get their own `fix(...)` commits. The history was
  rewritten only once, before the repository went public, to take private
  details out of it (addresses, location, unblurred pictures).
- **Spikes first.** Before building the services, "spike" tasks tested the
  risky assumptions on the real Mac, DVR and iPhone. Their findings are in
  `docs/spike-notes.md` and feed back into the design.

## Requirements

- A Mac with Apple silicon on macOS 26, plus [uv](https://docs.astral.sh/uv/),
  FFmpeg (`brew install ffmpeg`) and Apple's
  [`container`](https://github.com/apple/container).
- A DVR (or any camera with RTSP), with a user that can only
  view live video. DVR makers use different RTSP paths: set `dvr.stream_path`
  in `config/sentinel.yaml` to your DVR's (the example file shows the two
  common forms).
- An iPhone with the ntfy app. To get alerts away from home, Tailscale on both
  the Mac and the iPhone.

## Getting started

```sh
make setup      # install dependencies, export the YOLO model to CoreML
make secrets    # store the DVR login and generate service credentials in the Keychain
cp config/sentinel.example.yaml config/sentinel.yaml
                # set the cameras, the zones and notifications.ntfy_base_url
                # (the Mac's https://<mac>.<tailnet>.ts.net address)
make test
make install    # the control agent at login, the component agents, and
                # Tailscale Serve in front of ntfy
```

Then subscribe to the topic in the ntfy app on the iPhone, through the same
Tailscale address.

Watching is controlled through the control agent. Each command waits for its
answer:

```sh
make watch HOURS=2   # watch for 1, 2, 4, 8 or 12 hours, then stop by itself
make up              # watch 24/7
make down            # stop watching; only the control agent keeps running
make status          # the watch mode and time left, then each component's state, CPU and memory
make logs            # follow every component's log
make uninstall       # remove every launchd agent (data and logs are kept)
```

To calibrate the zones on real footage:

```sh
make record CAMERA=garagem MINUTES=10            # save a clip from the gateway
make replay CLIP=clips/<clip>.mp4 CAMERA=garagem # print the events it would produce
make zone-preview CAMERA=garagem                 # draw the zones on a live frame
```

## Security and privacy

- **Credentials.** The DVR password, broker passwords and ntfy tokens live only
  in the macOS Keychain. They never go in code, config files, container images,
  events or logs.
- **DVR access.** Sentinel uses its own DVR user, limited to live view, and reads
  RTSP only. A rejected login is never retried in a loop, because the DVR locks
  the user after about 5 failures.
- **Network exposure.** All services listen on the Mac only. Remote access is
  through Tailscale; nothing is port-forwarded.
- **Video.** Video and snapshots stay on the Mac. Snapshots are deleted after
  14 days.
- **Accepted limitation.** If the Mac is off, asleep on battery or away from
  home, nothing is watched. On the power adapter, Sentinel keeps the Mac awake
  while it watches. The detector-offline push makes a gap visible.

## Notes

- Notification text is in Portuguese (pt-BR); code, docs and specs are in
  English.
- This is a companion to [HomeView](https://github.com/carlosbarreto55/homeview),
  a menu bar camera viewer for the same DVR. The control contract is there so
  that HomeView can show Sentinel's state and switch its watch mode, which is a
  planned HomeView change.

## License

[GNU Affero General Public License v3.0](LICENSE). The detector uses
[Ultralytics YOLO](https://github.com/ultralytics/ultralytics), which is also
AGPL-3.0.
