import pytest

from fake_rtsp import FakeRTSP


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
