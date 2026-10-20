Each subtask is exactly one commit, using Conventional Commits with the message shown after it. The commit holds the subtask's code, its tests, and this file with the subtask's box ticked. Commits must pass the unit tests (`make test`). A problem found later is fixed in its own `fix(...)` commit, not by amending an earlier one. Starts after `stopped-vehicle-alerts` is archived.

## 1. Contract

- [x] 1.1 `contracts/control-state.v1.schema.json` and `contracts/control-request.v1.schema.json`, Python models that serialize to them, and tests that the design's examples validate and that requests with other durations or bad JSON are rejected — commit: `feat(control): define the control state and request contract`

## 2. Control agent

- [x] 2.1 Watch mode state machine (off, 24/7, timed with 1/2/4/8/12 h) with an injected clock, saved to and loaded from `control/mode.json`; tests for every scenario in the watch-modes and survive-restarts requirements, including a clock jump past the end time — commit: `feat(control): add the watch mode state machine`
- [x] 2.2 Component agents loaded on demand: `make install` writes the component plists to Application Support and only the control agent to `~/Library/LaunchAgents` (removing the old agents); start and stop in order, waiting for `bootout`; tests with a fake launchctl — commit: `feat(runtime): load component agents on demand`
- [x] 2.3 Keep awake on power adapter: a `caffeinate -s` child while watching, released when off; power source from `pmset -g batt`; tests with fake processes — commit: `feat(control): keep the Mac awake on power adapter while watching`
- [x] 2.4 Phase, cameras and last alert from the bus with a new `control` broker user (`make secrets`, broker password file); last alert built with the notifier's messages and kept in `control/last-alert.json`, dropped when its snapshot is gone; tests with scripted statuses and events — commit: `feat(control): follow component status and remember the last alert`
- [x] 2.5 `state.json` written atomically (0600, every change and every 5 s) and `request.json` watched, validated, applied within 2 s and reported as applied or refused; tests with a temporary folder and an injected clock — commit: `feat(control): write the state and accept requests from other apps`
- [ ] 2.6 Entry point `python -m sentinel.control` and its launchd agent (`RunAtLoad`, `KeepAlive`), first mode off — commit: `feat(control): run the control agent at login`

## 3. Commands

- [ ] 3.1 `make up`, `make watch HOURS=<n>` and `make down` as requests through the agent (wait up to 15 s, print the result); `make status` shows the mode and time left — commit: `feat(runtime): control watching from make through the agent`

## 4. Snapshot time

- [ ] 4.1 Print the event's local date and time on snapshots, top right over the DVR's clock (box size checked on both street cameras' frames); test that the text and box are drawn — commit: `feat(dwell): print the Mac's local time on snapshots`

## 5. Verification

- [ ] 5.1 Manual checks on the Mac, results in `docs/verificacao-watch-schedule.md`: a 1 h watch ends by itself; locked screen on power adapter keeps watching for an hour; on battery the state says battery; reboot during a timed watch resumes; timer expiring during sleep stays off; off leaves only the control agent and no DVR session within 10 s while HomeView plays normally; `kill -9` of the agent releases the awake assertion and the agent comes back with the same mode; the snapshot shows the right time — commit: `test: verify the watch schedule on the Mac`
