"""Logging for the components, with credentials redacted from every line.

Under launchd (``SENTINEL_LOG_FILE=1``, set by the agents) each component logs
to ``~/Library/Logs/HomeView Sentinel/<component>.log``, rotated at midnight
with 7 days kept (today's file and 6 older ones). launchd's own output file,
``<component>.launchd.log``, only gets what is written before logging starts
or a crash's traceback; it is emptied at start when it grows past 1 MB.
Run by hand, components log to the terminal.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from sentinel import paths
from sentinel.runtime.redact import RedactingFilter

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DAYS_KEPT = 7
LAUNCHD_LOG_LIMIT = 1_000_000


def log_file(component: str, directory: Path | None = None) -> Path:
    return (directory or paths.logs_dir()) / f"{component}.log"


def launchd_log_file(component: str, directory: Path | None = None) -> Path:
    return (directory or paths.logs_dir()) / f"{component}.launchd.log"


def rotating_handler(component: str, directory: Path | None = None) -> logging.Handler:
    path = log_file(component, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    return TimedRotatingFileHandler(
        path, when="midnight", backupCount=DAYS_KEPT - 1, encoding="utf-8"
    )


def trim_launchd_log(component: str, directory: Path | None = None) -> None:
    path = launchd_log_file(component, directory)
    try:
        if path.stat().st_size > LAUNCHD_LOG_LIMIT:
            # launchd keeps the file open in append mode, so truncating is safe.
            os.truncate(path, 0)
    except OSError:
        pass


def setup(component: str, secrets: Iterable[str] = ()) -> RedactingFilter:
    """Configure the root logger; returns the filter, to add secrets read later."""
    redacting = RedactingFilter(secrets)
    if os.environ.get("SENTINEL_LOG_FILE") == "1":
        handler = rotating_handler(component)
        trim_launchd_log(component)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(redacting)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)

    def log_crash(kind, value, traceback):
        logging.getLogger(component).critical("crashed", exc_info=(kind, value, traceback))
        sys.__excepthook__(kind, value, traceback)

    sys.excepthook = log_crash
    return redacting
