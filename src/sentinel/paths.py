"""Where Sentinel keeps its data and logs on the Mac."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "HomeView Sentinel"


def data_dir() -> Path:
    """Snapshots, broker persistence and ntfy data (``SENTINEL_DATA_DIR`` overrides it)."""
    override = os.environ.get("SENTINEL_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / APP_NAME


def snapshots_dir() -> Path:
    return data_dir() / "snapshots"


def logs_dir() -> Path:
    override = os.environ.get("SENTINEL_LOG_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Logs" / APP_NAME


def gateway_dir() -> Path:
    """The rendered go2rtc configuration, mounted into the gateway container."""
    return data_dir() / "gateway"


def gateway_stop_marker() -> Path:
    """Present after the DVR rejected the login: the gateway stays stopped (no
    second failed login) until ``make secrets`` stores a new password."""
    return gateway_dir() / "stopped-wrong-login"
