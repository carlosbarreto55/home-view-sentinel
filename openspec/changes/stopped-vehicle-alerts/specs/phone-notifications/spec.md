## ADDED Requirements

### Requirement: Self-hosted push server
A ntfy server SHALL run on the Mac with `upstream-base-url` set to `https://ntfy.sh`, so the iPhone is woken through Apple's push service while the message and the snapshot are only stored and served by the home server. Access SHALL be denied by default: the phone SHALL have a user that can only read the Sentinel topic, and the notifier a token that can only write to it. The server SHALL be reachable over HTTPS at the Mac's Tailscale name, from the iPhone at home and on mobile data, and SHALL NOT be reachable from the LAN or the internet otherwise.

#### Scenario: Unauthenticated access
- **WHEN** someone on the tailnet opens the Sentinel topic without credentials
- **THEN** the server refuses it

#### Scenario: Away from home
- **WHEN** the iPhone is on mobile data with Tailscale connected and a `dwell.started` is published
- **THEN** the push arrives on the locked iPhone and the snapshot opens in the ntfy app

### Requirement: Alert content
For each `dwell.started`, the notifier SHALL send one push in Portuguese with the snapshot attached:
- a motorcycle with a rider: title "Moto com piloto parada em frente de casa", high priority;
- a motorcycle without a rider: title "Moto parada em frente de casa", default priority;
- a car: title "Carro parado em frente de casa", default priority;
- body: camera name, zone name, how long it has been stopped and the local time, e.g. "Garagem · Calçada · parado há 2 min · 15:04" (time zone Etc/GMT+4).

#### Scenario: A motorcyclist stopped at the gate
- **WHEN** a `dwell.started` arrives for a motorcycle with a rider, camera "Garagem", zone "Calçada", 30 seconds, at 15:04:37 UTC
- **THEN** the iPhone shows "Moto com piloto parada em frente de casa" with "Garagem · Calçada · parada há 30 s · 11:04" and the snapshot, with high priority

### Requirement: End notifications
`dwell.ended` events SHALL NOT be sent by default. When enabled in the configuration, each SHALL be sent at low priority as "Saiu após <duração>" with the camera and zone.

#### Scenario: Enabled during calibration
- **WHEN** end notifications are enabled and a car leaves after 7 minutes
- **THEN** a low-priority push says "Saiu após 7 min" with "Garagem · Calçada"

### Requirement: Quiet hours
The configuration MAY define quiet hours. During them, alerts SHALL still be sent, at low priority (no sound). With no quiet hours configured, every alert SHALL use its normal priority.

#### Scenario: A car at 3 a.m.
- **WHEN** quiet hours are 23:00 to 06:00 and a car triggers `dwell.started` at 03:10 local time
- **THEN** the push is delivered silently at low priority

### Requirement: Detector offline alert
When the detector's status has been `offline` for more than 2 minutes, the notifier SHALL send "O Sentinel parou de vigiar as câmeras" once, and when it is `online` again, "O Sentinel voltou a vigiar". A camera stuck in `reconnecting` for more than 10 minutes SHALL send "Sem imagem da câmera <nome>" once.

#### Scenario: The detector crashes and comes back
- **WHEN** the detector crashes and launchd fails to bring it back for 3 minutes, then it starts
- **THEN** the iPhone receives "O Sentinel parou de vigiar as câmeras" and later "O Sentinel voltou a vigiar", once each

#### Scenario: A short restart
- **WHEN** the detector restarts and is back online within 20 seconds
- **THEN** no offline push is sent

### Requirement: Delivery is a consumer, not part of detection
The notifier SHALL depend only on the event contract and the broker. A failure of the ntfy server SHALL NOT affect detection; the notifier SHALL retry a failed send 3 times over 1 minute and then log it.

#### Scenario: ntfy is down
- **WHEN** the ntfy container is stopped and a `dwell.started` is published
- **THEN** the detector keeps running normally, and the notifier logs the failed delivery after its retries
