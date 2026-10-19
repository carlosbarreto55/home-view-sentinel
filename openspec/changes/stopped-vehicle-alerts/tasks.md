Each subtask is exactly one commit, using Conventional Commits with the message shown after it. The commit holds the subtask's code, its tests, and this file with the subtask's box ticked. Commits must pass the unit tests (`make test`, from 1.1 on). A problem found later is fixed in its own `fix(...)` commit, not by amending an earlier one. `../home-view` is read for reference only and is never modified.

## 1. Project setup

- [x] 1.1 Python 3.12 project with uv (`pyproject.toml`, package `sentinel` under `src/`, pytest and ruff configured), `.gitignore` (`.venv`, `config/sentinel.yaml`, `clips/`, `models/*.mlpackage`, `docs/zone-preview-*.png`, `.DS_Store`), a `Makefile` with `setup` and `test`, and one passing test — commit: `build: add Python project, Makefile and gitignore`
- [x] 1.2 Configuration: `config/sentinel.example.yaml` (cameras, zones, thresholds, sampling, model, quiet hours, ntfy topic) and `sentinel.config` loading and validating it, with errors naming the field (e.g. `cameras[garagem].zones[calcada].polygon`); tests for a valid file and each kind of error — commit: `feat(config): load and validate sentinel.yaml`
- [x] 1.3 Keychain helper over the `security` CLI (set, get, delete generic passwords under `com.carloseduardo.homeview-sentinel`), and `make secrets` asking for the DVR user and password and generating the broker and ntfy credentials; tests with a test-only service name, cleaned up after each test — commit: `feat(secrets): store service credentials in the Keychain`

## 2. Spikes on the real Mac, DVR and iPhone

Findings from each spike subtask are written to `docs/spike-notes.md` in the same commit. The user creates the `sentinel` DVR user (live view only) by hand before 2.2.

- [x] 2.1 Apple `container`: run a go2rtc container in the foreground under a test launchd agent; check that it reaches 192.0.2.10:554, that a port published on 127.0.0.1 works from macOS and is refused from the LAN, that launchd restarts it after `kill`, how two containers reach each other, and how long a cold start takes — commit: `docs(spike): validate Apple container under launchd`
- [x] 2.2 go2rtc against the DVR with the `sentinel` user: restream channel 1's main stream, read it with two consumers and confirm one DVR session (`lsof`/`netstat`); then open HomeView's grid (8 substreams) at the same time and record whether the DVR serves everything — commit: `docs(spike): restream DVR cameras through go2rtc`
- [x] 2.3 FFmpeg with VideoToolbox: decode channel 1's main stream at `fps=5,scale=1280:720` to a raw pipe; record CPU and memory, time to first frame, and confirm the 16:9 picture — commit: `docs(spike): decode main stream with VideoToolbox`
- [x] 2.4 YOLO on CoreML: export `yolo11n` and `yolo11s` with NMS; run both on frames saved by day and at night from the street cameras (main stream at 1280x720 and the substream); record latency per frame, and whether cars, motorcycles and riders at the gate and across the street are found; choose the model and the stream — commit: `docs(spike): choose YOLO model and stream for detection`
- [x] 2.5 ntfy on the iPhone: run ntfy in a container with `upstream-base-url`, `deny-all` and an attachment cache; expose it with Tailscale Serve; subscribe from the ntfy iOS app; check instant delivery with the phone locked, at home and on mobile data, and that an attached JPEG shows — commit: `docs(spike): deliver ntfy pushes to the iPhone over Tailscale`
- [x] 2.6 Write the spike findings into design.md (Risks, decisions 2, 4, 6 and 11, Open Questions); if Apple `container` failed 2.1, the switch to Colima for the same images is recorded there — commit: `docs(openspec): record spike findings in design`

## 3. Stream gateway

- [x] 3.1 `rtsp_probe`: one `DESCRIBE` for the configured camera, Digest answer (MD5, no `qop` and `qop=auth`), Basic fallback, 5 s timeout, results accepted / wrong credentials / not found; a fake RTSP server fixture for tests (challenge, accept, reject, silent) — commit: `feat(gateway): probe DVR credentials over RTSP`
- [x] 3.2 go2rtc configuration rendered from `sentinel.yaml` (only the configured cameras, `${DVR_USER}`/`${DVR_PASSWORD}` placeholders with percent-encoding at start, RTSP on loopback, API and WebRTC off or loopback); test that the rendered file contains no secret — commit: `feat(gateway): render go2rtc configuration from cameras`
- [ ] 3.3 Gateway container (`containers/go2rtc/`) and launcher: read the Keychain, run the probe, start go2rtc in the foreground; on wrong credentials publish/log "Usuário ou senha do DVR incorretos", write the stop marker and exit so launchd does not restart it; tests for the launcher's decisions with a fake probe — commit: `feat(gateway): start go2rtc after checking the DVR login`

## 4. Event bus

- [ ] 4.1 Mosquitto container (`containers/mosquitto/`): loopback-only listener, persistence in Application Support, password file rendered at start from the Keychain, anonymous refused — commit: `feat(bus): add authenticated Mosquitto broker container`
- [x] 4.2 Event contract: `contracts/event.v1.schema.json` for `dwell.started` and `dwell.ended`, Python models that serialize to it, and tests that every example in design.md validates and that invalid events are rejected — commit: `feat(bus): define versioned event contract`
- [x] 4.3 MQTT client wrapper (paho-mqtt): authenticated connect with retry, retained status with Last Will, QoS 1 publish that validates against the contract, persistent-session subscriptions; integration test against the broker container, skipped when it is not running — commit: `feat(bus): add MQTT client with status and last will`

## 5. Detection

- [x] 5.1 Frame reader: FFmpeg subprocess per camera (`-hwaccel videotoolbox`, `fps`, `scale=1280:720`, raw BGR pipe), exact frame framing, 10 s stall detection, backoff 5 s doubling to 5 min, camera status published; tests with a fake process — commit: `feat(detector): read camera frames from the gateway`
- [x] 5.2 `InferenceBackend` interface, a fake backend with scripted detections, and the CoreML backend for the model chosen in 2.4 (class filter car/truck/bus → car, motorcycle, person; minimum confidence); `make setup` exports the model and records its version in `models/README.md` — commit: `feat(detector): add inference backends`
- [x] 5.3 Rider association (person box lower half over a motorcycle box, ≥30% of the person's area; "with rider" by majority of the last 10 frames); tests for the spec's scenarios — commit: `feat(detector): attach riders to motorcycles`
- [x] 5.4 Tracking (supervision ByteTrack per camera, 3 s lost buffer) and zones (normalized polygons, bottom-center anchor); tests for zone geometry and for keeping an identifier through a 2 s gap — commit: `feat(detector): track vehicles and place them in zones`
- [x] 5.5 `make zone-preview CAMERA=<id>`: grab a frame from the gateway, draw zones and current detections, save `docs/zone-preview-<id>.png` — commit: `feat(tools): preview camera zones on a live frame`

## 6. Dwell alerts

- [x] 6.1 Dwell state machine (moving, stopped, alerted, ended) with injected clock: stillness window, movement grace, per-class thresholds with per-camera overrides, end grace; a test for every scenario of the dwell-alerts spec — commit: `feat(dwell): add dwell state machine`
- [x] 6.2 Cooldown per camera, zone and class, and re-identification of a still track near a recently lost alerted one; tests for the night-headlights and one-leaves-another-arrives scenarios — commit: `feat(dwell): avoid duplicate alerts for the same vehicle`
- [x] 6.3 Snapshot writer (JPEG with the box and the zone, path `<camera>/<UTC time>-<track>.jpg`) and daily deletion of snapshots older than the retention; tests for naming and retention — commit: `feat(dwell): save event snapshots`
- [x] 6.4 Detector pipeline and entry point: config, readers, backend, association, tracking, zones, dwell, snapshots and event publishing for every camera, with the detector's status and Last Will — commit: `feat(detector): run the detection pipeline for all cameras`
- [x] 6.5 `make record` (clip from the gateway) and `make replay` (pipeline over a clip with the clip's clock, events printed, nothing published); test that replaying scripted detections prints the expected events — commit: `feat(tools): record clips and replay them through the pipeline`
- [ ] 6.6 Calibration with the user: record day and night clips of the street cameras, set the cameras and zones in `sentinel.yaml`, replay and adjust thresholds until passing traffic gives no event and a test stop gives one; write the chosen values and the results to `docs/calibracao.md` — commit: `docs(dwell): record calibration of zones and thresholds`

## 7. Phone notifications

- [ ] 7.1 ntfy server container (`containers/ntfy/`): `upstream-base-url`, `base-url` at the Tailscale name, `deny-all`, phone user with read access and notifier token with write access created from the Keychain at start, attachment cache in Application Support — commit: `feat(notify): add self-hosted ntfy server container`
- [x] 7.2 Message rules and pt-BR text (titles by class and rider, body with camera, zone, duration with gender agreement and local time, priorities, end messages off by default, quiet hours lowering priority); tests for every case in the phone-notifications spec — commit: `feat(notify): build alert messages in Portuguese`
- [x] 7.3 ntfy client (PUT with snapshot body and headers, token auth, 3 retries over 1 minute); tests against a fake HTTP server — commit: `feat(notify): send pushes with snapshots through ntfy`
- [ ] 7.4 Notifier service and container: persistent subscription to events and status, offline alert after 2 minutes and back-online message, camera without picture for 10 minutes, its own status and Last Will; tests for the offline timing rules with an injected clock — commit: `feat(notify): add notifier service`

## 8. Runtime

- [ ] 8.1 launchd agent templates for the container runtime, gateway, broker, ntfy, notifier and detector (`KeepAlive`, log paths, environment from the Keychain through each launcher, gateway stop marker respected), and `make install` rendering and loading them — commit: `feat(runtime): run every component under launchd`
- [ ] 8.2 `make up`, `make down`, `make status` (retained statuses, launchd state, uptime, CPU) and `make logs`; daily log rotation with 7 days kept — commit: `feat(runtime): add management commands and log rotation`
- [ ] 8.3 Measure one hour with two cameras (CPU, memory) and record it in `docs/spike-notes.md`; if it is over budget, lower the sampling rate or model size in the example config — commit: `docs(runtime): measure resource use with two cameras`

## 9. Verification and docs

- [ ] 9.1 Manual checklist `docs/verificacao-manual.md` covering every scenario that needs the real world (a car parked 3 min, a motorcyclist waiting 45 s, traffic passing, night, iPhone locked at home and on mobile data, `make down` leaving no DVR session, Mac restart recovery, killing the detector, wrong DVR password causing one failed login) and running it, results in the same file — commit: `test: run manual verification checklist`
- [ ] 9.2 README in the style of HomeView's: what it does, the accepted limitation (Mac asleep or away means no watching), requirements, creating the restricted `sentinel` DVR user, `make` commands, iPhone setup (Tailscale, ntfy app, subscription), privacy and security, how a future consumer (the TV) subscribes, uninstalling — commit: `docs: add README`
