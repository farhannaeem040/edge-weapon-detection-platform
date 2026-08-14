"""Focused tests for the Agent runtime supervisor and its startup decision tree (IP-05 T-61).

Every branch is exercised deterministically against a real temporary SQLite database (never the
Jetson database) with fakes for the Backend activation client, the validation client, the
operational components, and — where the running loop matters — the monitor. Async methods run via
``asyncio.run`` so no async-test plugin is needed, and no real interval ever elapses. Obvious
placeholder secrets are used and must never appear in a log, a ``repr``, or an exception, which the
secret-safety tests assert.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.activation.errors import (
    ActivationKeyMissingError,
    ActivationPersistenceError,
    DeviceIdentityMismatchError,
)
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationService
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.startup import default_validation_client_factory
from weapon_detection_agent.runtime.supervisor import (
    AgentRuntimeSupervisor,
    AgentStartupError,
    StartupBranch,
)
from weapon_detection_agent.validation.loop import (
    CredentialLockPersistenceError,
    CredentialValidationLoopResult,
)
from weapon_detection_agent.validation.models import CredentialValidationResult, IndeterminateReason

DEVICE_ID = "device-supervisor-11111111"
OTHER_DEVICE_ID = "device-supervisor-99999999"
BRANCH_ID = "branch-supervisor-001"
SECRET_1 = "ZZZ-supervisor-secret-one-must-never-appear-ZZZ"  # noqa: S105 - placeholder
SECRET_2 = "ZZZ-supervisor-secret-two-must-never-appear-ZZZ"  # noqa: S105 - placeholder
FAKE_KEY = "keyid.ZZZ-supervisor-activation-key-ZZZ"  # noqa: S105 - placeholder
SENTINEL = "ZZZ-supervisor-monitor-raw-text-ZZZ"  # noqa: S105 - placeholder
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


# --- Test doubles ------------------------------------------------------------------------------


class FakeBackendClient:
    """The activation Backend client — scripted result/error, call and close counters."""

    def __init__(
        self, *, result: ActivationResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.activate_calls = 0
        self.closed = False

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.activate_calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    async def aclose(self) -> None:
        self.closed = True


class FakeValidationClient:
    """The T-57 validation client — scripted result, call and close counters, no network."""

    def __init__(self, *, result: CredentialValidationResult | None = None) -> None:
        self._result = result if result is not None else CredentialValidationResult.valid(200)
        self.validate_calls = 0
        self.closed = False

    async def validate(
        self, device_id: str, shared_secret: SecretStr
    ) -> CredentialValidationResult:
        self.validate_calls += 1
        return self._result

    async def aclose(self) -> None:
        self.closed = True


class FakeComponent:
    """A deterministic operational component with start/stop counters and running state."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.start_count = 0
        self.stop_count = 0
        self.running = False

    async def start(self) -> None:
        self.start_count += 1
        self.running = True

    async def stop(self) -> None:
        self.stop_count += 1
        self.running = False


class FakeMonitor:
    """A controllable monitor: returns a terminal result, raises, or blocks until cancelled."""

    def __init__(
        self,
        *,
        returns: CredentialValidationLoopResult | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._returns = returns
        self._raises = raises
        self.run_count = 0

    async def run(self) -> CredentialValidationLoopResult:
        self.run_count += 1
        if self._raises is not None:
            raise self._raises
        if self._returns is not None:
            return self._returns
        await asyncio.Event().wait()  # a normal monitor blocks here until shutdown cancels it
        raise AssertionError("unreachable")  # pragma: no cover


class MonitorFactory:
    """Records how many monitors the supervisor asked for; returns the configured one each time."""

    def __init__(self, monitor: FakeMonitor) -> None:
        self._monitor = monitor
        self.calls = 0

    def __call__(self) -> FakeMonitor:
        self.calls += 1
        return self._monitor


# --- Builder -----------------------------------------------------------------------------------


@dataclass
class _Ctx:
    supervisor: AgentRuntimeSupervisor
    repo: DeviceIdentityRepository
    paths: AgentPaths
    backend: FakeBackendClient
    validation: FakeValidationClient
    monitor_factory: MonitorFactory


def _identity(
    *, secret: str = SECRET_1, state: OperationalState = OperationalState.OPERATIONAL
) -> DeviceIdentity:
    if state is OperationalState.REACTIVATION_REQUIRED:
        return DeviceIdentity(
            device_id=DEVICE_ID,
            shared_secret=None,
            activated_at=T0,
            last_activated_at=T0,
            operational_state=OperationalState.REACTIVATION_REQUIRED,
        )
    return DeviceIdentity(
        device_id=DEVICE_ID, shared_secret=SecretStr(secret), activated_at=T0, last_activated_at=T0
    )


def _activation_result(*, device_id: str = DEVICE_ID, secret: str = SECRET_2) -> ActivationResult:
    return ActivationResult(
        device_id=device_id, shared_secret=SecretStr(secret), branch_id=BRANCH_ID
    )


def _build(
    tmp_path: Path,
    *,
    prestore: DeviceIdentity | None = None,
    lock_after_store: bool = False,
    key: str | None = None,
    write_key_file: bool = False,
    backend: FakeBackendClient | None = None,
    validation_result: CredentialValidationResult | None = None,
    components: tuple[FakeComponent, ...] = (),
    monitor: FakeMonitor | None = None,
    owns_validation: bool = True,
    interval: int = 30,
) -> _Ctx:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    repo = DeviceIdentityRepository(paths.database_file)
    if prestore is not None:
        repo.store(prestore)
        if lock_after_store:
            repo.mark_reactivation_required()
    if write_key_file:
        paths.activation_key_file.write_text("a-file-key", encoding="utf-8")

    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=str(paths.root),
        activation_key=key,
        http_timeout_seconds=5,
        credential_validation_interval_seconds=interval,
        log_level="INFO",
    )
    resolver = ActivationKeyResolver(
        environment_key=settings.activation_key, key_file_path=paths.activation_key_file
    )
    backend = backend if backend is not None else FakeBackendClient(result=_activation_result())
    service = ActivationService(
        backend_client=backend,  # type: ignore[arg-type]
        identity_repository=repo,
        key_resolver=resolver,
        clock=lambda: T1,
    )
    validation = FakeValidationClient(result=validation_result)
    factory = MonitorFactory(monitor if monitor is not None else FakeMonitor())
    supervisor = AgentRuntimeSupervisor(
        settings=settings,
        identity_repository=repo,
        key_resolver=resolver,
        activation_service=service,
        validation_client=validation,  # type: ignore[arg-type]
        components=components,  # type: ignore[arg-type]
        monitor_factory=factory,
        owns_validation_client=owns_validation,
    )
    return _Ctx(supervisor, repo, paths, backend, validation, factory)


# --- 1. Branch A — first activation ------------------------------------------------------------


def test_branch_a_first_activation(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            key=FAKE_KEY,
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_1)),
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.backend.activate_calls == 1  # exactly one activation
        assert ctx.supervisor.startup_branch is StartupBranch.FIRST_ACTIVATION
        stored = ctx.repo.load()
        assert stored is not None
        assert stored.operational_state is OperationalState.OPERATIONAL
        assert stored.shared_secret is not None
        assert a.start_count == 1  # components started
        assert ctx.supervisor.is_monitor_running is True
        assert ctx.monitor_factory.calls == 1  # exactly one monitor
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


def test_branch_a_deletes_file_key_after_persistence(tmp_path: Path) -> None:
    async def _scenario() -> None:
        ctx = _build(
            tmp_path,
            key=None,
            write_key_file=True,
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_1)),
        )
        await ctx.supervisor.startup()
        assert not ctx.paths.activation_key_file.exists()  # deleted only after persistence
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 2. Branch B — manual reactivation ---------------------------------------------------------


def test_branch_b_reactivation(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            lock_after_store=True,  # was locked, awaiting manual reactivation
            key=FAKE_KEY,
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_2)),
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.backend.activate_calls == 1
        assert ctx.supervisor.startup_branch is StartupBranch.REACTIVATION
        stored = ctx.repo.load()
        assert stored is not None
        assert stored.device_id == DEVICE_ID  # preserved
        assert stored.activated_at == T0  # preserved
        assert stored.last_activated_at == T1  # advanced
        assert stored.shared_secret is not None
        assert stored.shared_secret.get_secret_value() == SECRET_2  # replacement persisted
        assert stored.operational_state is OperationalState.OPERATIONAL
        assert a.start_count == 1
        assert ctx.monitor_factory.calls == 1
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 3. Branch B — DeviceId mismatch -----------------------------------------------------------


def test_branch_b_mismatch_fails_and_changes_nothing(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=FAKE_KEY,
            write_key_file=False,
            backend=FakeBackendClient(
                result=_activation_result(device_id=OTHER_DEVICE_ID, secret=SECRET_2)
            ),
            components=(a,),
        )
        ctx.paths.activation_key_file.write_text("a-file-key", encoding="utf-8")

        with pytest.raises(DeviceIdentityMismatchError):
            await ctx.supervisor.startup()

        stored = ctx.repo.load()
        assert stored is not None
        assert stored.device_id == DEVICE_ID  # unchanged
        assert stored.shared_secret is not None
        assert stored.shared_secret.get_secret_value() == SECRET_1  # unchanged
        assert ctx.paths.activation_key_file.exists()  # key kept
        assert a.start_count == 0  # no components
        assert ctx.monitor_factory.calls == 0  # no monitor

    asyncio.run(_scenario())


# --- 4. Branch B — persistence failure ---------------------------------------------------------


def test_branch_b_persistence_failure_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            lock_after_store=True,  # locked, secret already NULL
            key=FAKE_KEY,
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_2)),
            components=(a,),
        )

        def _boom(*args: object, **kwargs: object) -> None:
            raise RepositoryError("injected persistence failure")

        monkeypatch.setattr(ctx.repo, "replace_shared_secret", _boom)

        with pytest.raises(ActivationPersistenceError):
            await ctx.supervisor.startup()

        stored = ctx.repo.load()
        assert stored is not None
        assert stored.operational_state is OperationalState.REACTIVATION_REQUIRED  # still locked
        assert stored.shared_secret is None  # secret remains NULL
        assert a.start_count == 0
        assert ctx.monitor_factory.calls == 0

    asyncio.run(_scenario())


# --- 5. Branch C — already locked, no key ------------------------------------------------------


def test_branch_c_already_locked_stays_locked_without_backend(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            lock_after_store=True,
            key=None,
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.supervisor.startup_branch is StartupBranch.LOCKED_REACTIVATION_REQUIRED
        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert ctx.backend.activate_calls == 0  # zero activation
        assert ctx.validation.validate_calls == 0  # zero validation (no NULL secret sent)
        assert a.start_count == 0  # zero components
        assert ctx.monitor_factory.calls == 0  # zero monitor
        await ctx.supervisor.shutdown()  # locked-mode shutdown is safe

    asyncio.run(_scenario())


# --- 6. Branch D1 — startup validation Valid ---------------------------------------------------


def test_branch_d1_valid_operates_without_mutation(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.validation.validate_calls == 1  # exactly one startup validation
        assert ctx.backend.activate_calls == 0
        assert ctx.supervisor.startup_branch is StartupBranch.OPERATIONAL_VALIDATED
        stored = ctx.repo.load()
        assert stored is not None
        assert stored.operational_state is OperationalState.OPERATIONAL  # unchanged
        assert stored.shared_secret is not None
        assert stored.shared_secret.get_secret_value() == SECRET_1  # unchanged
        assert a.start_count == 1
        assert ctx.monitor_factory.calls == 1
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 7. Branch D2 — startup validation Indeterminate (offline start) ---------------------------


@pytest.mark.parametrize(
    "result",
    [
        CredentialValidationResult.indeterminate(IndeterminateReason.TIMEOUT),
        CredentialValidationResult.indeterminate(IndeterminateReason.TRANSPORT_FAILURE),
        CredentialValidationResult.indeterminate(IndeterminateReason.SERVER_FAILURE, 503),
        CredentialValidationResult.indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 404),
    ],
)
def test_branch_d2_indeterminate_starts_operational_offline(
    tmp_path: Path, result: CredentialValidationResult
) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=result,
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.supervisor.startup_branch is StartupBranch.OPERATIONAL_OFFLINE
        stored = ctx.repo.load()
        assert stored is not None
        assert stored.operational_state is OperationalState.OPERATIONAL  # not locked
        assert stored.shared_secret is not None  # secret preserved
        assert a.start_count == 1
        assert ctx.monitor_factory.calls == 1  # one monitor for later retry
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 8. Branch D3 — startup ConfirmedRejected --------------------------------------------------


def test_branch_d3_confirmed_rejected_locks_and_starts_nothing(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.confirmed_rejected(401),
            components=(a,),
        )
        await ctx.supervisor.startup()

        assert ctx.validation.validate_calls == 1  # exactly one startup validation
        assert ctx.supervisor.startup_branch is StartupBranch.STARTUP_CONFIRMED_REJECTED
        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        stored = ctx.repo.load()
        assert stored is not None
        assert stored.operational_state is OperationalState.REACTIVATION_REQUIRED  # persisted lock
        assert stored.shared_secret is None  # secret cleared
        assert a.start_count == 0  # no components
        assert ctx.monitor_factory.calls == 0  # no monitor
        assert ctx.backend.activate_calls == 0  # no activation
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 9. Branch D4 — lock persistence failure ---------------------------------------------------


def test_branch_d4_lock_persistence_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.confirmed_rejected(401),
            components=(a,),
        )

        def _boom() -> None:
            raise sqlite3.OperationalError("injected lock persistence failure")

        monkeypatch.setattr(ctx.repo, "mark_reactivation_required", _boom)

        with pytest.raises(AgentStartupError) as exc_info:
            await ctx.supervisor.startup()

        # Fail-closed: in-memory locked coordinator, nothing operational, no secret in the error.
        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert a.start_count == 0
        assert ctx.monitor_factory.calls == 0
        assert SECRET_1 not in str(exc_info.value)

    asyncio.run(_scenario())


# --- 10. No identity and no key ----------------------------------------------------------------


def test_no_identity_no_key_fails_loudly(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(tmp_path, key=None, components=(a,))

        with pytest.raises(ActivationKeyMissingError):
            await ctx.supervisor.startup()

        assert ctx.backend.activate_calls == 0
        assert ctx.validation.validate_calls == 0
        assert a.start_count == 0
        assert ctx.monitor_factory.calls == 0

    asyncio.run(_scenario())


# --- 11. Invalid local identity / database failure ---------------------------------------------


def test_local_identity_load_failure_fails_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(tmp_path, prestore=_identity(secret=SECRET_1), key=None, components=(a,))

        def _boom() -> DeviceIdentity | None:
            raise sqlite3.OperationalError("injected load failure")

        monkeypatch.setattr(ctx.repo, "load", _boom)

        with pytest.raises(AgentStartupError):
            await ctx.supervisor.startup()

        assert ctx.backend.activate_calls == 0  # not treated as an offline Backend
        assert ctx.validation.validate_calls == 0
        assert a.start_count == 0
        assert ctx.monitor_factory.calls == 0

    asyncio.run(_scenario())


# --- 12. Monitor terminal rejection ------------------------------------------------------------


def test_monitor_terminal_rejection_locks_without_double_persist(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        monitor = FakeMonitor(returns=CredentialValidationLoopResult.reactivation_required())
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
            monitor=monitor,
        )
        lock_calls: list[str] = []
        original = ctx.repo.mark_reactivation_required

        def _spy() -> None:
            lock_calls.append("lock")
            original()

        ctx.repo.mark_reactivation_required = _spy  # type: ignore[method-assign]

        await ctx.supervisor.startup()
        assert a.start_count == 1
        await ctx.supervisor._monitor_task  # let the terminal handling run

        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert a.stop_count == 1  # coordinator stopped the component
        assert monitor.run_count == 1
        assert ctx.monitor_factory.calls == 1  # monitor not restarted
        assert lock_calls == []  # no repository lock written twice (T-59 already did)
        assert ctx.supervisor.fatal_error is None  # a terminal result is not a fatal error
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 13. Monitor fatal persistence/lifecycle error ---------------------------------------------


def test_monitor_fatal_error_is_fail_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        monitor = FakeMonitor(raises=CredentialLockPersistenceError(f"boom {SENTINEL}"))
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
            monitor=monitor,
        )

        with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent.runtime.supervisor"):
            await ctx.supervisor.startup()
            await ctx.supervisor._monitor_task  # let the fatal handling run

        assert ctx.supervisor.fatal_error is not None
        assert ctx.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert a.stop_count == 1  # components stopped
        assert ctx.monitor_factory.calls == 1  # monitor not restarted
        assert ctx.backend.activate_calls == 0  # no activation
        # No raw exception text (which might carry sensitive data) in the error or logs.
        assert SENTINEL not in str(ctx.supervisor.fatal_error)
        assert SENTINEL not in caplog.text
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 14. Cancellation during shutdown ----------------------------------------------------------


def test_shutdown_cancels_monitor_and_closes_owned_client(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        monitor = FakeMonitor()  # blocks until cancelled
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
            monitor=monitor,
        )
        await ctx.supervisor.startup()
        assert ctx.supervisor.is_monitor_running is True

        await ctx.supervisor.shutdown()

        assert ctx.supervisor.is_monitor_running is False  # no orphan task
        assert a.stop_count == 1  # components stopped
        assert ctx.validation.closed is True  # owned client closed
        assert ctx.supervisor.fatal_error is None  # cancellation is not a fatal event

    asyncio.run(_scenario())


def test_shutdown_does_not_close_unowned_client(tmp_path: Path) -> None:
    async def _scenario() -> None:
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            owns_validation=False,  # a caller keeps ownership
        )
        await ctx.supervisor.startup()
        await ctx.supervisor.shutdown()
        assert ctx.validation.closed is False  # an injected, unowned client is never closed

    asyncio.run(_scenario())


# --- 15. Unexpected monitor exception ----------------------------------------------------------


def test_unexpected_monitor_exception_is_recorded_not_swallowed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _scenario() -> None:
        monitor = FakeMonitor(raises=RuntimeError(f"unexpected {SENTINEL}"))
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            monitor=monitor,
        )
        with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent.runtime.supervisor"):
            await ctx.supervisor.startup()
            await ctx.supervisor._monitor_task

        assert ctx.supervisor.fatal_error is not None  # recorded, not silently swallowed
        assert SENTINEL not in caplog.text  # no raw text leaks
        await ctx.supervisor.shutdown()  # shutdown still completes

    asyncio.run(_scenario())


# --- 16. Duplicate startup call ----------------------------------------------------------------


def test_duplicate_startup_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
        )
        await ctx.supervisor.startup()
        with pytest.raises(AgentStartupError):
            await ctx.supervisor.startup()  # a second startup is refused

        assert a.start_count == 1  # not started twice
        assert ctx.monitor_factory.calls == 1  # no duplicate monitor
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 17. Shutdown idempotence ------------------------------------------------------------------


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    async def _scenario() -> None:
        a = FakeComponent("a")
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(a,),
        )
        await ctx.supervisor.startup()
        await ctx.supervisor.shutdown()
        await ctx.supervisor.shutdown()  # safe second call

        assert a.stop_count == 1  # not stopped repeatedly
        assert ctx.validation.closed is True  # closed once, not double-closed into an error

    asyncio.run(_scenario())


# --- 18. Empty operational-component collection ------------------------------------------------


def test_empty_component_collection_startup_and_shutdown(tmp_path: Path) -> None:
    async def _scenario() -> None:
        ctx = _build(
            tmp_path,
            prestore=_identity(secret=SECRET_1),
            key=None,
            validation_result=CredentialValidationResult.valid(200),
            components=(),
        )
        await ctx.supervisor.startup()
        assert ctx.supervisor.state is OperationalState.OPERATIONAL
        assert ctx.supervisor.is_monitor_running is True  # monitor still starts
        await ctx.supervisor.shutdown()

    asyncio.run(_scenario())


# --- 19. Setting use — timeout vs interval (not swapped) ---------------------------------------


def test_settings_are_not_swapped(tmp_path: Path) -> None:
    ctx = _build(
        tmp_path,
        prestore=_identity(secret=SECRET_1),
        key=None,
        validation_result=CredentialValidationResult.valid(200),
        interval=45,
    )
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=str(ctx.paths.root),
        http_timeout_seconds=5,
        credential_validation_interval_seconds=45,
    )
    # The validation client uses the HTTP timeout, never the interval.
    client = default_validation_client_factory(settings)
    try:
        assert client._timeout == 5
        assert client._timeout != 45
    finally:
        asyncio.run(client.aclose())
    # The default monitor factory builds a monitor whose interval is the validation interval.
    monitor = ctx.supervisor._default_monitor_factory()
    assert monitor._interval_seconds == 45  # type: ignore[attr-defined]


# --- 20. No automatic key retrieval ------------------------------------------------------------


def test_no_automatic_key_retrieval_surface(tmp_path: Path) -> None:
    ctx = _build(tmp_path, prestore=_identity(secret=SECRET_1), lock_after_store=True, key=None)
    # The supervisor exposes no key-download / polling surface; recovery is manual (resolver only).
    for forbidden in ("download_key", "poll_for_key", "fetch_key", "retry_activation"):
        assert not hasattr(ctx.supervisor, forbidden)


# --- 23. Secret safety -------------------------------------------------------------------------


def test_startup_and_shutdown_leak_no_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def _scenario() -> None:
        ctx = _build(
            tmp_path,
            key=FAKE_KEY,
            backend=FakeBackendClient(result=_activation_result(secret=SECRET_1)),
        )
        with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
            await ctx.supervisor.startup()
            await ctx.supervisor.shutdown()

        assert FAKE_KEY not in caplog.text
        assert SECRET_1 not in caplog.text
        assert FAKE_KEY not in repr(ctx.supervisor)
        assert SECRET_1 not in repr(ctx.supervisor)

    asyncio.run(_scenario())
