"""The ntfy server launcher: self-hosted push server in a container.

``python -m sentinel.notifier.ntfy_server`` (run by launchd) runs ntfy with:

- ``upstream-base-url`` https://ntfy.sh, so ntfy.sh wakes the iPhone through
  Apple's push service while the message and the snapshot stay on the Mac;
- ``base-url`` at the Mac's Tailscale address (``notifications.ntfy_base_url``),
  which Tailscale Serve forwards to the port published on 127.0.0.1;
- access denied by default: the phone user may only read the topic, the
  notifier's token may only write to it.

ntfy is configured entirely through environment variables (spike 2.5), passed to
the container by name. Users are declared with bcrypt hashes, made here from the
Keychain values at every start, so no password is stored in clear.
"""

from __future__ import annotations

import logging
import secrets as random
import sys
from collections.abc import Callable
from pathlib import Path

import bcrypt

from sentinel import config as config_module
from sentinel import paths, secrets
from sentinel.config import Notifications
from sentinel.runtime import EXIT_DONE, EXIT_RESTART, container, logs

log = logging.getLogger("sentinel.ntfy")

IMAGE = "docker.io/binwiederhier/ntfy:v2.14.0"
CONTAINER_NAME = "sentinel-ntfy"
PORT = 2586  # on the Mac's loopback; Tailscale Serve forwards HTTPS to it
UPSTREAM = "https://ntfy.sh"
DATA_INSIDE = "/var/lib/ntfy"
NOTIFIER_USER = "notifier"


def password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def environment(
    settings: Notifications,
    phone_password: str,
    notifier_token: str,
    hash_password: Callable[[str], str] = password_hash,
) -> dict[str, str]:
    """ntfy's whole configuration as environment variables."""
    topic = settings.ntfy_topic
    # The notifier authenticates with its token only; its password is random and unused.
    notifier_password = random.token_urlsafe(24)
    return {
        "NTFY_BASE_URL": settings.ntfy_base_url or "",
        "NTFY_UPSTREAM_BASE_URL": UPSTREAM,
        "NTFY_LISTEN_HTTP": ":80",
        "NTFY_BEHIND_PROXY": "true",
        "NTFY_AUTH_DEFAULT_ACCESS": "deny-all",
        "NTFY_AUTH_FILE": f"{DATA_INSIDE}/user.db",
        "NTFY_CACHE_FILE": f"{DATA_INSIDE}/cache.db",
        "NTFY_ATTACHMENT_CACHE_DIR": f"{DATA_INSIDE}/attachments",
        "NTFY_AUTH_USERS": ",".join(
            [
                f"{secrets.NTFY_PHONE_USER}:{hash_password(phone_password)}:user",
                f"{NOTIFIER_USER}:{hash_password(notifier_password)}:user",
            ]
        ),
        "NTFY_AUTH_ACCESS": ",".join(
            [f"{secrets.NTFY_PHONE_USER}:{topic}:ro", f"{NOTIFIER_USER}:{topic}:wo"]
        ),
        "NTFY_AUTH_TOKENS": f"{NOTIFIER_USER}:{notifier_token}:sentinel-notifier",
    }


def container_spec(data_dir: Path, env_names: tuple[str, ...]) -> container.ContainerSpec:
    return container.ContainerSpec(
        name=CONTAINER_NAME,
        image=IMAGE,
        command=("serve",),
        env_names=env_names,
        publish=(f"127.0.0.1:{PORT}:80",),
        mounts=((data_dir, DATA_INSIDE),),
        cpus=1,
        memory="256M",  # container 1.5 refuses less than 200 MiB
    )


def run(
    settings: Notifications,
    data_dir: Path,
    read_secret: Callable[[str], str | None] = secrets.get,
    run_container: Callable[..., container.RunResult] = container.run,
    wait_for_system: Callable[[], bool] = container.wait_for_system,
    add_secrets: Callable[..., None] = lambda *values: None,
) -> int:
    if not settings.ntfy_base_url:
        log.error(
            "notifications.ntfy_base_url is not set in config/sentinel.yaml; set it to the "
            "Mac's Tailscale address (https://<mac>.<tailnet>.ts.net)"
        )
        return EXIT_DONE
    phone_password = read_secret(secrets.NTFY_PHONE_PASSWORD)
    token = read_secret(secrets.NTFY_NOTIFIER_TOKEN)
    if not phone_password or not token:
        log.error("the ntfy credentials are not in the Keychain; run `make secrets`")
        return EXIT_DONE
    add_secrets(phone_password, token)
    if not wait_for_system():
        log.error("the container system is not running; launchd will try again")
        return EXIT_RESTART
    data_dir.mkdir(parents=True, exist_ok=True)
    env = environment(settings, phone_password, token)
    log.info(
        "starting ntfy on 127.0.0.1:%d for %s (topic %s)",
        PORT,
        settings.ntfy_base_url,
        settings.ntfy_topic,
    )
    outcome = run_container(
        container_spec(data_dir, tuple(env)), env, on_line=lambda line: log.info("ntfy: %s", line)
    )
    return EXIT_DONE if outcome.stopped else EXIT_RESTART


def main() -> int:
    redacting = logs.setup("ntfy")
    try:
        settings = config_module.load(config_module.DEFAULT_PATH).notifications
    except config_module.ConfigError as error:
        log.error("%s", error)
        return EXIT_DONE
    try:
        return run(settings, paths.ntfy_dir(), add_secrets=redacting.add)
    except secrets.KeychainError as error:
        log.error("%s", error)
        return EXIT_RESTART


if __name__ == "__main__":
    sys.exit(main())
