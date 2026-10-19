"""Logging for the components, with credentials redacted from every line."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sentinel.runtime.redact import RedactingFilter

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(component: str, secrets: Iterable[str] = ()) -> RedactingFilter:
    """Log INFO and above to stderr; returns the filter, to add secrets read later."""
    redacting = RedactingFilter(secrets)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(redacting)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    return redacting
