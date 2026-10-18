## ADDED Requirements

### Requirement: When a vehicle counts as stopped
A tracked vehicle inside a zone SHALL count as stopped when the center of its box has moved less than 2% of the frame diagonal over the last 5 seconds. Movement or a missing detection lasting less than 3 seconds (the movement grace) SHALL NOT reset the time it has been stopped. Both values SHALL be configurable.

#### Scenario: A car slowing down to pass
- **WHEN** a car slows down inside the zone and drives on after 2 seconds
- **THEN** it never counts as stopped

#### Scenario: A rider adjusting the motorcycle
- **WHEN** a stopped motorcycle moves forward a little for 1 second and stops again
- **THEN** the time it has been stopped keeps counting from when it first stopped

### Requirement: Dwell threshold and start event
When a vehicle has been stopped in a zone for at least its threshold (default 30 seconds for a motorcycle, 120 seconds for a car; overridable per camera), the detector SHALL publish one `dwell.started` event for it, with its class, whether a motorcycle has a rider, the camera, the zone, the time it stopped, the seconds stopped, and a snapshot. Each vehicle SHALL produce at most one `dwell.started` while it stays.

#### Scenario: A car parks for three minutes
- **WHEN** a car parks inside the "Calçada" zone and stays for 3 minutes
- **THEN** exactly one `dwell.started` is published, about 120 seconds after it stopped

#### Scenario: A car stops briefly
- **WHEN** a car stops inside the zone for 60 seconds and leaves
- **THEN** no event is published

#### Scenario: A motorcyclist waits
- **WHEN** a motorcycle with a rider stops inside the zone and waits for 45 seconds
- **THEN** one `dwell.started` is published about 30 seconds after it stopped, with `with_rider` true

### Requirement: End event
When a vehicle that produced a `dwell.started` leaves the zone, or has not been detected for 10 seconds (the end grace), the detector SHALL publish one `dwell.ended` event with the total seconds it stayed and the identifier of its start event. Vehicles that never reached their threshold SHALL NOT produce `dwell.ended`.

#### Scenario: The parked car leaves
- **WHEN** a car that triggered `dwell.started` drives away 7 minutes after stopping
- **THEN** one `dwell.ended` is published within 10 seconds, with about 420 seconds stopped and the start event's identifier

### Requirement: No duplicate alerts for the same vehicle
After a `dwell.started` for a class in a zone, no other `dwell.started` for the same class in the same zone SHALL be published for 10 minutes (the cooldown) unless the first vehicle has ended. A new track that appears stopped within 5% of the frame diagonal of an alerted vehicle that was lost less than 2 minutes before, with the same class and zone, SHALL be treated as that same vehicle.

#### Scenario: The tracker loses a parked car at night
- **WHEN** a parked car that already triggered `dwell.started` is lost by the tracker for 40 seconds because of headlights and found again under a new track identifier at the same place
- **THEN** no new `dwell.started` is published, and its stopped time keeps counting from the original stop

#### Scenario: One car leaves and another arrives
- **WHEN** an alerted car leaves (its `dwell.ended` is published), and 3 minutes later another car parks in the same zone for 2 minutes
- **THEN** a new `dwell.started` is published for the second car

### Requirement: Snapshot
For each `dwell.started`, the detector SHALL save a JPEG of the 1280x720 frame at the moment of the event, with the vehicle's box and the zone drawn, in the snapshots folder under the camera's identifier, named after the time and the track. The event SHALL reference the file by its path relative to the snapshots folder. Snapshots older than 14 days (configurable) SHALL be deleted automatically.

#### Scenario: Event snapshot
- **WHEN** a `dwell.started` is published for track `garagem-42` at 15:04:37 UTC
- **THEN** the file `garagem/2026-10-18T15-04-37Z-garagem-42.jpg` exists in the snapshots folder and shows the vehicle boxed

### Requirement: Replay for calibration
`make replay CLIP=<file> CAMERA=<id>` SHALL run the same detection and dwell pipeline over a recorded clip, using the clip's own timestamps as the clock, and SHALL print the events it would publish instead of publishing them. `make record CAMERA=<id> MINUTES=<n>` SHALL save a clip of that camera from the gateway.

#### Scenario: Tuning on a night clip
- **WHEN** the user records 10 minutes of the street at night and replays it
- **THEN** the events are printed with their times in the clip, without any push being sent
