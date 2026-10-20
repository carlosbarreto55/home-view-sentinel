## MODIFIED Requirements

### Requirement: Snapshot
For each `dwell.started`, the detector SHALL save a JPEG of the 1280x720 frame at the moment of the event, with the vehicle's box and the zone drawn, and the event's date and time on the Mac's clock, in the configured time zone, printed in the top right corner over the DVR's own on-screen clock (which runs about 20 minutes off). The file SHALL be saved in the snapshots folder under the camera's identifier, named after the time and the track. The event SHALL reference the file by its path relative to the snapshots folder. Snapshots older than 14 days (configurable) SHALL be deleted automatically.

#### Scenario: Event snapshot
- **WHEN** a `dwell.started` is published for track `garagem-42` at 15:04:37 UTC
- **THEN** the file `garagem/2026-10-18T15-04-37Z-garagem-42.jpg` exists in the snapshots folder and shows the vehicle boxed

#### Scenario: The right time on the picture
- **WHEN** a `dwell.started` happens at 00:15:24 UTC on 2026-10-19 and the time zone is Etc/GMT+4, while the DVR's clock shows 20:35
- **THEN** the snapshot shows "18/10/2026 20:15:24" in its top right corner, and the DVR's 20:35 is no longer readable
