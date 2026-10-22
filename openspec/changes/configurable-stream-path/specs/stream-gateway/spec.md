## ADDED Requirements

### Requirement: Configurable stream path
The gateway and the login check SHALL build each camera's RTSP address from the DVR host, port and the path template in `dvr.stream_path`. The template SHALL accept the placeholders `{channel}` (the camera's channel), `{subtype}` (0 for the main stream, 1 for the substream) and `{stream_number}` (1 for the main stream, 2 for the substream). Without the setting, the path SHALL be `/cam/realmonitor?channel={channel}&subtype={subtype}`. Loading the configuration SHALL fail, naming `dvr.stream_path`, when the template does not start with `/`, does not use `{channel}`, or uses any other placeholder.

#### Scenario: Default path
- **WHEN** `sentinel.yaml` has no `dvr.stream_path` and the camera `garagem` is channel 1, main stream
- **THEN** the gateway reads `rtsp://<host>:554/cam/realmonitor?channel=1&subtype=0`

#### Scenario: A DVR that numbers its streams from 1
- **WHEN** `dvr.stream_path` is `/Streaming/Channels/{channel}0{stream_number}` and the camera `rua` is channel 3, substream
- **THEN** the gateway and the login check use `rtsp://<host>:554/Streaming/Channels/302`

#### Scenario: Unknown placeholder
- **WHEN** `dvr.stream_path` is `/live/{camera}`
- **THEN** loading the configuration fails with an error that names `dvr.stream_path` and lists the placeholders it accepts
