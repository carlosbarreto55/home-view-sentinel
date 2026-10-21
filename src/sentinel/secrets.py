"""Service credentials in the login Keychain, through the ``security`` CLI.

Each secret is a generic password with service ``com.carloseduardo.homeview-sentinel``
and the item name as the account. Values never appear on a command line: they are
written as hex through ``security -i`` on stdin and read back from ``-g`` output.

``python -m sentinel.secrets setup`` (``make secrets``) asks for the DVR user and
password and generates the broker and ntfy credentials that are still missing.
"""

from __future__ import annotations

import argparse
import getpass
import re
import secrets as random
import string
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

SERVICE = "com.carloseduardo.homeview-sentinel"

DVR_USER = "dvr-user"
DVR_PASSWORD = "dvr-password"
# One broker user per component; the password item is "mqtt-<user>".
MQTT_USERS = ("gateway", "detector", "notifier", "control", "admin")
NTFY_PHONE_USER = "iphone"
NTFY_PHONE_PASSWORD = "ntfy-phone-password"
NTFY_NOTIFIER_TOKEN = "ntfy-notifier-token"

_NOT_FOUND = 44  # security's exit status when no item matches
# "password: 0x<hex>  "<escaped>"", "password: 0x<hex>", "password: "<plain>"" or "password: "
_PASSWORD_LINE = re.compile(r'^password: (?:0x([0-9A-Fa-f]+)(?:\s.*)?|"(.*)")?\s*$')


class KeychainError(RuntimeError):
    pass


def mqtt_item(user: str) -> str:
    return f"mqtt-{user}"


def set(name: str, value: str, service: str = SERVICE) -> None:
    """Create or replace the item ``name``."""
    data = f"-X {value.encode().hex()}" if value else '-w ""'
    command = f"add-generic-password -U -s {service} -a {name} {data}\n"
    result = subprocess.run(
        ["security", "-i"], input=command, capture_output=True, text=True, check=False
    )
    # Interactive mode exits 0 even when a command fails, so check the item is there.
    if result.returncode != 0 or get(name, service) != value:
        raise KeychainError(f"could not store {name} in the Keychain: {result.stderr.strip()}")


def get(name: str, service: str = SERVICE) -> str | None:
    """The value of ``name``, or None when there is no such item."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", name, "-g"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == _NOT_FOUND:
        return None
    if result.returncode != 0:
        raise KeychainError(f"could not read {name} from the Keychain: {result.stderr.strip()}")
    for line in result.stderr.splitlines():
        match = _PASSWORD_LINE.match(line)
        if match:
            hex_value, plain = match.groups()
            if hex_value is not None:
                return bytes.fromhex(hex_value).decode()
            return plain or ""
    raise KeychainError(f"unexpected output reading {name} from the Keychain")


def require(name: str, service: str = SERVICE) -> str:
    """The value of ``name``; raises when it is missing (run ``make secrets``)."""
    value = get(name, service)
    if value is None:
        raise KeychainError(f"{name} is not in the Keychain; run `make secrets`")
    return value


def delete(name: str, service: str = SERVICE) -> bool:
    """Delete ``name``; returns whether it existed."""
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == _NOT_FOUND:
        return False
    if result.returncode != 0:
        raise KeychainError(f"could not delete {name} from the Keychain: {result.stderr.strip()}")
    return True


# --- generated credentials --------------------------------------------------


def generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def generate_phone_password() -> str:
    """Easy to type on the phone: four groups of five lowercase letters and digits."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(random.choice(alphabet) for _ in range(5)) for _ in range(4))


def generate_ntfy_token() -> str:
    """An ntfy access token: ``tk_`` and 29 lowercase letters and digits."""
    alphabet = string.ascii_lowercase + string.digits
    return "tk_" + "".join(random.choice(alphabet) for _ in range(29))


def setup(
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    say: Callable[[str], None] = print,
    service: str = SERVICE,
    stop_marker: Path | None = None,
) -> None:
    """Store the DVR credentials and generate the missing broker and ntfy ones.

    ``stop_marker`` is the gateway's marker of a rejected login, removed here.
    """
    current_user = get(DVR_USER, service) or "sentinel"
    user = ask(f"DVR user for Sentinel [{current_user}]: ").strip() or current_user
    while True:
        password = ask_secret("DVR password: ")
        if not password:
            say("The password cannot be empty.")
        elif ask_secret("DVR password again: ") != password:
            say("The passwords do not match.")
        else:
            break
    set(DVR_USER, user, service)
    set(DVR_PASSWORD, password, service)
    say("Stored the DVR user and password.")
    # A new login lets the gateway try again after a rejected one.
    if stop_marker is not None and stop_marker.exists():
        stop_marker.unlink()
        say("The gateway will try the new login at the next `make up`.")

    generated = []
    for user_name in MQTT_USERS:
        if get(mqtt_item(user_name), service) is None:
            set(mqtt_item(user_name), generate_password(), service)
            generated.append(mqtt_item(user_name))
    if get(NTFY_NOTIFIER_TOKEN, service) is None:
        set(NTFY_NOTIFIER_TOKEN, generate_ntfy_token(), service)
        generated.append(NTFY_NOTIFIER_TOKEN)
    phone_password = get(NTFY_PHONE_PASSWORD, service)
    if phone_password is None:
        phone_password = generate_phone_password()
        set(NTFY_PHONE_PASSWORD, phone_password, service)
        generated.append(NTFY_PHONE_PASSWORD)
    if generated:
        say("Generated: " + ", ".join(generated) + ".")
    say(f"ntfy login for the iPhone app: user {NTFY_PHONE_USER}, password {phone_password}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.secrets")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="store the DVR login and generate the other credentials")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            from sentinel.paths import gateway_stop_marker

            setup(stop_marker=gateway_stop_marker())
    except KeychainError as error:
        print(error, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
