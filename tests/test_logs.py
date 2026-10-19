import logging
import sys
from pathlib import Path

import pytest

from sentinel.runtime import logs


@pytest.fixture(autouse=True)
def restore_logging():
    """``setup`` changes the root logger and the excepthook; put them back."""
    root = logging.getLogger()
    handlers, level, hook = root.handlers[:], root.level, sys.excepthook
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:], sys.excepthook = handlers, hook
    root.setLevel(level)


def test_under_launchd_each_component_logs_to_its_own_rotating_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_LOG_FILE", "1")
    monkeypatch.setenv("SENTINEL_LOG_DIR", str(tmp_path))
    redacting = logs.setup("gateway")
    redacting.add("s3cret")
    logging.getLogger("sentinel.gateway").info("url=rtsp://u:p@dvr/ token s3cret")
    handler = logging.getLogger().handlers[0]
    handler.flush()
    assert handler.when == "MIDNIGHT" and handler.backupCount == 6  # 7 days with today's
    text = (tmp_path / "gateway.log").read_text()
    assert "url=rtsp://<redacted>@dvr/ token <redacted>" in text
    assert "s3cret" not in text


def test_rotation_keeps_seven_days(tmp_path):
    handler = logs.rotating_handler("detector", tmp_path)
    for day in range(1, 11):  # ten days of rotated files
        (tmp_path / f"detector.log.2026-10-{day:02d}").write_text(f"day {day}")
    old = sorted(Path(p).name for p in handler.getFilesToDelete())
    handler.close()
    # Today's detector.log plus the 6 newest rotated files make 7 days.
    assert old == [f"detector.log.2026-10-{day:02d}" for day in range(1, 5)]


def test_a_big_launchd_log_is_emptied_at_start(tmp_path):
    path = logs.launchd_log_file("notifier", tmp_path)
    path.write_bytes(b"x" * (logs.LAUNCHD_LOG_LIMIT + 1))
    logs.trim_launchd_log("notifier", tmp_path)
    assert path.stat().st_size == 0
    path.write_bytes(b"small")
    logs.trim_launchd_log("notifier", tmp_path)
    assert path.read_bytes() == b"small"


def test_by_hand_components_log_to_the_terminal(monkeypatch):
    monkeypatch.delenv("SENTINEL_LOG_FILE", raising=False)
    logs.setup("detector")
    assert type(logging.getLogger().handlers[0]) is logging.StreamHandler
