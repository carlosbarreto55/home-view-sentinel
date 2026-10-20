## Context

After `stopped-vehicle-alerts`, every component runs under its own launchd agent in `~/Library/LaunchAgents`, starts at login and stays up until `make down`. Measured on the user's MacBook Air (M5, 16 GB) on 2026-10-19, watching two cameras costs about 35% of one core (detector 27%, of which two thirds is HEVC decoding; container VMs 5%) and 1.3 GB of memory (the three container VMs hold about 300 MB each).

The Mac's power settings are `displaysleep 60` and `sleep 1` on both battery and power adapter: the Mac sleeps a minute after the display turns off, which also happens soon after the screen is locked. A running process does not keep a Mac awake, so today Sentinel stops watching about an hour after the user stops using the Mac, plugged in or not.

The user wants to watch mostly while waiting for a delivery, sometimes 24/7, and to control it from HomeView, their SwiftUI menu bar app that plays the DVR's cameras directly (its own DVR user, its own RTSP sessions, not through Sentinel's gateway). HomeView is not sandboxed and only works while its panel is open. Its side is the `show-sentinel-status` change in the HomeView project; this change provides what it needs.

## Goals / Non-Goals

**Goals:**
- Watch for 1, 2, 4, 8 or 12 hours, or 24/7, and turn off by itself at the end of a timed watch, even across sleep and reboot.
- Off really means off: no CPU, no container VMs, no DVR session; HomeView unaffected.
- Keep the Mac awake while watching on power adapter; leave battery behaviour to macOS, and say so.
- One source of truth for the mode, used by HomeView, `make` and anything later.
- A control contract that needs no network port and no new secret.
- The right time on snapshots.

**Non-Goals:**
- The HomeView interface (separate change).
- Schedules by time of day or weekday, an alert history, control from the iPhone.
- Preventing sleep on battery, or with the lid closed.
- Cutting the detector's CPU use (task 8.3 of `stopped-vehicle-alerts`).

## Decisions

### 1. A control agent owns the mode
A small native Python process, `python -m sentinel.control`, runs under the only launchd agent installed in `~/Library/LaunchAgents` (`com.carloseduardo.homeview-sentinel.control`, `RunAtLoad`, `KeepAlive` true). It holds the mode, starts and stops the components, keeps the Mac awake, remembers the last alert and writes the state.

The timer cannot live in HomeView: HomeView may be closed or quit, and by design runs nothing while its panel is closed. It cannot live in the broker either: the broker is one of the things that is stopped when the mode is off.

*Alternatives:* HomeView running `launchctl` and `caffeinate` itself (the timer and the awake assertion die with HomeView; two apps would disagree with `make`); keeping the broker always on and controlling over MQTT (300 MB of RAM all the time, only to receive a "start").

### 2. Component agents are loaded on demand
`make install` writes the component plists (container runtime, broker, ntfy, gateway, notifier, detector) to `~/Library/Application Support/HomeView Sentinel/launchd/` instead of `~/Library/LaunchAgents`, so launchd never loads them at login by itself. The control agent loads them in order with `launchctl bootstrap gui/<uid> <plist>` when watching starts, and unloads them in reverse with `launchctl bootout` (waiting until each is gone, as `make down` already does) when it stops. While loaded they keep `KeepAlive` with `SuccessfulExit = false`, so crashes are still restarted by launchd and the gateway's stop marker still works.

Switching between 24/7 and timed does not reload anything; only the end time changes.

### 3. Mode state machine, pure and tested
```
           watch(h ∈ {1,2,4,8,12})          now ≥ until
   OFF ─────────────────────────▶ TIMED(until) ────────────▶ OFF
    ▲  ╲                            │  ▲ watch(h): until = now + h
    │   ╲ always                    │  │
    │    ▼                          ▼  │
    └──── ALWAYS ◀──── always ──────┘──┘
     off                        (off from any state → OFF)
```
The machine takes requests and the wall-clock time from an injected clock and returns the desired mode and whether components should run; it does no I/O. The mode is saved to `control/mode.json` (`{"mode": "timed", "until": "2026-10-19T02:00:00Z"}`) on every change and read at start. The agent ticks every second; a wake from sleep is just a tick where `now` jumped, so an expired timer turns off then.

The components' phase is derived from what the agent asked for and what it sees: `starting` from the request until the detector and both cameras are online (or 2 minutes), `watching`, `stopping` until `bootout` finished, `stopped`, and `problem` when a component reports a problem (the gateway's "Usuário ou senha do DVR incorretos", a camera reconnecting, a component not online after 2 minutes).

### 4. Keep awake: `caffeinate -s` as a child process
While the mode is not off, the agent keeps a child `caffeinate -s` running and kills it when the mode becomes off. `-s` creates the "prevent system sleep" assertion, which macOS honours only on power adapter: exactly the requested behaviour, with no battery logic of our own. The assertion is tied to the child's life, so if the agent crashes, launchd's cleanup of the process group releases it. Locking the screen does not end the user's session, so launchd agents keep running while locked.

The power source for the state (and HomeView's hint) comes from `pmset -g batt` ("AC Power" or "Battery Power"), polled every 30 seconds.

*Alternatives:* calling `IOPMAssertionCreateWithName` through PyObjC (a new dependency for the same result); `caffeinate -i` (also prevents sleep on battery, which the user does not want).

### 5. The control contract: two JSON files, no port
Files under `~/Library/Application Support/HomeView Sentinel/control/`, folder mode 0700:

- `state.json`, written by the agent (temporary file and rename, mode 0600) on every change and at least every 5 seconds:
```json
{
  "schema": "sentinel.control-state/v1",
  "written_at": "2026-10-19T22:31:05Z",
  "mode": "timed",
  "until": "2026-10-20T02:00:00Z",
  "phase": "watching",
  "detail": null,
  "power": "battery",
  "kept_awake": false,
  "cameras": [
    {"id": "garagem", "name": "Garagem", "channel": 1, "state": "online"},
    {"id": "rua", "name": "Rua", "channel": 2, "state": "online"}
  ],
  "durations_hours": [1, 2, 4, 8, 12],
  "applied_request": "6f1c…",
  "refused_request": null,
  "last_alert": {
    "event_id": "8b84df78-…",
    "title": "Moto parada em frente de casa",
    "message": "Garagem · Calçada · parada há 30 s · 20:15",
    "occurred_at": "2026-10-19T00:15:24Z",
    "camera": {"id": "garagem", "name": "Garagem", "channel": 1},
    "zone": "Calçada",
    "snapshot": "/Users/…/HomeView Sentinel/snapshots/garagem/2026-10-19T00-15-24Z-garagem-1.jpg"
  }
}
```
- `request.json`, written by a client (temporary file and rename): `{"schema": "sentinel.control-request/v1", "id": "<uuid>", "mode": "off" | "always" | "timed", "hours": 2}`. The agent watches the folder (polling once a second is enough), validates the request against its schema, applies it, deletes the file, and reports its id in `applied_request` or `refused_request` (with `detail`).

Both schemas live in `contracts/` next to the event schema and are the source of truth for HomeView. A breaking change goes to `v2`. `durations_hours` lets clients offer exactly what the agent accepts. `written_at` older than 15 seconds means the agent is not running. Times are UTC; clients format them in local time.

*Alternatives:* a localhost HTTP API (any local process, including other users', can reach 127.0.0.1, so it needs a token shared through the Keychain, which prompts the user when another app reads it); a Unix socket (needs a custom client in Swift for little gain); MQTT (down when off, see decision 1).

### 6. Status and last alert from the bus
While the components run, the agent connects to the broker with its own user, `control` (added to `make secrets` and to the broker's password file), subscribes to `sentinel/v1/status/+` and `sentinel/v1/events/+` with a persistent session, and uses them for the phase, the cameras and the last alert. The last alert is built with the notifier's own message functions, so HomeView shows exactly what the phone showed, and is saved in `control/last-alert.json` so it survives restarts and the off mode. It is dropped when its snapshot file no longer exists (14-day retention).

### 7. `make` goes through the agent
`make up`, `make watch HOURS=n` and `make down` write a request and wait for it to be applied (or refused) for up to 15 seconds, then print the state. `make status` prints the mode and time left before the per-component table. `make install` installs the control agent and the component plists, and keeps the current mode (off on first install).

### 8. Time on the snapshot
`snapshots.draw` prints the event time, in `notifications.timezone`, as `dd/mm/yyyy hh:mm:ss`, white on a dark box in the top right corner, large enough to cover the DVR's clock on both street cameras (measured on their 1280x720 frames). The box position is fixed for now; if another camera's clock sits elsewhere, a per-camera setting can be added.

## Risks / Trade-offs

- [Breaking: after this change nothing watches at login unless the mode says so] → The first install sets off; the README and `make install` say how to turn watching on. The current 24/7 setup becomes off after the upgrade until the user chooses.
- [Starting takes 30–40 seconds (three VMs, the model, the first frames)] → The phase `starting` tells HomeView to show "Iniciando…".
- [On battery the Mac sleeps soon after the display turns off, and watching stops] → Accepted by the user; the state reports `power: battery` so HomeView shows the hint; on wake a running timer continues if it has not expired.
- [`caffeinate -s` could be left behind if the agent is killed hard] → It runs as the agent's child in the same launchd job, which launchd kills with the job; the manual checks verify `pmset -g assertions` after `kill -9`.
- [Two writers of the mode (HomeView and `make`)] → Only the agent writes the mode; everyone else sends requests. The last request applied wins.
- [The file contract couples HomeView to Sentinel's folder layout] → The folder and the schemas are part of the contract and versioned; HomeView hides its Sentinel strip when the folder or a supported schema is missing.
- [The time box hides part of the picture] → It covers only the DVR's clock area, top right, where the street is not.

## Migration Plan

1. Archive `stopped-vehicle-alerts` first (this change modifies its `service-runtime` and `dwell-alerts` requirements).
2. `make secrets` adds the `control` broker password; `make install` removes the six old agents from `~/Library/LaunchAgents`, installs the control agent, writes the component plists to Application Support, and starts with mode off.
3. Rollback: `make uninstall`, then check out the previous commit and `make install`.

## Open Questions

- None blocking. Stopping on purpose sends no "O Sentinel parou de vigiar" push: components stop in reverse order, the detector first and the notifier a few seconds later, well inside the notifier's 2-minute offline rule. The manual checks confirm it.
