"""A fake RTSP server for tests: challenges, accepts, rejects or stays silent."""

from __future__ import annotations

import base64
import hashlib
import socketserver
import threading
from dataclasses import dataclass, field

from sentinel.rtsp_probe import parse_parameters

REALM = "Login to fake"
NONCE = "0123456789abcdef0123456789abcdef"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


@dataclass
class FakeRTSP:
    """``mode``: "digest" (no qop, like the DVR), "digest-qop", "basic", "open"
    (answers 200 without a challenge), "silent" (never answers) or "garbage"."""

    user: str = "sentinel"
    password: str = "a@b:c/d#e"
    mode: str = "digest"
    close_after_challenge: bool = False
    # Answer to accepted credentials (the DVR may answer 404 for a channel without camera).
    accepted_status: str = "200 OK"
    failed_logins: int = 0
    accepted_logins: int = 0
    connections: int = 0
    requests: list[str] = field(default_factory=list)

    def __post_init__(self):
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                fake.connections += 1
                fake._serve(self)

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _serve(self, handler):
        while True:
            lines = []
            while True:
                line = handler.rfile.readline()
                if not line:
                    return
                line = line.decode(errors="replace").rstrip("\r\n")
                if not line:
                    break
                lines.append(line)
            self.requests.append("\r\n".join(lines))
            if self.mode == "silent":
                handler.rfile.read()  # wait until the client gives up
                return
            if self.mode == "garbage":
                handler.wfile.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            method, uri = lines[0].split(" ")[:2]
            cseq = _header(lines, "cseq") or "0"
            auth = _header(lines, "authorization")
            if self.mode == "open":
                self._reply(handler, "200 OK", cseq)
                continue
            if auth is None:
                self._reply(handler, "401 Unauthorized", cseq, self._challenge())
                if self.close_after_challenge:
                    return
                continue
            if self._valid(auth, method, uri):
                self.accepted_logins += 1
                self._reply(handler, self.accepted_status, cseq)
            else:
                self.failed_logins += 1
                self._reply(handler, "401 Unauthorized", cseq, self._challenge())

    def _challenge(self) -> str:
        if self.mode == "basic":
            return f'WWW-Authenticate: Basic realm="{REALM}"'
        qop = ', qop="auth"' if self.mode == "digest-qop" else ""
        return f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"{qop}'

    def _valid(self, auth: str, method: str, uri: str) -> bool:
        if self.mode == "basic":
            expected = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            return auth == f"Basic {expected}"
        if not auth.startswith("Digest "):
            return False
        fields = parse_parameters(auth[len("Digest ") :])
        ha1 = _md5(f"{self.user}:{REALM}:{self.password}")
        ha2 = _md5(f"{method}:{fields.get('uri', '')}")
        if self.mode == "digest-qop":
            expected = _md5(f"{ha1}:{NONCE}:{fields.get('nc')}:{fields.get('cnonce')}:auth:{ha2}")
        else:
            expected = _md5(f"{ha1}:{NONCE}:{ha2}")
        return (
            fields.get("username") == self.user
            and fields.get("uri") == uri
            and fields.get("response") == expected
        )

    @staticmethod
    def _reply(handler, status, cseq, *headers):
        text = "\r\n".join([f"RTSP/1.0 {status}", f"CSeq: {cseq}", *headers]) + "\r\n\r\n"
        handler.wfile.write(text.encode())


def _header(lines: list[str], name: str) -> str | None:
    for line in lines[1:]:
        key, _, value = line.partition(":")
        if key.strip().lower() == name:
            return value.strip()
    return None
