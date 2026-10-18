## ADDED Requirements

### Requirement: Broker
An MQTT broker SHALL run on the Mac, listening on the loopback interface only, with persistence enabled. Every client SHALL authenticate with its own user, and anonymous access SHALL be refused. Broker passwords SHALL come from the Keychain.

#### Scenario: Anonymous client
- **WHEN** a client connects to the broker without credentials
- **THEN** the connection is refused

### Requirement: Topics
Events SHALL be published on `sentinel/v1/events/<camera_id>` with QoS 1 and not retained. Each component (gateway, detector and each of its cameras, notifier) SHALL publish its state on `sentinel/v1/status/<component>` as a retained message with the state `online`, `reconnecting` or `offline`, the time since then and an optional detail, and SHALL register `offline` as its Last Will.

#### Scenario: The detector crashes
- **WHEN** the detector process is killed
- **THEN** the retained message on `sentinel/v1/status/detector` becomes `offline` without the detector publishing anything

#### Scenario: A new subscriber reads the state
- **WHEN** a client subscribes to `sentinel/v1/status/+` while everything is running
- **THEN** it immediately receives the current state of every component

### Requirement: Versioned event contract
Every event SHALL follow the JSON Schema in `contracts/event.v1.schema.json`, which is the single source of truth for producers and consumers. The detector SHALL validate each event against it before publishing and SHALL NOT publish an invalid one. Events SHALL carry `schema: "sentinel.event/v1"`, a unique `event_id`, `type`, `occurred_at` in UTC from the Mac's clock, the camera, the zone, the object and the dwell, and SHALL reference the snapshot by relative file name, never embed it. A breaking change to the contract SHALL use a new version and topic prefix (`v2`) while `v1` is still published.

#### Scenario: An event on the bus
- **WHEN** a `dwell.started` is published
- **THEN** its JSON validates against `contracts/event.v1.schema.json` and contains no credentials and no image data

### Requirement: Decoupled delivery
The detector SHALL NOT know which consumers exist. Adding a consumer SHALL need only a broker user and a subscription, with no change to the detector or the contract. Consumers SHALL use persistent sessions, so events published while a consumer is restarting are delivered to it when it reconnects.

#### Scenario: The notifier restarts during an event
- **WHEN** the notifier is restarted and a `dwell.started` is published while it is down
- **THEN** the notifier receives the event once it reconnects, and the push is sent

#### Scenario: Adding a second consumer
- **WHEN** a command-line MQTT client is given its own broker user and subscribes to `sentinel/v1/events/+`
- **THEN** it receives the same events as the notifier, and the detector's code and configuration are unchanged
