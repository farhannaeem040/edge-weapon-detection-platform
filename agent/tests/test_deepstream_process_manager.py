"""Unit tests for ``DeepStreamProcessManager`` (IP-06 T-72/T-73/T-75, FS-04 Phase 1).

Every test uses a fake subprocess factory — no real ``deepstream-app``, GPU, or Jetson required, so
these run in the default fast suite. A small number of tests build a real
``OperationalStateCoordinator``/``AgentRuntimeSupervisor`` (reusing the fakes already defined in
``test_agent_runtime_supervisor``) with a real ``DeepStreamProcessManager`` as the operational
component, to prove the integration — not just the class in isolation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from pathlib import Path

import pytest

from test_agent_runtime_supervisor import (  # noqa: E402 - shared test doubles, not production code
    SECRET_1,
    FakeBackendClient,
    FakeMonitor,
    _activation_result,
    _build,
    _identity,
)
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.deepstream import process_manager as process_manager_module
from weapon_detection_agent.deepstream.errors import DeepStreamAlreadyRunningError
from weapon_detection_agent.deepstream.process_manager import (
    DeepStreamProcessManager,
    default_deepstream_components_factory,
)
from weapon_detection_agent.persistence.models import OperationalState
from weapon_detection_agent.runtime.startup import default_components_factory
from weapon_detection_agent.runtime.supervisor import StartupBranch

# --- Fake subprocess -----------------------------------------------------------------------------


class _FakeProcess:
    """A controllable stand-in for ``asyncio.subprocess.Process``."""

    def __init__(
        self, pid: int, *, exits_immediately_with: int | None = None, ignores_sigterm: bool = False
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.sigterm_count = 0
        self.kill_count = 0
        self._ignores_sigterm = ignores_sigterm
        self._exited = asyncio.Event()
        if exits_immediately_with is not None:
            self.returncode = exits_immediately_with
            self._exited.set()

    def send_signal(self, sig: int) -> None:
        assert sig == signal.SIGTERM
        self.sigterm_count += 1
        if not self._ignores_sigterm:
            self.returncode = 0
            self._exited.set()

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode


class _FakeSubprocessFactory:
    """Records every call and hands back a configurable ``_FakeProcess``."""

    def __init__(
        self, *, exits_immediately_with: int | None = None, ignores_sigterm: bool = False
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self._exits_immediately_with = exits_immediately_with
        self._ignores_sigterm = ignores_sigterm
        self._next_pid = 1000
        self.last_process: _FakeProcess | None = None

    async def __call__(self, *argv: str, **kwargs: object) -> _FakeProcess:
        self.calls.append((argv, kwargs))
        pid = self._next_pid
        self._next_pid += 1
        process = _FakeProcess(
            pid,
            exits_immediately_with=self._exits_immediately_with,
            ignores_sigterm=self._ignores_sigterm,
        )
        self.last_process = process
        return process


def _manager(
    tmp_path: Path,
    factory: _FakeSubprocessFactory,
    *,
    config_path: Path | None = None,
    stop_timeout_seconds: float = 5.0,
) -> DeepStreamProcessManager:
    return DeepStreamProcessManager(
        executable_path=Path("/usr/bin/deepstream-app"),
        config_path=config_path if config_path is not None else tmp_path / "deepstream-app.txt",
        working_directory=tmp_path,
        stop_timeout_seconds=stop_timeout_seconds,
        restart_policy="none",
        log_path=tmp_path / "logs" / "deepstream.log",
        subprocess_factory=factory,
    )


# --- 1. start() launches the exact expected argv, no shell -----------------------------------


def test_start_launches_explicit_argv_no_shell(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        config = tmp_path / "deepstream-app.txt"
        manager = _manager(tmp_path, factory, config_path=config)

        await manager.start()

        assert len(factory.calls) == 1
        argv, kwargs = factory.calls[0]
        assert argv == (str(Path("/usr/bin/deepstream-app")), "-c", str(config))
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        assert kwargs["stderr"] == asyncio.subprocess.STDOUT
        assert "shell" not in kwargs
        assert manager.is_running is True
        assert manager.pid == factory.last_process.pid  # type: ignore[union-attr]

        await manager.stop()

    asyncio.run(_scenario())


# --- 2. Duplicate start is rejected -------------------------------------------------------------


def test_duplicate_start_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        manager = _manager(tmp_path, factory)
        await manager.start()

        with pytest.raises(DeepStreamAlreadyRunningError):
            await manager.start()

        assert len(factory.calls) == 1  # no second process spawned
        await manager.stop()

    asyncio.run(_scenario())


# --- 3. Normal graceful stop ----------------------------------------------------------------


def test_stop_sends_sigterm_and_reaps_without_sigkill(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()  # honors SIGTERM by default
        manager = _manager(tmp_path, factory)
        await manager.start()

        await manager.stop()

        process = factory.last_process
        assert process is not None
        assert process.sigterm_count == 1
        assert process.kill_count == 0
        assert manager.is_running is False
        assert manager.pid is None

    asyncio.run(_scenario())


# --- 4. Forced kill after timeout -------------------------------------------------------------


def test_stop_sends_sigkill_only_after_timeout(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory(ignores_sigterm=True)
        manager = _manager(tmp_path, factory, stop_timeout_seconds=0.05)
        await manager.start()

        await manager.stop()

        process = factory.last_process
        assert process is not None
        assert process.sigterm_count == 1  # SIGTERM was tried first
        assert process.kill_count == 1  # SIGKILL only after the timeout
        assert manager.is_running is False

    asyncio.run(_scenario())


# --- 5. Idempotent stop --------------------------------------------------------------------------


def test_stop_when_nothing_running_is_a_noop(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        manager = _manager(tmp_path, factory)

        await manager.stop()  # no start() first

        assert factory.calls == []
        assert manager.is_running is False

    asyncio.run(_scenario())


# --- 6. Unexpected exit is detected, logged, and does not propagate ------------------------------


def test_unexpected_exit_is_detected_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory(exits_immediately_with=137)
        manager = _manager(tmp_path, factory)

        with caplog.at_level(
            logging.WARNING, logger="weapon_detection_agent.deepstream.process_manager"
        ):
            await manager.start()
            # Give the watcher task a turn to observe the already-exited process.
            for _ in range(5):
                await asyncio.sleep(0)

        assert manager.is_running is False
        unexpected = [r for r in caplog.records if r.message == "deepstream_unexpected_exit"]
        assert len(unexpected) == 1
        assert unexpected[0].return_code == 137
        assert unexpected[0].component == "deepstream"

    asyncio.run(_scenario())


# --- 7. Cancelling stop() propagates cleanly, no hang -------------------------------------------


def test_cancelling_stop_propagates_cancelled_error(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory(ignores_sigterm=True)
        manager = _manager(tmp_path, factory, stop_timeout_seconds=5.0)
        await manager.start()

        stop_task = asyncio.create_task(manager.stop())
        await asyncio.sleep(0)  # let it start awaiting process.wait()
        stop_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await stop_task

    asyncio.run(_scenario())


# --- 8. Log lines never carry more than safe fields ----------------------------------------------


def test_log_lines_carry_only_safe_fields(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        manager = _manager(tmp_path, factory)

        with caplog.at_level(
            logging.INFO, logger="weapon_detection_agent.deepstream.process_manager"
        ):
            await manager.start()
            await manager.stop()

        safe_keys = {"component", "pid", "return_code"}
        # "message" is a standard LogRecord attribute the logging machinery populates when a record
        # is formatted (caplog does this) — not something application code adds via `extra=`.
        default_keys = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
            "message"
        }
        for record in caplog.records:
            extra_keys = set(record.__dict__.keys()) - default_keys
            assert extra_keys <= safe_keys
            assert "argv" not in record.__dict__
            assert "config_path" not in record.__dict__

    asyncio.run(_scenario())


# --- 9. Genericness: no model-specific token anywhere in the module's source --------------------


def test_process_manager_source_has_no_model_specific_token() -> None:
    # These must never appear anywhere in the file — not even in a comment/docstring, since even
    # documenting a specific model's dimensions here would be a genericness leak. Deliberately does
    # NOT forbid the bare word "profile" or "deepstream_model_profile": the module's docstring
    # legitimately *explains* why that setting is never read (see the structural check below, which
    # verifies the constructor accepts no such parameter — the real enforcement).
    source = inspect.getsource(process_manager_module).lower()
    forbidden = (
        "yolov4",
        "yolo_v4",
        "640",
        "batchednms",
        "nvdsinferparsecustombatchednmstlt",
        "num-detected-classes",
    )
    for token in forbidden:
        assert token not in source, f"process_manager.py must not reference {token!r}"


def test_process_manager_never_reads_model_profile(tmp_path: Path) -> None:
    factory = _FakeSubprocessFactory()
    manager = _manager(tmp_path, factory)

    # No attribute, public or private, may mention "profile" or "enabled".
    for attr_name in dir(manager):
        assert "profile" not in attr_name.lower()
        assert "enabled" not in attr_name.lower()

    constructor_params = inspect.signature(DeepStreamProcessManager.__init__).parameters
    assert "profile" not in "".join(constructor_params).lower()
    assert "enabled" not in "".join(constructor_params).lower()
    assert set(constructor_params) - {"self"} == {
        "executable_path",
        "config_path",
        "working_directory",
        "stop_timeout_seconds",
        "restart_policy",
        "log_path",
        "subprocess_factory",
    }


# --- 10. Profile/path swap changes selected config without touching process-manager code --------


def test_changing_config_path_changes_launched_argv_only(tmp_path: Path) -> None:
    """Two 'profiles' are simulated purely as two different config_path fixture files.

    DeepStreamProcessManager code is identical in both cases — only the constructor argument (which
    a deployment-time profile swap would change) differs. This is the amendment's central proof:
    the process manager makes zero decision based on which profile is active.
    """

    async def _scenario() -> None:
        profile_a_config = tmp_path / "profile-a" / "deepstream-app.txt"
        profile_b_config = tmp_path / "profile-b" / "deepstream-app.txt"
        profile_a_config.parent.mkdir(parents=True)
        profile_b_config.parent.mkdir(parents=True)
        profile_a_config.write_text("config-file=.../profile-a/infer-config.txt")
        profile_b_config.write_text("config-file=.../profile-b/infer-config.txt")

        factory_a = _FakeSubprocessFactory()
        factory_b = _FakeSubprocessFactory()
        manager_a = _manager(tmp_path, factory_a, config_path=profile_a_config)
        manager_b = _manager(tmp_path, factory_b, config_path=profile_b_config)

        await manager_a.start()
        await manager_b.start()

        argv_a = factory_a.calls[0][0]
        argv_b = factory_b.calls[0][0]
        assert argv_a[:2] == argv_b[:2] == (str(Path("/usr/bin/deepstream-app")), "-c")
        assert argv_a[2] != argv_b[2]  # only the config path differs
        assert argv_a[2] == str(profile_a_config)
        assert argv_b[2] == str(profile_b_config)

        await manager_a.stop()
        await manager_b.stop()

    asyncio.run(_scenario())


# --- 11. Settings-level factory wiring (IP-06 T-74) -----------------------------------------------


def test_default_deepstream_components_factory_disabled_by_default_registers_nothing() -> None:
    # WDA_DEEPSTREAM_ENABLED defaults to False — a fresh deployment never launches DeepStream
    # until an operator deliberately opts in, even through the real production wiring.
    settings = load_settings(backend_base_url="http://backend.local:5230")

    assert settings.deepstream_enabled is False
    assert default_deepstream_components_factory(settings) == ()


def test_default_deepstream_components_factory_builds_one_manager_when_enabled() -> None:
    settings = load_settings(backend_base_url="http://backend.local:5230", deepstream_enabled=True)

    components = default_deepstream_components_factory(settings)

    assert len(components) == 1
    manager = components[0]
    assert isinstance(manager, DeepStreamProcessManager)
    assert manager.name == "deepstream"


def test_default_components_factory_stays_empty() -> None:
    settings = load_settings(backend_base_url="http://backend.local:5230")

    assert default_components_factory(settings) == ()


# --- 12-15. Coordinator/supervisor integration (real DeepStreamProcessManager, fake subprocess) --


def _supervisor_with_deepstream(
    tmp_path: Path,
    factory: _FakeSubprocessFactory,
    **build_kwargs: object,
) -> tuple[object, DeepStreamProcessManager]:
    manager = _manager(tmp_path / "ds", factory)
    ctx = _build(tmp_path, components=(manager,), **build_kwargs)  # type: ignore[arg-type]
    return ctx, manager


def test_branch_a_first_activation_starts_deepstream(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        ctx, manager = _supervisor_with_deepstream(
            tmp_path,
            factory,
            key="keyid.ZZZ-deepstream-test-key-ZZZ",
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_1)),
        )

        await ctx.supervisor.startup()

        assert ctx.supervisor.startup_branch is StartupBranch.FIRST_ACTIVATION
        assert manager.is_running is True
        assert len(factory.calls) == 1

        await ctx.supervisor.shutdown()
        assert manager.is_running is False

    asyncio.run(_scenario())


def test_branch_c_locked_never_starts_deepstream(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        ctx, manager = _supervisor_with_deepstream(
            tmp_path,
            factory,
            prestore=_identity(secret=SECRET_1),
            lock_after_store=True,
            key=None,
        )

        await ctx.supervisor.startup()

        assert ctx.supervisor.startup_branch is StartupBranch.LOCKED_REACTIVATION_REQUIRED
        assert manager.is_running is False
        assert factory.calls == []  # never spawned at all

        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


def test_confirmed_rejection_stops_deepstream(tmp_path: Path) -> None:
    from weapon_detection_agent.validation.loop import CredentialValidationLoopResult

    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        rejection_result = CredentialValidationLoopResult.reactivation_required()
        rejection_monitor = FakeMonitor(returns=rejection_result)
        manager = _manager(tmp_path / "ds", factory)
        ctx = _build(
            tmp_path,
            key="keyid.ZZZ-deepstream-test-key-ZZZ",
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_1)),
            components=(manager,),  # type: ignore[arg-type]
            monitor=rejection_monitor,
        )

        await ctx.supervisor.startup()
        assert manager.is_running is True

        # Let the monitor task run to completion and drive the coordinator's lock.
        for _ in range(10):
            await asyncio.sleep(0)

        assert manager.is_running is False
        # The fake monitor returns a pre-built result directly, bypassing the real monitor's own
        # repository persistence step (T-59) — so the coordinator's in-memory state, not the
        # repository, is what proves the lock here (mirrors test_branch_c's own assertion style).
        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED

        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


def test_reactivation_after_lock_starts_deepstream_exactly_once_more(tmp_path: Path) -> None:
    async def _scenario() -> None:
        factory = _FakeSubprocessFactory()
        ctx, manager = _supervisor_with_deepstream(
            tmp_path,
            factory,
            prestore=_identity(secret=SECRET_1),
            lock_after_store=True,
            key="keyid.ZZZ-deepstream-reactivate-key-ZZZ",
            backend=FakeBackendClient(result=_activation_result(secret="ZZZ-new-secret-ZZZ")),
        )

        await ctx.supervisor.startup()

        assert ctx.supervisor.startup_branch is StartupBranch.REACTIVATION
        assert manager.is_running is True
        assert len(factory.calls) == 1  # exactly once, not twice, not zero

        await ctx.supervisor.shutdown()
        assert manager.is_running is False

    asyncio.run(_scenario())
