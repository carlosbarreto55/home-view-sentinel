## Why

Motorcycles and cars sometimes stop in front of the house and stay there, and nobody notices until much later. The DVR records everything, but the cameras are only watched when someone opens HomeView or the DVR's own app, and the DVR's own motion detection fires on every passing car, bird or shadow, so it is useless as an alert. A small always-on service can watch the street-facing cameras, tell a vehicle that is passing from one that has stopped and stayed, and warn the user's iPhone within seconds, with a picture of what is there.

The service is also built so that more devices can react to the same events later (first of all the Samsung TV in the living room), without changing the detection itself.

## What Changes

- New project, `homeview-sentinel`, separate from HomeView (which stays untouched and keeps talking to the DVR directly).
- A stream gateway (go2rtc, in a container) is the only Sentinel component that connects to the DVR. It uses a dedicated restricted DVR user whose credentials stay in the macOS Keychain, checks the login once before starting, and restreams the watched cameras locally.
- A native detector (Python, under launchd) reads the watched cameras from the gateway, decodes them in hardware, corrects 1080N frames to 16:9, and runs a YOLO model through CoreML to find cars, motorcycles and people, tracking each object across frames.
- Each watched camera has one or more zones (polygons) that mark "in front of the house". A vehicle that stays stopped inside a zone longer than a configurable time (default: 30 s for a motorcycle, 2 min for a car) produces a `dwell.started` event with a snapshot; when it leaves, a `dwell.ended` event with the total time. A motorcycle with a person on it is reported as a motorcyclist.
- An MQTT broker (Mosquitto, in a container) carries every event under a versioned JSON contract (a JSON Schema in the repo). Detection and notification never call each other.
- A notifier (in a container) subscribes to events and sends a push to the iPhone through a self-hosted ntfy server, with the snapshot attached, in Portuguese ("Moto parada em frente de casa"), with quiet hours and an alert when the detector stops working.
- Away from home, the iPhone reaches the Mac's ntfy server through Tailscale; nothing is port-forwarded.
- Everything starts at login and restarts by itself after a crash; `make up`, `make down`, `make status` and `make logs` manage it. A replay mode runs the detector over a recorded clip to tune zones and times, and a zone-preview command draws the zones over a live frame.

Out of scope for this change: the Samsung TV app and any TV notification, recordings or a timeline, recognizing specific vehicles or plates, people loitering on foot, cameras other than the configured street-facing ones, a native iPhone app, and running on a machine other than the Mac.

## Capabilities

### New Capabilities
- `stream-gateway`: the single connection to the DVR, its credentials and login check, and the local restreams the other components read.
- `vehicle-detection`: reading and decoding the watched cameras, 16:9 correction, object detection through a swappable inference backend, the motorcyclist association, tracking, and zones.
- `dwell-alerts`: when a tracked vehicle counts as stopped, the dwell thresholds, start and end events, cooldown, snapshots, and replay for calibration.
- `event-bus`: the MQTT broker, topic layout, component status, delivery guarantees, and the versioned event contract.
- `phone-notifications`: the self-hosted ntfy server, the notifier, message content in pt-BR, quiet hours, the detector-offline alert, and reaching the phone at home and away.
- `service-runtime`: configuration, secrets, starting at login and restarting, management commands, logs, and resource use.

### Modified Capabilities
<!-- None: this is the first change in the project. -->

## Impact

- New code: a Python package (`sentinel`) with the detector and the notifier, container definitions and configs for go2rtc, Mosquitto, ntfy and the notifier, launchd agents, a Makefile, and unit tests.
- New dependencies: Apple's `container` tool, FFmpeg (Homebrew), uv, Ultralytics YOLO with coremltools (AGPL-3.0, fine for a project that is never distributed), supervision (tracking and zones), paho-mqtt, the ntfy iOS app, and Tailscale on the Mac and the iPhone.
- DVR: a new restricted user for Sentinel (live view only), created by the user by hand on the DVR, and one or more extra RTSP sessions while Sentinel runs (main streams of the watched cameras). No DVR settings are changed.
- Mac: an always-running workload (target: under a quarter of one CPU core with two cameras), launchd agents in `~/Library/LaunchAgents`, logs in `~/Library/Logs/HomeView Sentinel`, data (snapshots, broker and ntfy state) in `~/Library/Application Support/HomeView Sentinel`.
- External service: ntfy.sh is used only as the iOS wake-up relay for the self-hosted server.
- HomeView: none. It is read for reference and never modified.
