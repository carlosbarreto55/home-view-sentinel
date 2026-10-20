## ADDED Requirements

### Requirement: Control agent
A control agent SHALL run whenever the user is logged in: it SHALL start at login and SHALL be restarted within 10 seconds if it exits for any reason. It SHALL be the only component that starts and stops the other components (container runtime, broker, ntfy, gateway, notifier, detector). It SHALL use on average less than 1% of one CPU core and less than 60 MB of memory.

#### Scenario: Restart after a crash
- **WHEN** the control agent process is killed
- **THEN** it is running again within 10 seconds, with the same mode as before

#### Scenario: Off at login
- **WHEN** the mode is off and the user logs in
- **THEN** only the control agent is running

### Requirement: Watch modes
The control agent SHALL keep one watch mode: **off**, **24/7**, or **timed** with a duration of exactly 1, 2, 4, 8 or 12 hours. Any other duration SHALL be refused. A timed watch SHALL end at the time it was started plus its duration, and starting a new timed watch SHALL replace the end time. The first mode after installation SHALL be off.

#### Scenario: Watching for 4 hours
- **WHEN** the mode is set to timed for 4 hours at 18:00
- **THEN** the components are started, the state shows the mode as timed until 22:00, and at 22:00 the mode becomes off and the components are stopped

#### Scenario: A duration that is not offered
- **WHEN** a request asks for 3 hours
- **THEN** it is refused, the mode does not change, and the state reports the refused request

#### Scenario: Switching to 24/7 during a timed watch
- **WHEN** a timed watch until 22:00 is running and the mode is set to 24/7
- **THEN** the components keep running without a restart and nothing turns them off at 22:00

### Requirement: Modes survive restarts and sleep
The mode and, for a timed watch, its end time (as a wall-clock time) SHALL be saved to disk whenever they change. After the agent starts, the Mac restarts or the Mac wakes from sleep, the agent SHALL apply the saved mode: 24/7 starts the components; a timed watch whose end time is still in the future starts them until that time; a timed watch whose end time has passed SHALL become off.

#### Scenario: Reboot during a timed watch
- **WHEN** a timed watch until 22:00 is running and the Mac restarts and the user logs in at 20:30
- **THEN** within 2 minutes every component is watching again, still until 22:00

#### Scenario: The timer ran out while the Mac slept
- **WHEN** a timed watch until 22:00 is running, the lid is closed at 21:00 and opened at 23:00
- **THEN** the mode is off and no component is started

### Requirement: Off stops everything
When the mode becomes off, the control agent SHALL stop every other component within 10 seconds, leaving no Sentinel process except itself, no Sentinel container and no connection from Sentinel to the DVR. Turning watching on SHALL bring every component online within 2 minutes. Starting or stopping Sentinel SHALL NOT affect HomeView or any other DVR client.

#### Scenario: Turning it off
- **WHEN** the mode is set to off while everything is watching
- **THEN** within 10 seconds no detector, gateway, notifier or Sentinel container is running and the DVR has no connection from Sentinel

#### Scenario: HomeView while Sentinel is off
- **WHEN** the mode is off and the person opens HomeView's panel
- **THEN** HomeView's cameras play exactly as they do without Sentinel installed

### Requirement: Keep the Mac awake on power adapter
While the mode is 24/7 or timed and the Mac is on its power adapter, the control agent SHALL hold a system assertion that prevents idle sleep, so the Mac keeps watching with the screen locked or the display off. On battery the assertion SHALL have no effect and the Mac SHALL sleep according to its own settings. Closing the lid, logging out or shutting down SHALL NOT be prevented. When the mode becomes off the assertion SHALL be released.

#### Scenario: Locked screen on power adapter
- **WHEN** the Mac is plugged in, a timed watch is running, and the person locks the screen (Ctrl+Cmd+Q) and walks away for an hour
- **THEN** the Mac does not sleep and the detector keeps watching for the whole hour

#### Scenario: On battery
- **WHEN** the Mac is on battery, a timed watch is running, and the display turns off
- **THEN** the Mac may sleep as its settings say, and the state reports the power source as battery while watching

#### Scenario: Off releases the assertion
- **WHEN** the mode becomes off
- **THEN** `pmset -g assertions` no longer lists an assertion from Sentinel

### Requirement: State for other apps
The control agent SHALL write its state to `control/state.json` in Sentinel's Application Support folder, following `contracts/control-state.v1.schema.json`. The state SHALL include the schema version, the mode, the end time of a timed watch, the phase (stopped, starting, watching, stopping or problem) with a detail in Portuguese when there is a problem, each watched camera's name, channel and state, the power source (power adapter or battery), whether the Mac is being kept awake, the last applied and last refused request identifiers, the last alert, and the time it was written. The file SHALL be replaced atomically, rewritten at least every 5 seconds, readable only by the user, and SHALL NOT contain credentials.

#### Scenario: Reading the state while watching
- **WHEN** a timed watch until 22:00 is running with both cameras online, on power adapter
- **THEN** `state.json` validates against the schema and says mode timed, until 22:00 local, phase watching, both cameras online, power adapter, kept awake

#### Scenario: A problem
- **WHEN** the DVR rejected Sentinel's login
- **THEN** the state says phase problem with the detail "Usuário ou senha do DVR incorretos"

#### Scenario: The agent stopped
- **WHEN** the control agent has not been running for 30 seconds
- **THEN** the `written_at` time in `state.json` is more than 15 seconds old, which tells readers the state is stale

### Requirement: Requests from other apps
Another app on the Mac SHALL be able to change the mode by writing a request to `control/request.json`, following `contracts/control-request.v1.schema.json`: a request identifier and either off, 24/7, or timed with one of the offered durations. The agent SHALL apply a valid request within 2 seconds and report its identifier as applied in the state; it SHALL report an invalid request as refused, with the reason, and change nothing. Only the user's own processes SHALL be able to write requests (file permissions); no network port SHALL be opened for control.

#### Scenario: HomeView asks for 2 hours
- **WHEN** a request with id `r-7` asks for a timed watch of 2 hours
- **THEN** within 2 seconds the state lists `r-7` as applied and shows the mode as timed for 2 hours from now

#### Scenario: A malformed request
- **WHEN** `request.json` contains invalid JSON
- **THEN** the mode does not change and the state reports the request as refused

### Requirement: Last alert
The control agent SHALL remember the most recent `dwell.started` event while the components run: its title and message as sent to the phone, the time it happened, the camera's identifier, name and channel, the zone's name, and the snapshot's absolute path. It SHALL keep it across its own restarts and while the mode is off, and SHALL include it in the state until a newer alert replaces it or its snapshot is deleted by the snapshot retention.

#### Scenario: The delivery
- **WHEN** a motorcycle stops in front of the garage, its push is sent at 20:15:24, and the mode is later set to off
- **THEN** the state still shows the last alert "Moto parada em frente de casa", "Garagem · Calçada · parada há 30 s · 20:15", at 20:15:24, with the path of its snapshot
