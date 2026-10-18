import base64
import email.header
import http.server
import threading

import pytest

from sentinel.notifier.messages import Push
from sentinel.notifier.ntfy import NtfyClient, header_value

TOKEN = "tk_abcdefghijklmnopqrstuvwxyz012"


class FakeNtfy:
    """Answers with the next status of ``statuses`` (then 200) and records requests."""

    def __init__(self, statuses=()):
        self.statuses = list(statuses)
        self.requests = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                length = int(self.headers.get("Content-Length", 0))
                fake.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(length),
                    }
                )
                status = fake.statuses.pop(0) if fake.statuses else 200
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ntfy():
    servers = []

    def start(statuses=()):
        server = FakeNtfy(statuses)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def decode(value):
    return str(email.header.make_header(email.header.decode_header(value)))


def client(server, tmp_path, slept=None):
    return NtfyClient(
        server.url,
        "sentinel",
        TOKEN,
        tmp_path,
        sleep=(slept.append if slept is not None else lambda s: None),
    )


ALERT = Push(
    title="Moto com piloto parada em frente de casa",
    message="Garagem · Calçada · parada há 30 s · 11:04",
    priority="high",
    tags=("motorcycle",),
    snapshot="garagem/2026-10-18T15-04-37Z-garagem-42.jpg",
)


def test_alert_with_snapshot(ntfy, tmp_path):
    (tmp_path / "garagem").mkdir()
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    (tmp_path / ALERT.snapshot).write_bytes(jpeg)
    server = ntfy()
    assert client(server, tmp_path).send(ALERT) is True
    [request] = server.requests
    headers = request["headers"]
    assert request["path"] == "/sentinel"
    assert request["body"] == jpeg
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert decode(headers["Title"]) == "Moto com piloto parada em frente de casa"
    assert decode(headers["Message"]) == "Garagem · Calçada · parada há 30 s · 11:04"
    assert headers["Priority"] == "high"
    assert headers["Tags"] == "motorcycle"
    assert headers["Filename"] == "2026-10-18T15-04-37Z-garagem-42.jpg"


def test_push_without_snapshot_sends_the_message_as_body(ntfy, tmp_path):
    server = ntfy()
    push = Push("Saiu após 7 min", "Garagem · Calçada", "low", ("wave",))
    assert client(server, tmp_path).send(push)
    [request] = server.requests
    assert request["body"].decode() == "Garagem · Calçada"
    assert "Filename" not in request["headers"]
    assert "Message" not in request["headers"]


def test_missing_snapshot_still_sends_the_text(ntfy, tmp_path):
    server = ntfy()
    assert client(server, tmp_path).send(ALERT)
    assert server.requests[0]["body"].decode() == ALERT.message


def test_retries_three_times_over_a_minute_then_succeeds(ntfy, tmp_path):
    server = ntfy([503, 502, 500])
    slept = []
    assert client(server, tmp_path, slept).send(ALERT)
    assert len(server.requests) == 4
    assert slept == [10, 20, 30]
    assert sum(slept) == 60


def test_gives_up_after_three_retries_and_logs(ntfy, tmp_path, caplog):
    server = ntfy([500] * 10)
    assert client(server, tmp_path).send(ALERT) is False
    assert len(server.requests) == 4
    assert "gave up" in caplog.text


def test_server_down_is_retried(tmp_path, caplog):
    slept = []
    down = NtfyClient("http://127.0.0.1:1", "sentinel", TOKEN, tmp_path, sleep=slept.append)
    assert down.send(ALERT) is False
    assert slept == [10, 20, 30]
    assert "gave up" in caplog.text


def test_refused_token_is_not_retried(ntfy, tmp_path):
    server = ntfy([403])
    assert client(server, tmp_path).send(ALERT) is False
    assert len(server.requests) == 1


def test_header_value_encoding():
    assert header_value("plain") == "plain"
    encoded = header_value("Calçada")
    assert encoded == "=?UTF-8?B?" + base64.b64encode("Calçada".encode()).decode() + "?="
    assert decode(encoded) == "Calçada"
