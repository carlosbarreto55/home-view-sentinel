import base64
import hashlib
import stat

from sentinel import secrets
from sentinel.bus import broker
from sentinel.runtime import EXIT_DONE, EXIT_RESTART
from sentinel.runtime.container import RunResult

# Made by `mosquitto_passwd -b passwd alice wonderland` in eclipse-mosquitto:2.0.22.
MOSQUITTO_HASH = (
    "$7$101$gVtsVZDRJIulVXa6$LfXzagMMjPINFVOkElKRPXi5nOkSwA/WHdowH8u5CJ+8Hbx/"
    "WbMImIb7ntBVaBVK8KCwuOvigE4EP/mFTUOLFw=="
)


def test_hash_matches_mosquitto_passwd():
    salt = base64.b64decode("gVtsVZDRJIulVXa6")
    assert broker.password_hash("wonderland", salt) == MOSQUITTO_HASH


def test_hash_uses_a_new_salt_each_time():
    assert broker.password_hash("x") != broker.password_hash("x")
    _, _, iterations, salt, digest = broker.password_hash("x").split("$")
    assert iterations == "101"
    assert len(base64.b64decode(salt)) == 12
    assert base64.b64decode(digest) == hashlib.pbkdf2_hmac(
        "sha512", b"x", base64.b64decode(salt), 101, 64
    )


def keychain():
    return {secrets.mqtt_item(user): f"pw-{user}-0123456789" for user in secrets.MQTT_USERS}


def test_config_refuses_anonymous_and_keeps_no_password_in_clear(tmp_path):
    started = []
    code = broker.run(
        tmp_path,
        read_secret=keychain().get,
        run_container=lambda spec, on_line: started.append(spec) or RunResult(False, 1),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_RESTART
    conf = (tmp_path / "config" / "mosquitto.conf").read_text()
    assert "allow_anonymous false" in conf and "persistence true" in conf
    passwd = tmp_path / "config" / "passwd"
    text = passwd.read_text()
    assert [line.split(":")[0] for line in text.splitlines()] == list(secrets.MQTT_USERS)
    assert not any(pw in text for pw in keychain().values())
    assert stat.S_IMODE(passwd.stat().st_mode) == 0o600

    command = " ".join(started[0].run_command())
    assert "--publish 127.0.0.1:1883:1883" in command
    assert f"{tmp_path}/config:/mosquitto/config" in command
    assert f"{tmp_path}/data:/mosquitto/data" in command


def test_stopped_on_purpose_is_not_restarted(tmp_path):
    code = broker.run(
        tmp_path,
        read_secret=keychain().get,
        run_container=lambda spec, on_line: RunResult(True, 0),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_DONE


def test_missing_broker_passwords_wait_for_make_secrets(tmp_path):
    started = []
    code = broker.run(
        tmp_path,
        read_secret={}.get,
        run_container=lambda spec, on_line: started.append(spec),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_DONE and started == []
