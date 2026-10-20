## MODIFIED Requirements

### Requirement: Starts at login and restarts by itself
Only the control agent SHALL start when the user logs in. Every other component (container runtime, gateway, broker, ntfy, notifier, detector) SHALL run under a launchd agent that the control agent loads when the watch mode is 24/7 or a timed watch is running, and unloads when the mode becomes off. While loaded, a component SHALL be restarted within 10 seconds when it exits, except the gateway after a rejected DVR login. Components SHALL start in a working order on their own: a component whose dependency is not ready yet SHALL wait or retry instead of failing permanently.

#### Scenario: Mac restart while watching 24/7
- **WHEN** the mode is 24/7, the Mac restarts and the user logs in
- **THEN** within 2 minutes every component is `online` without any command

#### Scenario: Mac restart while off
- **WHEN** the mode is off, the Mac restarts and the user logs in
- **THEN** only the control agent runs

#### Scenario: A component crashes
- **WHEN** the notifier process is killed while watching
- **THEN** launchd starts it again within 10 seconds

### Requirement: Management commands
The Makefile SHALL provide `make setup` (dependencies, model export), `make secrets` (ask for and store the DVR credentials, generate the broker and ntfy credentials), `make install` (install the control agent and the component agents), `make up` (watch 24/7), `make watch HOURS=<1|2|4|8|12>` (watch for that many hours), `make down` (stop watching), `make status` (the watch mode and time left, and each component's state, uptime and CPU use) and `make logs`, plus `make test`, `make replay`, `make record` and `make zone-preview`. `make up`, `make watch` and `make down` SHALL go through the control agent, so that they and other apps always agree on the mode. `make down` SHALL stop every component except the control agent, and leave no connection to the DVR, within 10 seconds.

#### Scenario: Stopping everything
- **WHEN** the user runs `make down`
- **THEN** within 10 seconds no Sentinel process except the control agent and no Sentinel container is running, and the DVR has no connection from Sentinel

#### Scenario: Watching from the Terminal
- **WHEN** the user runs `make watch HOURS=2` at 18:00
- **THEN** the components start and `make status` shows the mode as watching until 20:00

#### Scenario: Checking state
- **WHEN** the user runs `make status` while everything runs
- **THEN** it shows the watch mode and lists the gateway, broker, ntfy, notifier, detector and each watched camera with its state
