"""Keeping the Mac awake while watching, and the power source.

``caffeinate -s`` creates the "prevent system sleep" assertion, which macOS
honours only on the power adapter: on battery the Mac sleeps as its settings
say. Closing the lid, logging out and shutting down are not prevented. It runs
as the agent's child with ``-w <agent pid>``, so it also ends if the agent is
killed hard, and the assertion goes with it.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable

from sentinel.control.contract import AC, BATTERY

log = logging.getLogger(__name__)

POWER_POLL_SECONDS = 30

Spawn = Callable[[list[str]], subprocess.Popen]
Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _spawn(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class KeepAwake:
    def __init__(self, spawn: Spawn = _spawn, pid: int | None = None):
        self.spawn = spawn
        self.pid = pid if pid is not None else os.getpid()
        self.process: subprocess.Popen | None = None

    @property
    def held(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def hold(self) -> None:
        """Hold the assertion; starts ``caffeinate`` again if it ended."""
        if self.held:
            return
        if self.process is not None:
            log.warning("caffeinate ended (exit code %s); starting it again", self.process.poll())
        try:
            self.process = self.spawn(["/usr/bin/caffeinate", "-s", "-w", str(self.pid)])
        except OSError as error:
            log.error("could not run caffeinate: %s", error)
            self.process = None
            return
        log.info("keeping the Mac awake on power adapter")

    def release(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            log.info("no longer keeping the Mac awake")
        self.process = None


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)


def power_source(run: Runner = _run) -> str:
    """``ac`` or ``battery``, from ``pmset -g batt``'s "Now drawing from 'AC Power'".

    A Mac without a battery, or an answer that cannot be read, counts as AC:
    only battery changes what the Mac does while watching."""
    try:
        result = run(["/usr/bin/pmset", "-g", "batt"])
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("could not read the power source: %s", error)
        return AC
    return BATTERY if "'Battery Power'" in result.stdout else AC
