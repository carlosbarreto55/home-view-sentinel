## ADDED Requirements

### Requirement: Single connection to the DVR
The stream gateway SHALL be the only Sentinel component that connects to the DVR. It SHALL hold at most one RTSP session to the DVR per configured stream, however many local consumers read that stream, and SHALL serve each configured camera locally at `rtsp://127.0.0.1:8554/<camera_id>`.

#### Scenario: Two consumers of one camera
- **WHEN** the detector and a test client (ffprobe) both read `rtsp://127.0.0.1:8554/garagem`
- **THEN** the DVR has exactly one RTSP connection from the Mac for channel 1's main stream on behalf of Sentinel

#### Scenario: Only configured cameras are requested
- **WHEN** `sentinel.yaml` lists only the cameras on channels 1 and 3
- **THEN** the gateway never opens a session for channels 2, 4, 5, 6, 7 or 8

### Requirement: Dedicated DVR user and credential handling
The gateway SHALL connect with a DVR user dedicated to Sentinel, read from the macOS Keychain at start. The DVR user and password SHALL NOT appear in any file in the repository, in a container image, in an MQTT event or in a log. Logs SHALL show stream addresses without user and password. Credentials containing reserved characters (`@ : / # ?`) SHALL work.

#### Scenario: No secret on disk
- **WHEN** the service has been running and the user searches the repository, the rendered go2rtc configuration and the logs for the DVR password
- **THEN** the password is found nowhere

#### Scenario: Password with reserved characters
- **WHEN** the Sentinel DVR user's password is `a@b:c/d#e`
- **THEN** the gateway authenticates and serves the configured cameras

### Requirement: Login check before starting
Before the gateway starts, the launcher SHALL send one authenticated RTSP `DESCRIBE` to the DVR and classify the result as accepted, wrong credentials, or DVR not found. On wrong credentials it SHALL NOT start the gateway, SHALL NOT be restarted automatically, and SHALL log and publish the status "Usuário ou senha do DVR incorretos". The service SHALL make at most one failed login attempt per start, so the DVR never locks the user because of Sentinel.

#### Scenario: Wrong password
- **WHEN** the Keychain holds a wrong password for the Sentinel DVR user and the service starts
- **THEN** the DVR receives exactly one failed login, the gateway does not start, and the gateway status says "Usuário ou senha do DVR incorretos" until the user runs `make secrets` and starts the service again

#### Scenario: DVR unreachable
- **WHEN** the DVR is switched off and the service starts
- **THEN** within 5 seconds the launcher reports the DVR as not found at 192.0.2.10:554, and launchd starts it again later, so the gateway comes up by itself once the DVR is back

### Requirement: Local-only exposure
The gateway's RTSP port SHALL be published on the Mac's loopback interface only, and its API and WebRTC ports SHALL be disabled or loopback-only. No gateway port SHALL be reachable from another device on the LAN or the tailnet.

#### Scenario: Probing from another device
- **WHEN** a phone on the home Wi-Fi tries to open `rtsp://<Mac's LAN address>:8554/garagem`
- **THEN** the connection is refused
