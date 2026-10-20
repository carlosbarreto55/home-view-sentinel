"""The control agent's loop: requests in, the mode, the components, the state out.

Every second the agent:

1. takes ``request.json`` if there is one and applies it to the mode, or
   refuses it (the mode does not change), and reports its id;
2. turns off a timed watch that is over (a wake from sleep is just a tick where
   the clock jumped) and saves the mode whenever it changes;
3. asks the supervisor to have the components running or not; starting and
   stopping happen on the supervisor's own thread, so requests keep being
   answered meanwhile;
4. keeps the Mac awake while watching, and reads the power source every 30 s;
5. writes ``state.json`` when it changed, and at least every 5 seconds.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from sentinel.control import modes
from sentinel.control.awake import POWER_POLL_SECONDS, KeepAwake
from sentinel.control.contract import AC, ControlState, InvalidRequest, Request
from sentinel.control.files import ControlFiles
from sentinel.control.follow import Follower, Run
from sentinel.control.modes import Mode, ModeMachine, Refused

log = logging.getLogger("sentinel.control")

RETRY_SECONDS = 30
Clock = Callable[[], datetime]


class Supervisor:
    """Has the components started or stopped to match the mode, one action at a time.

    ``start`` and ``stop`` are idempotent (already loaded agents are left alone),
    so the first action after the agent starts adopts whatever is running."""

    def __init__(
        self,
        start: Callable[[], None],
        stop: Callable[[], list[str]],
        clock: Clock,
    ):
        self._start = start
        self._stop = stop
        self.clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._wanted: bool | None = None
        self._done: bool | None = None  # what the last successful action achieved
        self._busy = False
        self._started_at: datetime | None = None
        self._error: str | None = None
        self._failed_at: datetime | None = None

    def want(self, watching: bool) -> None:
        with self._lock:
            if watching == self._wanted:
                return
            self._wanted = watching
            self._error = self._failed_at = None
            self._started_at = self.clock() if watching else None
        self._wake.set()

    def run(self) -> Run:
        with self._lock:
            return Run(
                wanted=bool(self._wanted),
                busy=self._busy or (self._wanted is not None and self._wanted != self._done),
                started_at=self._started_at,
                error=self._error,
            )

    def step(self) -> None:
        """Do what is needed now, if anything (blocks while it starts or stops)."""
        with self._lock:
            wanted = self._wanted
            if wanted is None or wanted == self._done:
                return
            if self._failed_at is not None and self.clock() - self._failed_at < timedelta(
                seconds=RETRY_SECONDS
            ):
                return
            self._busy = True
        try:
            if wanted:
                log.info("starting the components")
                self._start()
                problem = None
            else:
                log.info("stopping the components")
                left = self._stop()
                problem = f"Não pararam: {', '.join(left)}" if left else None
        except Exception as error:  # launchctl or the bus failed; try again later
            log.exception("could not %s the components", "start" if wanted else "stop")
            problem = f"Não foi possível {'iniciar' if wanted else 'parar'} o Sentinel: {error}"
        with self._lock:
            self._busy = False
            if self._wanted != wanted:
                return  # the mode changed meanwhile; the next step catches up
            if problem is None:
                self._done = wanted
                self._error = self._failed_at = None
                log.info("the components are %s", "started" if wanted else "stopped")
            else:
                log.error("%s", problem)
                self._done = None
                self._error, self._failed_at = problem, self.clock()

    def loop(self, stopping: threading.Event) -> None:
        while not stopping.is_set():
            self._wake.wait(1)
            self._wake.clear()
            if not stopping.is_set():
                self.step()


class Agent:
    def __init__(
        self,
        files: ControlFiles,
        machine: ModeMachine,
        supervisor: Supervisor,
        follower: Follower,
        keep_awake: KeepAwake,
        power_source: Callable[[], str],
        clock: Clock,
    ):
        self.files = files
        self.machine = machine
        self.supervisor = supervisor
        self.follower = follower
        self.keep_awake = keep_awake
        self.power_source = power_source
        self.clock = clock
        self.applied: str | None = None
        self.refused: str | None = None
        self.refusal: str | None = None
        self.power = AC
        self._power_read_at: datetime | None = None
        self._saved: Mode | None = None

    def tick(self) -> None:
        now = self.clock()
        request = self.files.take_request()
        if request is not None:
            self._handle(request)
        self.machine.tick()
        mode = self.machine.mode
        if mode != self._saved:
            modes.save(self.files.mode_path, mode)
            self._saved = mode
        self.supervisor.want(mode.watching)
        if mode.watching:
            self.keep_awake.hold()
        else:
            self.keep_awake.release()
        if self._power_read_at is None or not (
            timedelta(0) <= now - self._power_read_at < timedelta(seconds=POWER_POLL_SECONDS)
        ):
            self.power, self._power_read_at = self.power_source(), now
        self.files.write_state(self.state(now))

    def _handle(self, request: Request | InvalidRequest) -> None:
        if isinstance(request, InvalidRequest):
            self._refuse(request.request_id, f"Pedido inválido ({request.reason})")
            return
        try:
            mode = self.machine.request(request.mode, request.hours)
        except Refused as error:
            self._refuse(request.id, str(error))
            return
        log.info("request %s applied: %s", request.id, mode.to_dict())
        self.applied, self.refusal = request.id, None

    def _refuse(self, request_id: str, reason: str) -> None:
        log.warning("request %s refused: %s", request_id, reason)
        self.refused, self.refusal = request_id, reason

    def state(self, now: datetime) -> ControlState:
        mode = self.machine.mode
        phase, problem = self.follower.phase(self.supervisor.run(), now)
        return ControlState(
            written_at=now,
            mode=mode.name,
            until=mode.until,
            phase=phase,
            detail=problem or self.refusal,
            power=self.power,
            kept_awake=self.keep_awake.held and self.power == AC,
            cameras=self.follower.camera_states(),
            applied_request=self.applied,
            refused_request=self.refused,
            last_alert=self.follower.last_alert(),
        )
