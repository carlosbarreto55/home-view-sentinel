"""Send pushes to the self-hosted ntfy server.

``PUT /<topic>`` with the snapshot as the body (``Filename`` names it) and the
text in headers, or the message as the body when there is no snapshot. The
notifier authenticates with its write-only token. Non-ASCII header values are
sent as RFC 2047 encoded words, which ntfy decodes.

A failed send is retried 3 times over a minute (after 10, 20 and 30 s), then
given up and logged; the detector is never affected.
"""

from __future__ import annotations

import base64
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from sentinel.notifier.messages import Push

log = logging.getLogger(__name__)

RETRY_DELAYS = (10.0, 20.0, 30.0)
TIMEOUT = 15.0


def header_value(text: str) -> str:
    """ASCII as is; anything else as an RFC 2047 encoded word."""
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


class NtfyClient:
    def __init__(
        self,
        base_url: str,
        topic: str,
        token: str,
        snapshots_dir: Path,
        sleep: Callable[[float], None] = time.sleep,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        timeout: float = TIMEOUT,
    ):
        self.url = f"{base_url.rstrip('/')}/{topic}"
        self.token = token
        self.snapshots_dir = snapshots_dir
        self.sleep = sleep
        self.retry_delays = retry_delays
        self.timeout = timeout

    def request(self, push: Push) -> urllib.request.Request:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Title": header_value(push.title),
            "Priority": push.priority,
        }
        if push.tags:
            headers["Tags"] = ",".join(push.tags)
        body = push.message.encode()
        snapshot = self._snapshot(push)
        if snapshot is not None:
            body = snapshot.read_bytes()
            headers["Filename"] = snapshot.name
            headers["Content-Type"] = "image/jpeg"
            if push.message:
                headers["Message"] = header_value(push.message)
        return urllib.request.Request(self.url, data=body, headers=headers, method="PUT")

    def _snapshot(self, push: Push) -> Path | None:
        if not push.snapshot:
            return None
        path = self.snapshots_dir / push.snapshot
        if not path.is_file():
            log.warning("snapshot %s not found; sending without it", push.snapshot)
            return None
        return path

    def send(self, push: Push) -> bool:
        """Send with retries; returns whether ntfy accepted it."""
        attempts = 1 + len(self.retry_delays)
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(self.request(push), timeout=self.timeout) as response:
                    log.info("sent %r (%s)", push.title, response.status)
                    return True
            except urllib.error.HTTPError as error:
                if 400 <= error.code < 500 and error.code != 429:
                    log.error("ntfy refused %r: %s %s", push.title, error.code, error.reason)
                    return False
                problem = f"{error.code} {error.reason}"
            except (urllib.error.URLError, OSError) as error:
                problem = str(getattr(error, "reason", error))
            if attempt < len(self.retry_delays):
                delay = self.retry_delays[attempt]
                log.warning("sending %r failed (%s); retrying in %g s", push.title, problem, delay)
                self.sleep(delay)
            else:
                log.error("gave up sending %r after %d attempts: %s", push.title, attempts, problem)
        return False
