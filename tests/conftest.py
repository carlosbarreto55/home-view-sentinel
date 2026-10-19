import pytest

from fake_rtsp import FakeRTSP


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    """Tests never touch the real data and log folders."""
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SENTINEL_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture
def fake_rtsp():
    """Start fake RTSP servers: ``fake_rtsp(mode=..., password=...)``."""
    servers = []

    def start(**options) -> FakeRTSP:
        server = FakeRTSP(**options)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()
