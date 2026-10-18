## ADDED Requirements

### Requirement: Reading the watched cameras
The detector SHALL read each camera listed in `sentinel.yaml` from the gateway, decode it with hardware acceleration, and pass frames to detection at the configured sampling rate (default 5 frames per second). Every frame SHALL be resized to 1280x720, so 960x1080 (1080N) channels appear at their intended 16:9 shape.

#### Scenario: A 1080N channel
- **WHEN** the detector reads channel 1, whose main stream is 960x1080
- **THEN** detection receives 1280x720 frames in which a round object looks round, about 5 times per second

#### Scenario: The 16:9 channel
- **WHEN** the detector reads channel 8, whose main stream is 1280x720
- **THEN** detection receives its frames at 1280x720, unchanged in shape

### Requirement: Stream loss and recovery
When a camera's stream stops or no frame arrives for 10 seconds, the detector SHALL publish that camera as reconnecting, discard its current tracks, and try again after 5 seconds, doubling the wait up to 5 minutes, back to 5 seconds after a frame arrives. A failing camera SHALL NOT affect the others.

#### Scenario: The gateway restarts
- **WHEN** the gateway is restarted while the detector is reading two cameras
- **THEN** both cameras are published as reconnecting, and both are read again within 15 seconds of the gateway being back

#### Scenario: One camera unplugged
- **WHEN** the camera on channel 3 loses power
- **THEN** channel 3 is published as reconnecting and retried on the backoff schedule, while channel 1 keeps being detected without interruption

### Requirement: Detected objects
The detector SHALL detect cars (including trucks and buses), motorcycles and people, and SHALL ignore every other class. Detections below the configured minimum confidence (default 0.4) SHALL be ignored.

#### Scenario: A bicycle and a dog
- **WHEN** a bicycle and a dog stop inside a zone
- **THEN** no object is tracked and no event is produced for them

### Requirement: Motorcyclist
A person whose box's lower half overlaps a motorcycle's box by at least 30% of the person box's area SHALL be attached to that motorcycle as its rider and SHALL NOT be tracked separately. A tracked motorcycle SHALL be reported as with a rider when a rider was attached in at least half of its last 10 frames.

#### Scenario: Rider waiting on a stopped motorcycle
- **WHEN** a motorcycle stops with a person sitting on it
- **THEN** one object is tracked, a motorcycle with a rider, not a motorcycle plus a person

#### Scenario: Rider gets off
- **WHEN** the rider gets off and walks away, leaving the motorcycle parked
- **THEN** after at most 10 frames the motorcycle is reported without a rider

### Requirement: Tracking
Each detected vehicle SHALL get a track identifier that stays the same across frames while it remains visible, including through gaps of up to 3 seconds without a detection (a pedestrian passing in front, a missed frame).

#### Scenario: Brief occlusion
- **WHEN** a person walks in front of a parked car, hiding it for 2 seconds
- **THEN** the car keeps the same track identifier

### Requirement: Zones
Each watched camera SHALL have one or more named zones, given as polygons in normalized coordinates (0 to 1) of the 16:9 frame. An object SHALL count as inside a zone when the bottom center of its box is inside the polygon. Objects outside every zone SHALL be ignored by the dwell rules. `make zone-preview CAMERA=<id>` SHALL save the camera's current frame with its zones and live detections drawn, so the polygons can be adjusted.

#### Scenario: Traffic on the far lane
- **WHEN** a car drives along the far side of the street, outside every zone
- **THEN** it produces no dwell event, whatever it does

#### Scenario: Previewing zones
- **WHEN** the user runs `make zone-preview CAMERA=garagem`
- **THEN** an image of the current frame is saved with the "Calçada" polygon and the current detections drawn on it

### Requirement: Swappable inference backend
The detector SHALL use the model only through an inference-backend interface that takes a frame and returns detections (class, confidence, box). This change SHALL provide a CoreML backend and a fake backend for tests; another backend (such as OpenVINO) SHALL be addable without changing tracking, zones, dwell rules or events.

#### Scenario: Running the pipeline in tests
- **WHEN** the unit tests run the detection pipeline with the fake backend and scripted detections
- **THEN** they get the same tracks and events the CoreML backend would produce for those detections, without any model file
