"""Check the DVR address and login with one authenticated RTSP DESCRIBE.

A Python port of HomeView's ``RTSPProbe``: send ``DESCRIBE``, answer the Digest
(or Basic) challenge once, and classify the answer. The whole check gives up
after 5 seconds. At most one request carries credentials, so a wrong password
costs exactly one failed login on the DVR.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import secrets
import socket
import time
from dataclasses import dataclass
from urllib.parse import quote

USER_AGENT = "HomeView-Sentinel"


class ProbeResult(enum.Enum):
    ACCEPTED = "accepted"
    WRONG_CREDENTIALS = "wrong_credentials"
    NOT_FOUND = "not_found"


def encode_credential(text: str) -> str:
    """Percent-encode everything except RFC 3986 unreserved characters (like HomeView)."""
    return quote(text, safe="-._~")


def stream_url(host: str, port: int, path: str) -> str:
    """The stream address without credentials, safe for logs."""
    return f"rtsp://{host}:{port}{path}"


@dataclass(frozen=True)
class Response:
    status: int
    headers: list[tuple[str, str]]

    def values(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]


@dataclass(frozen=True)
class DigestChallenge:
    realm: str
    nonce: str
    qop: str | None = None
    opaque: str | None = None
    algorithm: str | None = None

    @classmethod
    def parse(cls, header: str) -> DigestChallenge | None:
        """Parse a ``WWW-Authenticate`` value; None when it is not a usable Digest challenge."""
        text = header.strip()
        if not text.lower().startswith("digest "):
            return None
        params = parse_parameters(text[len("digest ") :])
        if "realm" not in params or "nonce" not in params:
            return None
        offered = [q.strip().lower() for q in params.get("qop", "").split(",")]
        return cls(
            realm=params["realm"],
            nonce=params["nonce"],
            qop="auth" if "auth" in offered else None,
            opaque=params.get("opaque"),
            algorithm=params.get("algorithm"),
        )


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _quote_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def digest_authorization(
    challenge: DigestChallenge,
    method: str,
    uri: str,
    user: str,
    password: str,
    nonce_count: int = 1,
    client_nonce: str | None = None,
) -> str:
    """The ``Authorization`` header value answering a Digest challenge (RFC 2617, MD5)."""
    client_nonce = client_nonce or secrets.token_hex(8)
    ha1 = _md5(f"{user}:{challenge.realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    nc = f"{nonce_count:08x}"
    if challenge.qop:
        response = _md5(f"{ha1}:{challenge.nonce}:{nc}:{client_nonce}:{challenge.qop}:{ha2}")
    else:
        response = _md5(f"{ha1}:{challenge.nonce}:{ha2}")
    fields = [
        f"username={_quote_value(user)}",
        f"realm={_quote_value(challenge.realm)}",
        f"nonce={_quote_value(challenge.nonce)}",
        f"uri={_quote_value(uri)}",
        f"response={_quote_value(response)}",
    ]
    if challenge.qop:
        fields += [f"qop={challenge.qop}", f"nc={nc}", f"cnonce={_quote_value(client_nonce)}"]
    if challenge.opaque is not None:
        fields.append(f"opaque={_quote_value(challenge.opaque)}")
    if challenge.algorithm is not None:
        fields.append(f"algorithm={challenge.algorithm}")
    return "Digest " + ", ".join(fields)


def parse_parameters(text: str) -> dict[str, str]:
    """Parse ``key="quoted, value", key2=token`` pairs; keys are lowercased."""
    result: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t,":
            i += 1
        start = i
        while i < n and text[i] not in "=,":
            i += 1
        key = text[start:i].strip().lower()
        if i >= n or text[i] != "=":
            continue
        i += 1
        while i < n and text[i] in " \t":
            i += 1
        value = []
        if i < n and text[i] == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                value.append(text[i])
                i += 1
            i += 1
            parsed = "".join(value)
        else:
            while i < n and text[i] != ",":
                value.append(text[i])
                i += 1
            parsed = "".join(value).strip()
        if key:
            result[key] = parsed
    return result


def describe_request(uri: str, sequence: int, authorization: str | None = None) -> bytes:
    lines = [
        f"DESCRIBE {uri} RTSP/1.0",
        f"CSeq: {sequence}",
        f"User-Agent: {USER_AGENT}",
        "Accept: application/sdp",
    ]
    if authorization:
        lines.append(f"Authorization: {authorization}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class _Closed(Exception):
    """The server closed the connection before answering."""


class _Connection:
    def __init__(self, host: str, port: int, deadline: float):
        self.deadline = deadline
        self.sock = socket.create_connection((host, port), timeout=self._remaining())
        self.buffer = b""

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def send(self, request: bytes) -> Response:
        try:
            self.sock.settimeout(self._remaining())
            self.sock.sendall(request)
            while b"\r\n\r\n" not in self.buffer:
                self.sock.settimeout(self._remaining())
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise _Closed
                self.buffer += chunk
        except (BrokenPipeError, ConnectionResetError):
            raise _Closed from None
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        response = _parse_head(head.decode("utf-8", errors="replace"))
        length = int(next(iter(response.values("Content-Length")), "0") or 0)
        while len(rest) < length:
            self.sock.settimeout(self._remaining())
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            rest += chunk
        self.buffer = rest[length:]
        return response

    def close(self) -> None:
        self.sock.close()


def _parse_head(text: str) -> Response:
    lines = text.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("RTSP/") or not parts[1].isdigit():
        raise ValueError(f"not an RTSP response: {lines[0]!r}")
    headers = []
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers.append((key.strip(), value.strip()))
    return Response(status=int(parts[1]), headers=headers)


def _authorization(response: Response, uri: str, user: str, password: str) -> str | None:
    offers = response.values("WWW-Authenticate")
    for offer in offers:
        challenge = DigestChallenge.parse(offer)
        if challenge:
            return digest_authorization(challenge, "DESCRIBE", uri, user, password)
    if any(offer.strip().lower().startswith("basic") for offer in offers):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {token}"
    return None


def probe(
    host: str,
    port: int,
    user: str,
    password: str,
    path: str = "/",
    timeout: float = 5.0,
) -> ProbeResult:
    """DESCRIBE one stream; when challenged, answer once and classify the result."""
    deadline = time.monotonic() + timeout
    uri = stream_url(host, port, path)
    connection = None
    try:
        connection = _Connection(host, port, deadline)
        challenge = connection.send(describe_request(uri, 1))
        if challenge.status != 401:
            return ProbeResult.ACCEPTED
        authorization = _authorization(challenge, uri, user, password)
        if authorization is None:
            return ProbeResult.NOT_FOUND
        request = describe_request(uri, 2, authorization)
        try:
            answer = connection.send(request)
        except _Closed:
            # Some servers close the connection after a 401; ask again on a new one.
            connection.close()
            connection = _Connection(host, port, deadline)
            answer = connection.send(request)
        return ProbeResult.WRONG_CREDENTIALS if answer.status == 401 else ProbeResult.ACCEPTED
    except (OSError, TimeoutError, ValueError, _Closed):
        return ProbeResult.NOT_FOUND
    finally:
        if connection:
            connection.close()
