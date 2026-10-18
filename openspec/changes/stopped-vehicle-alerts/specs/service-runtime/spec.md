## ADDED Requirements

### Requirement: Starts at login and restarts by itself
Every component (container runtime, gateway, broker, ntfy, notifier, detector) SHALL run under a launchd agent that starts it when the user logs in and restarts it within 10 seconds when it exits, except the gateway after a rejected DVR login. Components SHALL start in a working order on their own: a component whose dependency is not ready yet SHALL wait or retry instead of failing permanently.

#### Scenario: Mac restart
- **WHEN** the Mac restarts and the user logs in
- **THEN** within 2 minutes every component is `online` without any command

#### Scenario: A component crashes
- **WHEN** the notifier process is killed
- **THEN** launchd starts it again within 10 seconds

### Requirement: Management commands
The Makefile SHALL provide `make setup` (dependencies, model export), `make secrets` (ask for and store the DVR credentials, generate the broker and ntfy credentials), `make install` (install the launchd agents), `make up`, `make down`, `make status` (each component's state, uptime and CPU use) and `make logs`, plus `make test`, `make replay`, `make record` and `make zone-preview`. `make down` SHALL stop every component, and leave no connection to the DVR, within 10 seconds.

#### Scenario: Stopping everything
- **WHEN** the user runs `make down`
- **THEN** within 10 seconds no Sentinel process or container is running and the DVR has no connection from Sentinel

#### Scenario: Checking state
- **WHEN** the user runs `make status` while everything runs
- **THEN** it lists the gateway, broker, ntfy, notifier, detector and each watched camera with its state

### Requirement: Configuration
The service SHALL read `config/sentinel.yaml` (cameras, zones, thresholds, sampling rate, model, quiet hours, ntfy topic), which is not committed; `config/sentinel.example.yaml` SHALL be committed as a starting point. Invalid configuration SHALL stop the component at start with a message naming the field and the problem.

#### Scenario: A zone with two points
- **WHEN** a zone's polygon has only two points
- **THEN** the detector refuses to start with an error naming `cameras[garagem].zones[calcada].polygon`

### Requirement: Secrets in the Keychain
The DVR user and password, broker passwords and the ntfy credentials SHALL be stored only in the login Keychain, under the service `com.carloseduardo.homeview-sentinel`, and SHALL be given to each process only as environment variables at start. They SHALL NOT be written to the repository, configuration files, container images or logs.

#### Scenario: Changing the DVR password
- **WHEN** the user changes the Sentinel user's password on the DVR, runs `make secrets` with the new one and `make up`
- **THEN** the gateway connects with the new password, and the old one is no longer in the Keychain

### Requirement: Logs and data
Each component SHALL log to its own file in `~/Library/Logs/HomeView Sentinel/`, rotated daily with 7 days kept, without credentials. Snapshots, broker persistence and ntfy data SHALL live in `~/Library/Application Support/HomeView Sentinel/`.

#### Scenario: Log retention
- **WHEN** the service has run for 10 days
- **THEN** each component has at most 7 days of logs

### Requirement: Resource use
With two watched cameras at 5 frames per second, the whole service SHALL use on average less than 25% of one CPU core and less than 1.5 GB of memory on the user's Mac.

#### Scenario: One hour of watching
- **WHEN** two cameras are watched for one hour during the day
- **THEN** the average CPU use measured over that hour is below 25% of one core and memory stays below 1.5 GB
