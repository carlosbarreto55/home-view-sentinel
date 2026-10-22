from pathlib import Path

import yaml

from sentinel import config
from sentinel.gateway import go2rtc

EXAMPLE = Path(__file__).parent.parent / "config" / "sentinel.example.yaml"
PASSWORD = "a@b:c/d#e"


def two_cameras() -> config.Config:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    rua = dict(data["cameras"][0], id="rua", name="Rua", channel=3, stream="sub")
    data["cameras"].append(rua)
    return config.from_dict(data)


def test_only_configured_cameras_are_streamed():
    rendered = yaml.safe_load(go2rtc.render(two_cameras()))
    assert rendered["streams"] == {
        "garagem": "rtsp://${DVR_USER}:${DVR_PASSWORD}@192.0.2.10:554/cam/realmonitor?channel=1&subtype=0",
        "rua": "rtsp://${DVR_USER}:${DVR_PASSWORD}@192.0.2.10:554/cam/realmonitor?channel=3&subtype=1",
    }


def test_streams_follow_the_configured_path():
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["dvr"]["stream_path"] = "/Streaming/Channels/{channel}0{stream_number}"
    data["cameras"].append(dict(data["cameras"][0], id="rua", name="Rua", channel=3, stream="sub"))
    rendered = yaml.safe_load(go2rtc.render(config.from_dict(data)))
    assert rendered["streams"] == {
        "garagem": "rtsp://${DVR_USER}:${DVR_PASSWORD}@192.0.2.10:554/Streaming/Channels/101",
        "rua": "rtsp://${DVR_USER}:${DVR_PASSWORD}@192.0.2.10:554/Streaming/Channels/302",
    }


def test_only_rtsp_is_served():
    rendered = yaml.safe_load(go2rtc.render(two_cameras(), rtsp_listen="127.0.0.1:8554"))
    assert rendered["rtsp"] == {"listen": "127.0.0.1:8554"}
    for server in ("api", "webrtc", "rtmp", "srtp"):
        assert rendered[server] == {"listen": ""}


def test_rendered_file_contains_no_secret(tmp_path, monkeypatch):
    # Even with the login in the environment, only placeholders are written.
    monkeypatch.setenv("DVR_USER", "camuser")
    monkeypatch.setenv("DVR_PASSWORD", PASSWORD)
    path = go2rtc.write(two_cameras(), tmp_path / "go2rtc" / "go2rtc.yaml")
    text = path.read_text(encoding="utf-8")
    for secret in ("camuser", PASSWORD, go2rtc.encode_credential(PASSWORD)):
        assert secret not in text


def test_environment_is_percent_encoded():
    assert go2rtc.environment("sentinel", PASSWORD) == {
        "DVR_USER": "sentinel",
        "DVR_PASSWORD": "a%40b%3Ac%2Fd%23e",
    }


def test_local_url():
    assert go2rtc.local_url("garagem") == "rtsp://127.0.0.1:8554/garagem"
