"""Keep credentials out of logs.

go2rtc logs a stream's full source URL, password included, when the DVR
connection fails, so nothing a component logs is trusted to be clean: every line
goes through ``redact``, which hides the user and password of any ``rtsp://``
URL and any known secret value.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

MASK = "<redacted>"
_URL_CREDENTIALS = re.compile(r"(rtsps?://)[^/@\s]+@")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    text = _URL_CREDENTIALS.sub(rf"\g<1>{MASK}@", text)
    # Longest first, so a secret that contains another is hidden whole.
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


class RedactingFilter(logging.Filter):
    """A logging filter that redacts each record's final message."""

    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def add(self, *secrets: str) -> None:
        self.secrets.extend(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg = redact(message, self.secrets)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text, self.secrets)
        return True
