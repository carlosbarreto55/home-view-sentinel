## Why

Sentinel today is either fully on, all day, or fully off through `make up` and `make down` in a Terminal. On the MacBook Air that costs about 35% of one CPU core and 1.3 GB of RAM while it runs (measured on 2026-10-19), and the Mac's own sleep settings put it to sleep a minute after the display turns off, even when plugged in, so "on" does not really mean watching. The user mostly wants Sentinel while waiting for a delivery, sometimes all day, and wants to turn it on and off from HomeView, the menu bar camera app they already open, instead of a Terminal.

## What Changes

- A small **control agent** runs at login and all the time, using next to nothing. It holds the watch mode and is the only thing that starts and stops the other components.
- **Watch modes**: off; watching for 1, 2, 4, 8 or 12 hours, after which it turns off by itself; or 24/7. The mode and the end time survive a reboot or sleep: after waking, a timed watch that is over stays off.
- **Off stops everything** except the control agent: no detector, no gateway, no containers, no connection to the DVR. HomeView, which talks to the DVR directly with its own user, is not affected.
- **Keep the Mac awake only on power adapter** while watching (the system assertion that `caffeinate -s` uses). Locking the screen does not stop watching. On battery the Mac may still sleep as usual, and the state says so, so HomeView can show a hint.
- A **local control contract** for other apps on the Mac: the agent writes its state (mode, end time, what is running, problems, power source, last alert) to a JSON file, and accepts requests to change the mode through another JSON file, both under Application Support, with versioned JSON Schemas in `contracts/`. No new network port.
- The agent **remembers the last alert** (its text, time, camera, zone and snapshot), so it can be shown even after a restart or while Sentinel is off.
- **BREAKING**: only the control agent starts at login. The other components no longer start by themselves at login; they follow the mode. `make up` sets 24/7, `make down` sets off, and a new `make watch HOURS=<1|2|4|8|12>` starts a timed watch. `make status` also shows the mode and the time left.
- Snapshots get the **Mac's local date and time printed on them**, covering the DVR's on-screen clock (about 20 minutes off), so the picture in the push and in HomeView shows the right time.

Out of scope: the HomeView side (a separate change in the HomeView project, `show-sentinel-status`), schedules by time of day or weekday, an event history beyond the last alert, and control from the iPhone.

## Capabilities

### New Capabilities
- `watch-control`: the control agent, the watch modes and durations, starting and stopping the components, keeping the Mac awake on power adapter, the state and request files for other apps, and the last alert.

### Modified Capabilities
- `service-runtime`: only the control agent starts at login; components follow the watch mode; `make up`, `make down`, `make status` change meaning and `make watch` is added.
- `dwell-alerts`: snapshots show the Mac's local date and time.

## Impact

- New code: `sentinel.control` (agent, mode state machine, state and request files, keep-awake), `contracts/control-state.v1.schema.json` and `contracts/control-request.v1.schema.json`, a new broker user `control` (`make secrets`, broker password file).
- Changed code: `sentinel.runtime` (component agents are loaded on demand from Sentinel's own folder instead of `~/Library/LaunchAgents`; `make` targets), `sentinel.detector.snapshots` (time stamp).
- Mac: one launchd agent in `~/Library/LaunchAgents` instead of six; files in `~/Library/Application Support/HomeView Sentinel/control/`.
- Depends on the `stopped-vehicle-alerts` change being archived first, since it modifies that change's `service-runtime` and `dwell-alerts` requirements.
- HomeView: none in this change; the contract is what its `show-sentinel-status` change reads and writes.
