import re
import shutil
import subprocess

import pytest

from sentinel import secrets

TEST_SERVICE = "com.carloseduardo.homeview-sentinel.test"

pytestmark = pytest.mark.skipif(shutil.which("security") is None, reason="needs macOS Keychain")

ALL_ITEMS = [
    secrets.DVR_USER,
    secrets.DVR_PASSWORD,
    secrets.NTFY_PHONE_PASSWORD,
    secrets.NTFY_NOTIFIER_TOKEN,
    "item",
    *(secrets.mqtt_item(user) for user in secrets.MQTT_USERS),
]


@pytest.fixture(autouse=True)
def clean_keychain():
    yield
    for name in ALL_ITEMS:
        secrets.delete(name, TEST_SERVICE)


@pytest.mark.parametrize(
    "value",
    ["simple", "a@b:c/d#e", 'quote " and back\\slash', "acentuação é", "414243", ""],
)
def test_set_and_get_round_trip(value):
    secrets.set("item", value, TEST_SERVICE)
    assert secrets.get("item", TEST_SERVICE) == value


def test_set_replaces_the_old_value():
    secrets.set("item", "old", TEST_SERVICE)
    secrets.set("item", "new", TEST_SERVICE)
    assert secrets.get("item", TEST_SERVICE) == "new"


def test_missing_item():
    assert secrets.get("item", TEST_SERVICE) is None
    with pytest.raises(secrets.KeychainError, match="make secrets"):
        secrets.require("item", TEST_SERVICE)


def test_delete():
    secrets.set("item", "x", TEST_SERVICE)
    assert secrets.delete("item", TEST_SERVICE) is True
    assert secrets.get("item", TEST_SERVICE) is None
    assert secrets.delete("item", TEST_SERVICE) is False


def test_value_never_on_the_command_line(monkeypatch):
    seen = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        seen.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    secrets.set("item", "s3cret-value", TEST_SERVICE)
    assert not any("s3cret-value" in " ".join(args) for args in seen)


def test_generated_credentials_have_the_expected_shape():
    assert re.fullmatch(r"[A-Za-z0-9]{32}", secrets.generate_password())
    assert re.fullmatch(r"tk_[a-z0-9]{29}", secrets.generate_ntfy_token())
    assert re.fullmatch(r"([a-z2-9]{5}-){3}[a-z2-9]{5}", secrets.generate_phone_password())


def run_setup(answers, secret_answers):
    answers, secret_answers, said = iter(answers), iter(secret_answers), []
    secrets.setup(
        ask=lambda _: next(answers),
        ask_secret=lambda _: next(secret_answers),
        say=said.append,
        service=TEST_SERVICE,
    )
    return said


def test_setup_stores_dvr_login_and_generates_the_rest():
    said = run_setup(["sentinel"], ["", "a@b:c/d#e", "typo", "a@b:c/d#e", "a@b:c/d#e"])
    assert "The password cannot be empty." in said
    assert "The passwords do not match." in said
    assert secrets.get(secrets.DVR_USER, TEST_SERVICE) == "sentinel"
    assert secrets.get(secrets.DVR_PASSWORD, TEST_SERVICE) == "a@b:c/d#e"
    for user in secrets.MQTT_USERS:
        assert secrets.get(secrets.mqtt_item(user), TEST_SERVICE)
    assert secrets.get(secrets.NTFY_NOTIFIER_TOKEN, TEST_SERVICE).startswith("tk_")
    phone = secrets.get(secrets.NTFY_PHONE_PASSWORD, TEST_SERVICE)
    assert any(phone in line for line in said)
    assert not any("a@b:c/d#e" in line for line in said)


def test_setup_again_changes_dvr_password_and_keeps_generated_ones():
    run_setup(["sentinel"], ["old", "old"])
    token = secrets.get(secrets.NTFY_NOTIFIER_TOKEN, TEST_SERVICE)
    broker = secrets.get(secrets.mqtt_item("detector"), TEST_SERVICE)

    run_setup([""], ["new", "new"])  # empty answer keeps the stored user

    assert secrets.get(secrets.DVR_USER, TEST_SERVICE) == "sentinel"
    assert secrets.get(secrets.DVR_PASSWORD, TEST_SERVICE) == "new"
    assert secrets.get(secrets.NTFY_NOTIFIER_TOKEN, TEST_SERVICE) == token
    assert secrets.get(secrets.mqtt_item("detector"), TEST_SERVICE) == broker
