from dataclasses import replace

import bcrypt

from sentinel import secrets
from sentinel.config import Notifications
from sentinel.notifier import ntfy_server
from sentinel.runtime import EXIT_DONE, EXIT_RESTART
from sentinel.runtime.container import RunResult

SETTINGS = Notifications(ntfy_topic="sentinel", ntfy_base_url="https://mac.tail1234.ts.net")
PHONE = "abcde-fghij-kmnpq-rstuv"
TOKEN = "tk_" + "a" * 29
KEYCHAIN = {secrets.NTFY_PHONE_PASSWORD: PHONE, secrets.NTFY_NOTIFIER_TOKEN: TOKEN}


def test_environment_denies_by_default_and_scopes_each_user_to_the_topic():
    env = ntfy_server.environment(SETTINGS, PHONE, TOKEN)
    assert env["NTFY_BASE_URL"] == "https://mac.tail1234.ts.net"
    assert env["NTFY_UPSTREAM_BASE_URL"] == "https://ntfy.sh"
    assert env["NTFY_AUTH_DEFAULT_ACCESS"] == "deny-all"
    assert env["NTFY_BEHIND_PROXY"] == "true"
    assert env["NTFY_AUTH_ACCESS"] == "iphone:sentinel:ro,notifier:sentinel:wo"
    assert env["NTFY_AUTH_TOKENS"] == f"notifier:{TOKEN}:sentinel-notifier"
    assert env["NTFY_ATTACHMENT_CACHE_DIR"].startswith("/var/lib/ntfy/")


def test_users_are_declared_with_bcrypt_hashes_only():
    env = ntfy_server.environment(SETTINGS, PHONE, TOKEN)
    users = dict(
        (name, (hashed, role))
        for name, hashed, role in (u.rsplit(":", 2) for u in env["NTFY_AUTH_USERS"].split(","))
    )
    assert set(users) == {"iphone", "notifier"}
    assert all(role == "user" for _, role in users.values())
    assert bcrypt.checkpw(PHONE.encode(), users["iphone"][0].encode())
    assert PHONE not in env["NTFY_AUTH_USERS"]


def test_secrets_reach_the_container_by_name_only(tmp_path):
    started = []
    code = ntfy_server.run(
        SETTINGS,
        tmp_path,
        read_secret=KEYCHAIN.get,
        run_container=lambda spec, env, on_line: started.append((spec, env)) or RunResult(False, 1),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_RESTART
    spec, env = started[0]
    command = " ".join(spec.run_command())
    assert TOKEN not in command and "$2b$" not in command
    assert "--env NTFY_AUTH_TOKENS" in command
    assert "--publish 127.0.0.1:2586:80" in command
    assert command.endswith("binwiederhier/ntfy:v2.14.0 serve")
    assert env["NTFY_AUTH_TOKENS"].endswith(f"{TOKEN}:sentinel-notifier")


def test_without_the_tailscale_address_it_waits_for_the_config(tmp_path):
    started = []
    code = ntfy_server.run(
        replace(SETTINGS, ntfy_base_url=None),
        tmp_path,
        read_secret=KEYCHAIN.get,
        run_container=lambda *a, **k: started.append(a),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_DONE and started == []


def test_without_credentials_it_waits_for_make_secrets(tmp_path):
    code = ntfy_server.run(
        SETTINGS, tmp_path, read_secret={}.get, run_container=None, wait_for_system=lambda: True
    )
    assert code == EXIT_DONE


def test_stopped_on_purpose_is_not_restarted(tmp_path):
    code = ntfy_server.run(
        SETTINGS,
        tmp_path,
        read_secret=KEYCHAIN.get,
        run_container=lambda spec, env, on_line: RunResult(True, 0),
        wait_for_system=lambda: True,
    )
    assert code == EXIT_DONE
