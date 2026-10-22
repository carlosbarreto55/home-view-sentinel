## Why

The stream gateway and the login check build every camera's RTSP address with one fixed path, `/cam/realmonitor?channel=N&subtype=S`. That is the path of the DVR Sentinel was built against, but DVR makers use different paths: another common form is `/Streaming/Channels/101`, where the last digit numbers the main stream 1 and the substream 2. With the path fixed in code, Sentinel only works with DVRs that happen to use the first form.

## What Changes

- A new setting, `dvr.stream_path` in `sentinel.yaml`, holds the RTSP path of a camera's stream as a template with three placeholders: `{channel}`, `{subtype}` (0 for the main stream, 1 for the substream) and `{stream_number}` (1 for the main stream, 2 for the substream).
- The default is today's path, so existing configurations keep working unchanged.
- The configuration is checked when it loads: the path must start with `/`, must use `{channel}`, and may only use those three placeholders, with errors naming `dvr.stream_path`.
- The go2rtc configuration and the login check both build the address from the setting.
- The example configuration and the README explain the setting, with the two common forms.

Out of scope: per-camera paths, cameras on different hosts, and discovering the path from the DVR (ONVIF).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `stream-gateway`: the stream address comes from the configured path template instead of a fixed path.

## Impact

- Changed code: `sentinel.config` (the setting and its checks), `sentinel.rtsp_probe` (the probe takes the stream path), `sentinel.gateway.go2rtc` and `sentinel.gateway.launcher` (build the path from the setting).
- `config/sentinel.example.yaml`, `README.md` and `openspec/config.yaml` describe the setting.
- No change for an existing `sentinel.yaml`: without the setting, the path is the same as before.
