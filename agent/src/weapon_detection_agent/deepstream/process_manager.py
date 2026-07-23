"""Supervises a DeepStream child process (IP-06 T-72/T-73, FS-04 Phase 1).

``DeepStreamProcessManager`` implements the existing ``OperationalComponent`` protocol (IP-05 T-60)
unchanged — no new lifecycle abstraction, no new locking primitive. The coordinator starts/stops it
exactly as it would any other operational component.

**Genericness requirement (binding, FS-04 §8 amendment).** This module knows only six things: an
executable path, an application config path, a working directory, a graceful-stop timeout, a
restart policy, and a log path. It contains **no** model-specific name, dimension, output-tensor
name, parser name, or class count anywhere — and it never reads ``WDA_DEEPSTREAM_MODEL_PROFILE``.
Which model actually runs is decided entirely by the *content* of the configured application config
file, assembled at deployment time by ``deploy-engine.sh``/``install.sh`` — never by a branch in
this class. A static test (``tests/test_deepstream_process_manager.py``) enforces this
mechanically.

The process is launched with an explicit argv — never ``shell=True``, never a shell string — via an
injectable subprocess factory (default :func:`asyncio.create_subprocess_exec`) so unit tests never
need a real ``deepstream-app``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from weapon_detection_agent.deepstream.errors import DeepStreamAlreadyRunningError

if TYPE_CHECKING:
    from weapon_detection_agent.config.settings import AgentSettings

_LOGGER = logging.getLogger("weapon_detection_agent.deepstream.process_manager")

# Injectable subprocess launcher. The default is asyncio.create_subprocess_exec itself (argv-based,
# never a shell); tests inject a fake with the same call shape to avoid a real DeepStream process.
SubprocessFactory = Callable[..., Coroutine[Any, Any, "asyncio.subprocess.Process"]]


class DeepStreamProcessManager:
    """Owns exactly one DeepStream child process: launch, graceful stop, forced kill, exit
    detection.

    Construct with the six DeepStream settings (never a model profile — see module docstring) and
    an optional injected subprocess factory. Safe to construct without DeepStream installed;
    nothing is touched until :meth:`start` is called.
    """

    def __init__(
        self,
        *,
        executable_path: Path,
        config_path: Path,
        working_directory: Path,
        stop_timeout_seconds: float,
        restart_policy: str,
        log_path: Path,
        subprocess_factory: SubprocessFactory = asyncio.create_subprocess_exec,
    ) -> None:
        self._executable_path = Path(executable_path)
        self._config_path = Path(config_path)
        self._working_directory = Path(working_directory)
        self._stop_timeout_seconds = stop_timeout_seconds
        self._restart_policy = restart_policy
        self._log_path = Path(log_path)
        self._subprocess_factory = subprocess_factory

        self._process: asyncio.subprocess.Process | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._log_file: IO[bytes] | None = None

    # --- OperationalComponent protocol (IP-05 T-60) ---------------------------------------------

    @property
    def name(self) -> str:
        """A safe, static component identifier — never derived from a path or argv."""
        return "deepstream"

    async def start(self) -> None:
        """Launch DeepStream as a direct child process (explicit argv, never a shell).

        Raises :class:`DeepStreamAlreadyRunningError` if a process is already tracked as running —
        no duplicate is ever spawned (FS-04 Behavior #6).
        """
        if self._process is not None:
            raise DeepStreamAlreadyRunningError("DeepStream is already running")

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self._log_path, "ab")  # noqa: SIM115 - closed in _cleanup/stop, not here

        argv = (str(self._executable_path), "-c", str(self._config_path))
        try:
            process = await self._subprocess_factory(
                *argv,
                cwd=str(self._working_directory),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except BaseException:
            log_file.close()
            raise

        self._process = process
        self._log_file = log_file
        self._stopping = False
        self._watcher_task = asyncio.create_task(
            self._watch(process), name="deepstream-process-watcher"
        )
        _LOGGER.info("deepstream_started", extra={"component": self.name, "pid": process.pid})

    async def stop(self) -> None:
        """Stop DeepStream: SIGTERM, wait up to the configured timeout, then SIGKILL; always reap.

        A no-op (idempotent) if nothing is tracked as running (FS-04 Behavior #7).
        """
        process = self._process
        if process is None:
            return

        self._stopping = True
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGTERM)

        try:
            await asyncio.wait_for(process.wait(), timeout=self._stop_timeout_seconds)
        except asyncio.TimeoutError:
            _LOGGER.warning("deepstream_stop_timeout_sigkill", extra={"component": self.name})
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

        watcher = self._watcher_task
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

        self._cleanup()
        _LOGGER.info("deepstream_stopped", extra={"component": self.name})

    # --- Observability (safe; no credential material, never the raw Process object) -------------

    @property
    def is_running(self) -> bool:
        """Whether a DeepStream process is currently tracked as running."""
        return self._process is not None

    @property
    def pid(self) -> int | None:
        """The tracked process's PID, or ``None`` when nothing is running."""
        return self._process.pid if self._process is not None else None

    # --- Unexpected-exit detection (IP-06 T-73) --------------------------------------------------

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        """Await the process's exit; log and clean up if it exits without :meth:`stop` first.

        ``asyncio.CancelledError`` (this task cancelled by :meth:`stop`) propagates untouched — the
        same cancellation contract every other Agent background task already follows.
        """
        return_code = await process.wait()
        if self._stopping:
            # stop() is already handling this exit; avoid a redundant unexpected-exit log/cleanup.
            return
        _LOGGER.warning(
            "deepstream_unexpected_exit",
            extra={"component": self.name, "return_code": return_code},
        )
        self._cleanup()

    def _cleanup(self) -> None:
        self._process = None
        self._watcher_task = None
        self._stopping = False
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


def default_deepstream_components_factory(
    settings: AgentSettings,
) -> tuple[DeepStreamProcessManager, ...]:
    """Build the real ``DeepStreamProcessManager`` from settings, if enabled (IP-06 T-74).

    Used only by the real production entrypoint (``main.py``) — never the default the test suite or
    the general :func:`~weapon_detection_agent.app.create_app` default exercises (that default stays
    ``()``, unchanged, so no existing test needs to know DeepStream exists).

    Returns an empty tuple when ``settings.deepstream_enabled`` is ``False`` (the default) — no
    ``DeepStreamProcessManager`` is even constructed, so the supervisor registers no component at
    all. A disabled feature behaves as if it were not wired in, not merely as an idle component.
    """
    if not settings.deepstream_enabled:
        return ()
    return (
        DeepStreamProcessManager(
            executable_path=settings.deepstream_executable_path,
            config_path=settings.deepstream_config_path,
            working_directory=settings.deepstream_working_directory,
            stop_timeout_seconds=settings.deepstream_stop_timeout_seconds,
            restart_policy=settings.deepstream_restart_policy,
            log_path=settings.deepstream_log_path,
        ),
    )
