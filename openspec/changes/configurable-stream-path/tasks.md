Each subtask is exactly one commit, using Conventional Commits with the message shown after it. The commit holds the subtask's code, its tests, and this file with the subtask's box ticked. Commits must pass the unit tests (`make test`).

## 1. Stream path

- [x] 1.1 `dvr.stream_path` in `sentinel.config` with its default and checks (starts with `/`, uses `{channel}`, only `{channel}`, `{subtype}` and `{stream_number}`); the probe takes the stream path, and the go2rtc configuration and the launcher's login check build it from the setting; `config/sentinel.example.yaml` documents it; tests for the default, a path numbering streams from 1, and each check — commit: `feat(gateway): read the DVR stream path from the config`
- [ ] 1.2 README requirements and `openspec/config.yaml` describe the setting instead of one fixed path — commit: `docs: describe the DVR stream path setting`
