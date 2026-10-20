from sentinel.config import Notifications
from sentinel.control import __main__ as control_main
from sentinel.control.follow import Follower


class FakeClient:
    def __init__(self, calls):
        self.calls = calls
        self.connected = False

    def start(self):
        self.calls.append("bus start")

    def stop(self):
        self.calls.append("bus stop")


def test_components_connect_the_bus_before_starting_and_after_stopping(tmp_path, monkeypatch):
    calls, secrets_seen = [], []
    follower = Follower((), Notifications(), tmp_path, tmp_path / "last-alert.json")
    monkeypatch.setattr(control_main.secrets, "require", lambda item: f"pw-{item}")
    monkeypatch.setattr(control_main.follow, "connect", lambda f, pw: FakeClient(calls))
    monkeypatch.setattr(
        control_main.launchd, "start_components", lambda: calls.append("agents start")
    )
    monkeypatch.setattr(
        control_main.launchd, "stop_components", lambda: calls.append("agents stop") or []
    )
    components = control_main.Components(follower, secrets_seen.append)
    components.start()
    components.start()  # adopting running components: one bus client only
    assert components.stop() == []
    assert calls == ["bus start", "agents start", "agents start", "bus stop", "agents stop"]
    assert secrets_seen == ["pw-mqtt-control"]
    components.disconnect()  # harmless when already disconnected
    assert components.client is None
