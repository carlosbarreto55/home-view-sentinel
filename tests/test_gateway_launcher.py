import logging
from pathlib import Path

import pytest

from sentinel import config, secrets
from sentinel.gateway import launcher
from sentinel.gateway.launcher import WRONG_LOGIN, Gateway
from sentinel.rtsp_probe import ProbeResult
from sentinel.runtime import EXIT_DONE, EXIT_RESTART
from sentinel.runtime.container import RunResult

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"
PASSWORD = "a@b:c/d#e"
EXITED = RunResult(stopped=False, exit_code=1)


class FakeBus:
    def __init__(self):
        self.statuses = []
        self.stopped_with = None

    def start(self):
        pass

    def wait_connected(self, timeout=None):
        return True

    def set_status(self, state, detail=None, component=None):
        self.statuses.append((state, detail))

    def stop(self, detail="stopped"):
        self.stopped_with = detail


class FakeProbe:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, host, port, user, password, path):
        self.calls.append((host, port, user, password, path))
        return self.result


class FakeContainer:
    def __init__(self, result=EXITED, lines=()):
        self.result = result
        self.lines = lines
        self.calls = []

    def __call__(self, spec, env, on_line):
        self.calls.append((spec, env))
        for line in self.lines:
            on_line(line)
        return self.result


def gateway(tmp_path, result=ProbeResult.ACCEPTED, run=None, keychain=None, system=True):
    keychain = (
        keychain
        if keychain is not None
        else {
            secrets.DVR_USER: "sentinel",
            secrets.DVR_PASSWORD: PASSWORD,
        }
    )
    gw = Gateway(
        config.load(EXAMPLE),
        FakeBus(),
        config_dir=tmp_path / "gateway",
        stop_marker=tmp_path / "gateway" / "stopped-wrong-login",
        read_secret=keychain.get,
        check_login=FakeProbe(result),
        run_container=run or FakeContainer(),
        wait_for_system=lambda: system,
    )
    return gw


def test_wrong_password_stops_for_good_after_one_login(tmp_path, caplog):
    gw = gateway(tmp_path, ProbeResult.WRONG_CREDENTIALS)
    with caplog.at_level(logging.ERROR):
        assert gw.run() == EXIT_DONE
    assert len(gw.check_login.calls) == 1
    assert gw.run_container.calls == []
    assert gw.stop_marker.exists()
    assert gw.bus.stopped_with == WRONG_LOGIN
    assert WRONG_LOGIN in caplog.text
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert PASSWORD not in messages and "sentinel" not in messages  # neither user nor password

    # Started again (by launchd at login, or by hand): no second login attempt.
    again = gateway(tmp_path, ProbeResult.WRONG_CREDENTIALS)
    assert again.run() == EXIT_DONE
    assert again.check_login.calls == []
    assert again.bus.stopped_with == WRONG_LOGIN


def test_dvr_not_found_exits_for_launchd_to_try_again(tmp_path):
    gw = gateway(tmp_path, ProbeResult.NOT_FOUND)
    assert gw.run() == EXIT_RESTART
    assert gw.run_container.calls == []
    assert not gw.stop_marker.exists()
    assert gw.bus.stopped_with == "DVR not found at 192.0.2.10:554"


def test_accepted_login_runs_go2rtc_with_the_login_in_its_environment_only(tmp_path):
    gw = gateway(tmp_path)
    assert gw.run() == EXIT_RESTART  # go2rtc exited on its own
    host, port, user, password, path = gw.check_login.calls[0]
    camera = gw.config.cameras[0]
    assert (host, port, user, password) == ("192.0.2.10", 554, "sentinel", PASSWORD)
    assert path == gw.config.dvr.path(camera.channel, camera.subtype)

    spec, env = gw.run_container.calls[0]
    assert env == {"DVR_USER": "sentinel", "DVR_PASSWORD": "a%40b%3Ac%2Fd%23e"}
    command = " ".join(spec.run_command())
    assert PASSWORD not in command and "a%40b" not in command
    assert "--env DVR_USER --env DVR_PASSWORD" in command
    assert "--publish 127.0.0.1:8554:8554" in command
    rendered = (tmp_path / "gateway" / "go2rtc.yaml").read_text()
    assert "${DVR_PASSWORD}" in rendered and PASSWORD not in rendered
    assert gw.bus.statuses == [("online", None)]
    assert gw.bus.stopped_with == "go2rtc exited (1)"


def test_stopped_on_purpose_is_not_restarted(tmp_path):
    gw = gateway(tmp_path, run=FakeContainer(RunResult(stopped=True, exit_code=0)))
    assert gw.run() == EXIT_DONE
    assert gw.bus.stopped_with == "stopped"


def test_missing_login_waits_for_make_secrets_without_probing(tmp_path):
    gw = gateway(tmp_path, keychain={})
    assert gw.run() == EXIT_DONE
    assert gw.check_login.calls == []


def test_container_system_not_running_retries_later(tmp_path):
    gw = gateway(tmp_path, system=False)
    assert gw.run() == EXIT_RESTART
    assert gw.run_container.calls == []


def test_go2rtc_output_never_logs_the_password(tmp_path, caplog):
    line = (
        'WRN error="i/o timeout" url=rtsp://sentinel:a%40b%3Ac%2Fd%23e@192.0.2.10:554/'
        "cam/realmonitor?channel=1&subtype=0"
    )
    gw = gateway(tmp_path, run=FakeContainer(lines=[line]))
    with caplog.at_level(logging.INFO):
        gw.run()
    assert "rtsp://<redacted>@192.0.2.10:554/cam/realmonitor" in caplog.text
    assert "a%40b" not in caplog.text


@pytest.mark.parametrize("probe_result", list(ProbeResult))
def test_the_dvr_gets_at_most_one_login_per_start(tmp_path, probe_result):
    gw = gateway(tmp_path, probe_result)
    gw.run()
    assert len(gw.check_login.calls) == 1


def test_container_spec_mounts_the_rendered_config(tmp_path):
    spec = launcher.container_spec(tmp_path)
    command = spec.run_command()
    assert command[:5] == ["container", "run", "--rm", "--name", "sentinel-gateway"]
    assert f"{tmp_path}:/config" in command
    assert command[-4:] == ["alexxit/go2rtc:1.9.10", "go2rtc", "-config", "/config/go2rtc.yaml"]
