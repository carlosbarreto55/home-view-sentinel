import logging

from sentinel.runtime.container import wait_for_system
from sentinel.runtime.redact import RedactingFilter, redact


def test_redacts_credentials_in_rtsp_urls():
    line = "url=rtsp://sentinel:p%40ss@192.0.2.10:554/cam/realmonitor?channel=1 x=rtsps://u:p@h/"
    assert redact(line) == (
        "url=rtsp://<redacted>@192.0.2.10:554/cam/realmonitor?channel=1 x=rtsps://<redacted>@h/"
    )


def test_leaves_urls_without_credentials_alone():
    assert redact("rtsp://127.0.0.1:8554/garagem") == "rtsp://127.0.0.1:8554/garagem"


def test_redacts_known_secret_values_anywhere():
    assert redact("password is hunter2-long", ["hunter2", "hunter2-long", ""]) == (
        "password is <redacted>"
    )


def test_logging_filter_redacts_messages_and_arguments(caplog):
    logger = logging.getLogger("test.redact")
    caplog.handler.addFilter(RedactingFilter(["s3cret"]))
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("source %s with %s", "rtsp://u:s3cret@dvr/", "s3cret")
    assert caplog.records[0].getMessage() == "source rtsp://<redacted>@dvr/ with <redacted>"


def test_wait_for_system_gives_up_after_the_timeout():
    checks = iter([False, False, True])
    assert wait_for_system(timeout=60, check=lambda: next(checks), sleep=lambda s: None)
    assert not wait_for_system(timeout=0, check=lambda: False, sleep=lambda s: None)
