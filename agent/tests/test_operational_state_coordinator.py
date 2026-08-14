"""Unit tests for the operational-state coordinator and credential-lock enforcement (IP-05 T-60).

Every transition is exercised deterministically with fake operational components
(``_FakeComponent``) that record start/stop counts, running state, and operation order, and can be
configured to block, fail on start, or fail on stop. Async coordinator methods run via
``asyncio.run`` so no async-test plugin is required. Unlock tests use a real temporary SQLite
database (never the Jetson database) so the "durable state is the source of truth" contract is
proven against real persistence. A recognisable secret sentinel is planted in a component's
stop-failure text and must never surface in a coordinator error or log, which the secret-safety
tests assert.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import types
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.operational_components import OperationalComponent
from weapon_detection_agent.runtime.operational_state_coordinator import (
    OperationalComponentShutdownError,
    OperationalComponentsLockedError,
    OperationalComponentStartError,
    OperationalStateCoordinator,
    ReactivationNotPersistedError,
    UnknownValidationLoopResultError,
)
from weapon_detection_agent.validation.loop import CredentialValidationLoopResult

# A raw component-exception sentinel that a real component might carry — it must never reach a
# coordinator error message or log line (the coordinator reports names only, not raw text).
SECRET_SENTINEL = "ZZZ-coordinator-raw-exception-must-never-appear-ZZZ"  # noqa: S105 - placeholder
FAKE_SECRET = "ZZZ-coordinator-identity-secret-must-never-appear-ZZZ"  # noqa: S105 - placeholder
DEVICE_ID = "device-77777777-6666-5555-4444-333333333333"
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER_AT = datetime(2026, 6, 7, 8, 9, 10, tzinfo=timezone.utc)


# --- Fake operational component ----------------------------------------------------------------


class _FakeComponent:
    """A deterministic operational component for tests (satisfies ``OperationalComponent``)."""

    def __init__(
        self,
        name: str,
        order: list[str] | None = None,
        *,
        start_fails: bool = False,
        stop_fails: bool = False,
        start_gate: asyncio.Event | None = None,
        stop_gate: asyncio.Event | None = None,
    ) -> None:
        self.name = name
        self.start_count = 0
        self.stop_count = 0
        self.running = False
        self._order = order
        self._start_fails = start_fails
        self._stop_fails = stop_fails
        self._start_gate = start_gate
        self._stop_gate = stop_gate

    async def start(self) -> None:
        if self._start_gate is not None:
            await self._start_gate.wait()
        self.start_count += 1
        if self._order is not None:
            self._order.append(f"{self.name}:start")
        if self._start_fails:
            raise RuntimeError(f"start failure for {self.name}")
        self.running = True

    async def stop(self) -> None:
        if self._stop_gate is not None:
            await self._stop_gate.wait()
        self.stop_count += 1
        if self._order is not None:
            self._order.append(f"{self.name}:stop")
        if self._stop_fails:
            # A raw component exception that (in the real world) might carry sensitive text.
            raise RuntimeError(f"stop failure for {self.name} {SECRET_SENTINEL}")
        self.running = False


# --- Helpers -----------------------------------------------------------------------------------


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _repo(tmp_path: Path) -> DeviceIdentityRepository:
    return DeviceIdentityRepository(_ready_db(tmp_path))


def _operational_identity(secret: str = FAKE_SECRET) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=DEVICE_ID,
        shared_secret=SecretStr(secret),
        activated_at=ACTIVATED_AT,
        last_activated_at=LATER_AT,
    )


def _coordinator(
    repo: DeviceIdentityRepository,
    components: Sequence[OperationalComponent],
    state: OperationalState,
) -> OperationalStateCoordinator:
    return OperationalStateCoordinator(
        identity_repository=repo,
        components=components,  # type: ignore[arg-type]  # structural fakes of the component shape
        initial_state=state,
    )


# --- 1. Initial Operational state --------------------------------------------------------------


def test_initial_operational_starts_each_component_once(tmp_path: Path) -> None:
    a, b = _FakeComponent("a"), _FakeComponent("b")
    coord = _coordinator(_repo(tmp_path), [a, b], OperationalState.OPERATIONAL)

    assert coord.state is OperationalState.OPERATIONAL
    assert coord.can_run_operational_components is True

    asyncio.run(coord.start_operational_components())

    assert (a.start_count, b.start_count) == (1, 1)
    assert a.running and b.running


# --- 2. Initial ReactivationRequired state -----------------------------------------------------


def test_initial_locked_rejects_start_with_zero_component_calls(tmp_path: Path) -> None:
    a = _FakeComponent("a")
    coord = _coordinator(_repo(tmp_path), [a], OperationalState.REACTIVATION_REQUIRED)

    assert coord.can_run_operational_components is False
    with pytest.raises(OperationalComponentsLockedError):
        asyncio.run(coord.start_operational_components())

    assert a.start_count == 0
    assert not a.running


# --- 3 / 17. Handle T-59 terminal result -------------------------------------------------------


def test_handle_reactivation_required_result_locks_and_stops_without_repersisting(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    repo.store(_operational_identity())
    lock_calls: list[str] = []
    original = repo.mark_reactivation_required

    def _spy_lock() -> None:
        lock_calls.append("lock")
        original()

    repo.mark_reactivation_required = _spy_lock  # type: ignore[method-assign]

    a, b = _FakeComponent("a"), _FakeComponent("b")
    coord = _coordinator(repo, [a, b], OperationalState.OPERATIONAL)
    asyncio.run(coord.start_operational_components())

    result = CredentialValidationLoopResult.reactivation_required()  # the real T-59 type
    asyncio.run(coord.handle_validation_loop_result(result))

    assert coord.state is OperationalState.REACTIVATION_REQUIRED
    assert coord.can_run_operational_components is False
    assert not a.running and not b.running
    assert (a.stop_count, b.stop_count) == (1, 1)
    assert lock_calls == []  # the coordinator never re-persists the lock (T-59 already did)


def test_handle_unknown_result_fails_safely(tmp_path: Path) -> None:
    coord = _coordinator(_repo(tmp_path), [], OperationalState.OPERATIONAL)
    bogus = types.SimpleNamespace(exit_reason=object())  # a future/unknown exit reason

    with pytest.raises(UnknownValidationLoopResultError):
        asyncio.run(coord.handle_validation_loop_result(bogus))  # type: ignore[arg-type]

    assert coord.state is OperationalState.OPERATIONAL  # not silently assumed anything else


# --- 4. Lock stops all running components in reverse start order -------------------------------


def test_lock_stops_all_running_in_reverse_start_order(tmp_path: Path) -> None:
    order: list[str] = []
    a = _FakeComponent("a", order)
    b = _FakeComponent("b", order)
    c = _FakeComponent("c", order)
    coord = _coordinator(_repo(tmp_path), [a, b, c], OperationalState.OPERATIONAL)

    asyncio.run(coord.start_operational_components())
    asyncio.run(coord.enter_reactivation_required())

    assert order == ["a:start", "b:start", "c:start", "c:stop", "b:stop", "a:stop"]
    assert not (a.running or b.running or c.running)
    assert coord.state is OperationalState.REACTIVATION_REQUIRED


# --- 5. Lock is idempotent ---------------------------------------------------------------------


def test_lock_is_idempotent_and_does_not_restart_or_restop(tmp_path: Path) -> None:
    a, b = _FakeComponent("a"), _FakeComponent("b")
    coord = _coordinator(_repo(tmp_path), [a, b], OperationalState.OPERATIONAL)

    asyncio.run(coord.start_operational_components())
    asyncio.run(coord.enter_reactivation_required())
    asyncio.run(coord.enter_reactivation_required())  # second call is a safe no-op

    assert (a.start_count, b.start_count) == (1, 1)  # nothing restarted
    assert (a.stop_count, b.stop_count) == (1, 1)  # already-stopped not stopped again
    assert coord.state is OperationalState.REACTIVATION_REQUIRED


# --- 6. Start prevented after lock -------------------------------------------------------------


def test_start_rejected_after_lock(tmp_path: Path) -> None:
    a = _FakeComponent("a")
    coord = _coordinator(_repo(tmp_path), [a], OperationalState.OPERATIONAL)

    asyncio.run(coord.enter_reactivation_required())
    with pytest.raises(OperationalComponentsLockedError):
        asyncio.run(coord.start_operational_components())

    assert a.start_count == 0


# --- 7. Concurrent start-versus-lock -----------------------------------------------------------


def test_concurrent_start_and_lock_ends_locked_with_nothing_running(tmp_path: Path) -> None:
    async def _scenario() -> None:
        gate = asyncio.Event()
        a = _FakeComponent("a")
        b = _FakeComponent("b", start_gate=gate)  # b's start blocks until the gate opens
        coord = _coordinator(_repo(tmp_path), [a, b], OperationalState.OPERATIONAL)

        start_task = asyncio.ensure_future(coord.start_operational_components())
        for _ in range(5):  # let start acquire the lock and block on b's start gate
            await asyncio.sleep(0)
        assert a.running and b.start_count == 0  # a up; b still gated, lock held by start

        lock_task = asyncio.ensure_future(coord.enter_reactivation_required())
        for _ in range(5):  # lock must queue behind the held transition lock
            await asyncio.sleep(0)
        assert coord.state is OperationalState.OPERATIONAL  # lock cannot proceed yet

        gate.set()  # let the blocked start finish; the queued lock then runs
        await start_task
        await lock_task

        assert coord.state is OperationalState.REACTIVATION_REQUIRED
        assert not (a.running or b.running)  # everything started before the lock is stopped
        assert (a.start_count, b.start_count) == (1, 1)  # no component started after the lock
        assert (a.stop_count, b.stop_count) == (1, 1)

    asyncio.run(_scenario())


# --- 8. Partial start failure ------------------------------------------------------------------


def test_partial_start_failure_rolls_back_and_leaves_nothing_running(tmp_path: Path) -> None:
    order: list[str] = []
    a = _FakeComponent("a", order)
    b = _FakeComponent("b", order, start_fails=True)
    c = _FakeComponent("c", order)
    coord = _coordinator(_repo(tmp_path), [a, b, c], OperationalState.OPERATIONAL)

    with pytest.raises(OperationalComponentStartError):
        asyncio.run(coord.start_operational_components())

    # a started, b failed; a is rolled back; c is never reached.
    assert order == ["a:start", "b:start", "a:stop"]
    assert not a.running
    assert c.start_count == 0
    # A failed start never changes the persistent-mirroring state.
    assert coord.state is OperationalState.OPERATIONAL


# --- 9. Stop failure ---------------------------------------------------------------------------


def test_stop_failure_keeps_lock_and_reports_safe_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    a = _FakeComponent("a")
    b = _FakeComponent("b", stop_fails=True)  # its stop raises text carrying the secret sentinel
    coord = _coordinator(_repo(tmp_path), [a, b], OperationalState.OPERATIONAL)
    asyncio.run(coord.start_operational_components())

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent.runtime.operational_state"):
        with pytest.raises(OperationalComponentShutdownError) as exc_info:
            asyncio.run(coord.enter_reactivation_required())

    # The Agent stays locked despite the stop failure; the other component was still stopped.
    assert coord.state is OperationalState.REACTIVATION_REQUIRED
    assert coord.can_run_operational_components is False
    assert a.stop_count == 1  # reverse order: b failed first, then a was still attempted
    assert "b" in exc_info.value.component_names

    # No raw component exception text (which might carry sensitive data) reaches the error or logs.
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    rendered += "\n" + "\n".join(str(r.__dict__) for r in caplog.records)
    assert SECRET_SENTINEL not in str(exc_info.value)
    assert SECRET_SENTINEL not in repr(exc_info.value)
    assert SECRET_SENTINEL not in rendered


def test_stop_failure_component_is_retried_by_a_later_lock(tmp_path: Path) -> None:
    # A component whose stop keeps failing stays tracked as running, so a later lock retries it.
    b = _FakeComponent("b", stop_fails=True)
    coord = _coordinator(_repo(tmp_path), [b], OperationalState.OPERATIONAL)
    asyncio.run(coord.start_operational_components())

    with pytest.raises(OperationalComponentShutdownError):
        asyncio.run(coord.enter_reactivation_required())
    with pytest.raises(OperationalComponentShutdownError):
        asyncio.run(coord.enter_reactivation_required())  # retried, still locked

    assert b.stop_count == 2  # attempted again on the second lock
    assert coord.state is OperationalState.REACTIVATION_REQUIRED


# --- 10 / 13. Unlock after persisted reactivation ----------------------------------------------


def test_unlock_after_persisted_reactivation_then_explicit_start(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store(_operational_identity())  # durable state: Operational with a secret
    a = _FakeComponent("a")
    coord = _coordinator(repo, [a], OperationalState.REACTIVATION_REQUIRED)

    asyncio.run(coord.mark_operational_after_reactivation())

    assert coord.state is OperationalState.OPERATIONAL
    assert coord.can_run_operational_components is True
    assert a.start_count == 0  # 13: unlocking does not auto-start components

    asyncio.run(coord.start_operational_components())  # caller starts explicitly
    assert a.start_count == 1 and a.running


# --- 11. Unlock rejected when persistence is not complete --------------------------------------


def test_unlock_rejected_when_durable_state_still_locked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store(_operational_identity())
    repo.mark_reactivation_required()  # durable state remains locked (secret cleared)
    a = _FakeComponent("a")
    coord = _coordinator(repo, [a], OperationalState.REACTIVATION_REQUIRED)

    with pytest.raises(ReactivationNotPersistedError):
        asyncio.run(coord.mark_operational_after_reactivation())

    assert coord.state is OperationalState.REACTIVATION_REQUIRED
    assert a.start_count == 0


def test_unlock_rejected_when_identity_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)  # no identity stored
    coord = _coordinator(repo, [], OperationalState.REACTIVATION_REQUIRED)

    with pytest.raises(ReactivationNotPersistedError):
        asyncio.run(coord.mark_operational_after_reactivation())

    assert coord.state is OperationalState.REACTIVATION_REQUIRED


def test_unlock_rejected_when_repository_load_fails(tmp_path: Path) -> None:
    class _FailingRepo(DeviceIdentityRepository):
        def __init__(self) -> None:
            super().__init__(connection_factory=lambda: _unused())  # never opened

        def load(self) -> DeviceIdentity | None:
            raise sqlite3.OperationalError("injected load failure")

    def _unused() -> object:
        raise AssertionError("connection should not be opened")

    coord = _coordinator(_FailingRepo(), [], OperationalState.REACTIVATION_REQUIRED)

    with pytest.raises(ReactivationNotPersistedError):
        asyncio.run(coord.mark_operational_after_reactivation())

    assert coord.state is OperationalState.REACTIVATION_REQUIRED


# --- 12. Unlock does not activate --------------------------------------------------------------


def test_unlock_performs_no_activation_or_key_access(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store(_operational_identity())
    coord = _coordinator(repo, [], OperationalState.REACTIVATION_REQUIRED)

    # The coordinator holds only the identity repository — no Backend client, no key resolver, no
    # activation surface — so it cannot activate or read/delete an Activation Key.
    assert not hasattr(coord, "activate")
    assert not hasattr(OperationalStateCoordinator, "activate")
    asyncio.run(coord.mark_operational_after_reactivation())
    assert coord.state is OperationalState.OPERATIONAL


# --- 14. Duplicate start protection ------------------------------------------------------------


def test_duplicate_start_does_not_start_components_twice(tmp_path: Path) -> None:
    a, b = _FakeComponent("a"), _FakeComponent("b")
    coord = _coordinator(_repo(tmp_path), [a, b], OperationalState.OPERATIONAL)

    asyncio.run(coord.start_operational_components())
    asyncio.run(coord.start_operational_components())  # already running — no-op

    assert (a.start_count, b.start_count) == (1, 1)


# --- 15. Empty component collection ------------------------------------------------------------


def test_empty_component_collection_is_safe(tmp_path: Path) -> None:
    coord = _coordinator(_repo(tmp_path), [], OperationalState.OPERATIONAL)

    asyncio.run(coord.start_operational_components())  # no components — no error
    asyncio.run(coord.enter_reactivation_required())  # lock with nothing to stop — no error

    assert coord.state is OperationalState.REACTIVATION_REQUIRED


# --- 16. Secret safety -------------------------------------------------------------------------


def test_coordinator_repr_and_errors_hold_no_credentials(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store(_operational_identity())
    coord = _coordinator(repo, [], OperationalState.OPERATIONAL)

    # The coordinator never stores a secret, so no representation can leak one.
    assert FAKE_SECRET not in repr(coord)
    assert FAKE_SECRET not in str(coord.__dict__)

    locked = OperationalComponentsLockedError("locked")
    shutdown = OperationalComponentShutdownError(["deepstream", "alerts"])
    assert FAKE_SECRET not in str(locked)
    assert SECRET_SENTINEL not in str(shutdown)
    assert "deepstream" in str(shutdown)  # only safe static names appear


# --- from_identity factory ---------------------------------------------------------------------


def test_from_identity_mirrors_operational_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    coord = OperationalStateCoordinator.from_identity(
        _operational_identity(), identity_repository=repo
    )
    assert coord.state is OperationalState.OPERATIONAL


def test_from_identity_rejects_missing_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError):
        OperationalStateCoordinator.from_identity(None, identity_repository=repo)


def test_operational_component_protocol_is_satisfied_by_fake() -> None:
    # The runtime-checkable Protocol recognises a component exposing start/stop.
    assert isinstance(_FakeComponent("x"), OperationalComponent)
