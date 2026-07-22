"""Unit and integration tests for the credential-validation polling lifecycle (IP-05 T-59).

The monitor is exercised deterministically: no real 30-second sleeps. Time is controlled by a
scripted :class:`_ScriptedSleeper` that records every requested interval and stops the loop after a
set number of waits (raising a private sentinel that is not one of the lifecycle outcomes), so the
serial ``validate → classify → wait`` cadence is asserted exactly. The validation client is a fake
(``_FakeValidationClient``) that returns scripted ``CredentialValidationResult`` values and counts
calls, so no real Backend, network, or httpx is involved. The lock/persistence assertions run
against a real temporary SQLite database (never the Jetson database). Every secret is an obvious
placeholder and must never appear in a result, a log, or an exception — which the secret-safety
tests assert.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.validation.client import VALIDATION_PATH
from weapon_detection_agent.validation.loop import (
    CredentialLockPersistenceError,
    CredentialValidationLoopExitReason,
    CredentialValidationLoopResult,
    DeviceIdentityUnavailableError,
)
from weapon_detection_agent.validation.models import (
    CredentialValidationResult,
    IndeterminateReason,
)
from weapon_detection_agent.validation.monitor import CredentialValidationMonitor

# Recognisable non-credential sentinels — never real secrets (IP-02 §10 forbids committing one).
FAKE_SECRET = "ZZZ-fake-monitor-secret-must-never-appear-ZZZ"  # noqa: S105 - placeholder
FAKE_SECRET_B = "ZZZ-fake-monitor-secret-B-must-never-appear-ZZZ"  # noqa: S105 - placeholder
DEVICE_ID = "device-99999999-8888-7777-6666-555555555555"
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER_AT = datetime(2026, 6, 7, 8, 9, 10, tzinfo=timezone.utc)

INTERVAL = 30.0
HTTP_TIMEOUT = 10.0  # a distinct value: the interval must never be used as the HTTP timeout.


# --- Test doubles ------------------------------------------------------------------------------


class _StopLoop(Exception):
    """A private sentinel raised by the scripted sleeper to end an otherwise-infinite loop.

    It is deliberately not a lifecycle outcome (Valid/Indeterminate/ConfirmedRejected/
    ReactivationRequired) and not ``CancelledError``, so it cannot be confused with real behaviour.
    """


class _ScriptedSleeper:
    """A deterministic wait seam: records every requested delay, stops after ``max_waits`` waits."""

    def __init__(self, max_waits: int) -> None:
        self._max_waits = max_waits
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self._max_waits:
            raise _StopLoop
        # Yield control without consuming real time.
        await asyncio.sleep(0)


class _BlockingSleeper:
    """A wait seam that blocks forever on the first wait, signalling when the wait was entered.

    Used to prove cancellation-while-waiting propagates and that no further request is made.
    """

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.entered.set()
        await asyncio.Event().wait()  # never set — blocks until cancelled


class _FakeValidationClient:
    """A stand-in for the T-57 client: returns scripted results and records each call.

    It never opens a socket and exposes no ``activate`` method, so the monitor cannot activate.
    ``aclose`` records whether the monitor (wrongly) tried to close this injected client.
    """

    def __init__(self, results: list[CredentialValidationResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    async def validate(
        self, device_id: str, shared_secret: SecretStr
    ) -> CredentialValidationResult:
        self.calls.append((device_id, shared_secret.get_secret_value()))
        if self._results:
            return self._results.pop(0)
        return CredentialValidationResult.indeterminate(IndeterminateReason.TRANSPORT_FAILURE)

    async def aclose(self) -> None:
        self.closed = True


# --- Fixtures / helpers ------------------------------------------------------------------------


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _operational_identity(secret: str = FAKE_SECRET) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=DEVICE_ID,
        shared_secret=SecretStr(secret),
        activated_at=ACTIVATED_AT,
        last_activated_at=LATER_AT,
    )


def _seed_operational(tmp_path: Path) -> tuple[DeviceIdentityRepository, Path]:
    db = _ready_db(tmp_path)
    repo = DeviceIdentityRepository(db)
    repo.store(_operational_identity())
    return repo, db


def _monitor(
    repo: DeviceIdentityRepository,
    client: _FakeValidationClient,
    sleeper: _ScriptedSleeper | _BlockingSleeper,
    *,
    interval: float = INTERVAL,
) -> CredentialValidationMonitor:
    return CredentialValidationMonitor(
        identity_repository=repo,
        validation_client=client,  # type: ignore[arg-type]  # structural fake of the T-57 client
        interval_seconds=interval,
        sleeper=sleeper,  # type: ignore[arg-type]  # structural fake of the Sleeper
    )


def _valid() -> CredentialValidationResult:
    return CredentialValidationResult.valid(200)


def _confirmed() -> CredentialValidationResult:
    return CredentialValidationResult.confirmed_rejected(401)


def _indeterminate(
    reason: IndeterminateReason = IndeterminateReason.TIMEOUT, status: int | None = None
) -> CredentialValidationResult:
    return CredentialValidationResult.indeterminate(reason, status)


# --- 1. Immediate first validation -------------------------------------------------------------


def test_first_validation_is_immediate_with_no_sleep_before_it(tmp_path: Path) -> None:
    repo, _ = _seed_operational(tmp_path)
    # A sleeper that stops the instant it is asked to wait the first time: if any validation ran, it
    # ran before the wait.
    sleeper = _ScriptedSleeper(max_waits=1)
    client = _FakeValidationClient([_valid()])

    with pytest.raises(_StopLoop):
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert len(client.calls) == 1  # validated once, immediately
    # The single wait recorded happened only after that first validation.
    assert sleeper.delays == [INTERVAL]


# --- 2. Periodic Valid results -----------------------------------------------------------------


def test_valid_results_mutate_nothing_and_wait_one_interval_between_attempts(
    tmp_path: Path,
) -> None:
    repo, _ = _seed_operational(tmp_path)
    before = repo.load()
    sleeper = _ScriptedSleeper(max_waits=3)  # allow three attempts, then stop on the third wait
    client = _FakeValidationClient([_valid(), _valid(), _valid()])

    with pytest.raises(_StopLoop):
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert len(client.calls) == 3  # one validation per interval, serial (no overlap)
    assert sleeper.delays == [INTERVAL, INTERVAL, INTERVAL]

    after = repo.load()
    assert before is not None and after is not None
    assert after.operational_state is OperationalState.OPERATIONAL
    assert after.shared_secret is not None
    assert after.shared_secret.get_secret_value() == FAKE_SECRET  # secret preserved
    assert after.device_id == before.device_id
    assert after.activated_at == before.activated_at  # timestamps unchanged
    assert after.last_activated_at == before.last_activated_at


# --- 3. Indeterminate timeout ------------------------------------------------------------------


def test_indeterminate_timeout_does_not_lock_and_retries_only_after_one_interval(
    tmp_path: Path,
) -> None:
    repo, _ = _seed_operational(tmp_path)
    sleeper = _ScriptedSleeper(max_waits=2)
    client = _FakeValidationClient([_indeterminate(IndeterminateReason.TIMEOUT), _valid()])

    with pytest.raises(_StopLoop):
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert len(client.calls) == 2  # validated, waited one interval, validated again
    assert sleeper.delays == [INTERVAL, INTERVAL]  # exactly one interval each — no immediate retry

    after = repo.load()
    assert after is not None
    assert after.operational_state is OperationalState.OPERATIONAL
    assert after.shared_secret is not None  # secret not cleared


# --- 4. Indeterminate transport/server outcomes ------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        _indeterminate(IndeterminateReason.TRANSPORT_FAILURE),
        _indeterminate(IndeterminateReason.SERVER_FAILURE, 503),
        _indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 403),
        _indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 404),
        _indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 408),
        _indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 409),
        _indeterminate(IndeterminateReason.UNEXPECTED_STATUS, 429),
        _indeterminate(IndeterminateReason.INVALID_RESPONSE, 200),
    ],
)
def test_representative_indeterminate_outcomes_never_lock(
    tmp_path: Path, result: CredentialValidationResult
) -> None:
    repo, _ = _seed_operational(tmp_path)
    calls: list[str] = []
    original = repo.mark_reactivation_required

    def _tracking_lock() -> None:
        calls.append("locked")
        original()

    repo.mark_reactivation_required = _tracking_lock  # type: ignore[method-assign]
    sleeper = _ScriptedSleeper(max_waits=1)
    client = _FakeValidationClient([result])

    with pytest.raises(_StopLoop):
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert calls == []  # mark_reactivation_required never called
    after = repo.load()
    assert after is not None
    assert after.operational_state is OperationalState.OPERATIONAL


# --- 5. Confirmed rejection --------------------------------------------------------------------


def test_confirmed_rejection_locks_once_and_returns_terminal_result(tmp_path: Path) -> None:
    repo, _ = _seed_operational(tmp_path)
    before = repo.load()

    lock_calls: list[str] = []
    original = repo.mark_reactivation_required

    def _tracking_lock() -> None:
        lock_calls.append("locked")
        original()

    repo.mark_reactivation_required = _tracking_lock  # type: ignore[method-assign]
    # A Valid first, then a confirmed rejection: proves the loop keeps polling until the rejection,
    # then stops. A trailing Valid must never be consumed (no request after the lock).
    sleeper = _ScriptedSleeper(max_waits=5)
    client = _FakeValidationClient([_valid(), _confirmed(), _valid()])

    result = asyncio.run(_monitor(repo, client, sleeper).run())

    assert isinstance(result, CredentialValidationLoopResult)
    assert result.exit_reason is CredentialValidationLoopExitReason.REACTIVATION_REQUIRED
    assert result.is_reactivation_required
    assert lock_calls == ["locked"]  # exactly one lock operation
    assert len(client.calls) == 2  # Valid then Confirmed; the trailing Valid is never requested

    after = repo.load()
    assert before is not None and after is not None
    assert after.operational_state is OperationalState.REACTIVATION_REQUIRED
    assert after.shared_secret is None  # secret becomes NULL
    assert after.device_id == before.device_id  # DeviceId unchanged
    assert after.activated_at == before.activated_at  # timestamps unchanged
    assert after.last_activated_at == before.last_activated_at


# --- 6. Existing local lock --------------------------------------------------------------------


def test_starting_already_locked_makes_no_request_and_terminates_as_reactivation_required(
    tmp_path: Path,
) -> None:
    db = _ready_db(tmp_path)
    repo = DeviceIdentityRepository(db)
    repo.store(_operational_identity())
    repo.mark_reactivation_required()  # locked before the monitor starts

    sleeper = _ScriptedSleeper(max_waits=1)
    client = _FakeValidationClient([_valid()])  # would be returned if a request were ever made

    result = asyncio.run(_monitor(repo, client, sleeper).run())

    assert result.is_reactivation_required
    assert client.calls == []  # zero HTTP requests
    assert sleeper.delays == []  # no wait — it exits immediately


# --- 7. Missing / non-authenticatable identity -------------------------------------------------


def test_missing_identity_makes_no_request_and_fails_with_lifecycle_error(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = DeviceIdentityRepository(db)  # no identity stored
    sleeper = _ScriptedSleeper(max_waits=1)
    client = _FakeValidationClient([_valid()])

    with pytest.raises(DeviceIdentityUnavailableError):
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert client.calls == []  # no request, no fabricated credentials
    assert sleeper.delays == []


# --- 8. Lock persistence failure ---------------------------------------------------------------


def test_lock_persistence_failure_raises_safe_error_and_does_not_return_success(
    tmp_path: Path,
) -> None:
    repo, _ = _seed_operational(tmp_path)

    def _failing_lock() -> None:
        raise sqlite3.OperationalError("injected commit failure")  # no secret in the text

    repo.mark_reactivation_required = _failing_lock  # type: ignore[method-assign]
    sleeper = _ScriptedSleeper(max_waits=5)
    # A confirmed rejection, then a Valid that must never be requested after the failed lock.
    client = _FakeValidationClient([_confirmed(), _valid()])

    with pytest.raises(CredentialLockPersistenceError) as exc_info:
        asyncio.run(_monitor(repo, client, sleeper).run())

    assert len(client.calls) == 1  # no second validation request after the failure
    # The secret appears in neither the raised error nor its chained cause.
    assert FAKE_SECRET not in str(exc_info.value)
    assert FAKE_SECRET not in repr(exc_info.value)
    assert FAKE_SECRET not in str(exc_info.value.__cause__)


# --- 9. Cancellation during interval wait ------------------------------------------------------


def test_cancellation_while_waiting_propagates_and_stops_further_requests(tmp_path: Path) -> None:
    async def _scenario() -> None:
        repo, _ = _seed_operational(tmp_path)
        sleeper = _BlockingSleeper()
        client = _FakeValidationClient([_valid()])
        task = asyncio.ensure_future(_monitor(repo, client, sleeper).run())

        await sleeper.entered.wait()  # the loop validated once, then began waiting
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(client.calls) == 1  # no further request after cancellation
        assert not client.closed  # the injected client is never closed by the monitor

    asyncio.run(_scenario())


# --- 10. Cancellation during the HTTP request --------------------------------------------------


def test_cancellation_during_request_propagates_and_mutates_no_state(tmp_path: Path) -> None:
    repo, _ = _seed_operational(tmp_path)
    started = asyncio.Event()

    class _CancellingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def validate(
            self, device_id: str, shared_secret: SecretStr
        ) -> CredentialValidationResult:
            self.calls += 1
            started.set()
            raise asyncio.CancelledError

        async def aclose(self) -> None:  # pragma: no cover - never called
            pass

    client = _CancellingClient()
    sleeper = _ScriptedSleeper(max_waits=1)
    monitor = _monitor(repo, client, sleeper)  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.run())

    assert client.calls == 1
    after = repo.load()
    assert after is not None
    assert after.operational_state is OperationalState.OPERATIONAL  # unchanged
    assert after.shared_secret is not None


# --- 11. No overlap ----------------------------------------------------------------------------


def test_requests_never_overlap_even_when_the_first_blocks(tmp_path: Path) -> None:
    async def _scenario() -> None:
        repo, _ = _seed_operational(tmp_path)
        release = asyncio.Event()
        entered = asyncio.Event()

        class _BlockingClient:
            def __init__(self) -> None:
                self.concurrent = 0
                self.max_concurrent = 0
                self.calls = 0

            async def validate(
                self, device_id: str, shared_secret: SecretStr
            ) -> CredentialValidationResult:
                self.calls += 1
                self.concurrent += 1
                self.max_concurrent = max(self.max_concurrent, self.concurrent)
                entered.set()
                try:
                    await release.wait()  # hold the first request open
                    return _valid()
                finally:
                    self.concurrent -= 1

            async def aclose(self) -> None:  # pragma: no cover
                pass

        client = _BlockingClient()
        sleeper = _ScriptedSleeper(max_waits=1)
        monitor = _monitor(repo, client, sleeper)  # type: ignore[arg-type]
        task = asyncio.ensure_future(monitor.run())

        await entered.wait()  # the first request is in flight
        # Give the loop ample opportunity to (wrongly) start a second request; it must not.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert client.calls == 1
        assert client.max_concurrent == 1

        release.set()  # let the first request finish; the loop then hits the stopping wait
        with pytest.raises(_StopLoop):
            await task
        assert client.max_concurrent == 1  # never more than one request at a time

    asyncio.run(_scenario())


# --- 12. Exact interval use --------------------------------------------------------------------


def test_the_configured_interval_controls_the_wait_not_the_http_timeout(tmp_path: Path) -> None:
    repo, _ = _seed_operational(tmp_path)
    distinct_interval = 45.0  # neither the default 30 nor the 10s HTTP timeout
    sleeper = _ScriptedSleeper(max_waits=2)
    client = _FakeValidationClient([_valid(), _valid()])

    with pytest.raises(_StopLoop):
        asyncio.run(_monitor(repo, client, sleeper, interval=distinct_interval).run())

    assert sleeper.delays == [distinct_interval, distinct_interval]
    assert HTTP_TIMEOUT not in sleeper.delays  # the HTTP timeout is never used as the interval


# --- 13. No activation behaviour ---------------------------------------------------------------


def test_monitor_never_activates_or_reads_a_key(tmp_path: Path) -> None:
    repo, _ = _seed_operational(tmp_path)
    sleeper = _ScriptedSleeper(max_waits=3)
    client = _FakeValidationClient([_valid(), _indeterminate(), _confirmed()])

    result = asyncio.run(_monitor(repo, client, sleeper).run())

    assert result.is_reactivation_required
    # The client is a validation-only fake with no activation surface, and the monitor exposes none.
    assert not hasattr(client, "activate")
    assert not hasattr(CredentialValidationMonitor, "activate")
    # It only ever calls the validation client; no request path is the activation endpoint.
    assert VALIDATION_PATH == "/api/v1/device/credentials/validate"


# --- 14. Secret safety -------------------------------------------------------------------------


def test_logs_and_terminal_result_contain_no_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo, _ = _seed_operational(tmp_path)
    sleeper = _ScriptedSleeper(max_waits=5)
    client = _FakeValidationClient([_valid(), _indeterminate(), _confirmed()])

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent.validation.monitor"):
        result = asyncio.run(_monitor(repo, client, sleeper).run())

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    rendered += "\n" + "\n".join(str(record.__dict__) for record in caplog.records)
    assert FAKE_SECRET not in rendered
    assert FAKE_SECRET not in repr(result)
    assert FAKE_SECRET not in str(result)


# --- 15. Reopen persistence integration --------------------------------------------------------


def test_confirmed_rejection_lock_survives_database_reopen(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_operational_identity())
    before = DeviceIdentityRepository(db).load()
    assert before is not None and before.operational_state is OperationalState.OPERATIONAL

    sleeper = _ScriptedSleeper(max_waits=5)
    client = _FakeValidationClient([_confirmed()])
    result = asyncio.run(_monitor(DeviceIdentityRepository(db), client, sleeper).run())
    assert result.is_reactivation_required

    # Close and reopen the database via a brand-new repository/connection: the lock is durable.
    reopened = DeviceIdentityRepository(db).load()
    assert reopened is not None
    assert reopened.operational_state is OperationalState.REACTIVATION_REQUIRED
    assert reopened.shared_secret is None  # secret remains NULL
    assert reopened.device_id == before.device_id
    assert reopened.activated_at == before.activated_at
    assert reopened.last_activated_at == before.last_activated_at


# --- Extra: interval guard ---------------------------------------------------------------------


@pytest.mark.parametrize("bad_interval", [0.0, -1.0])
def test_non_positive_interval_is_rejected(tmp_path: Path, bad_interval: float) -> None:
    repo, _ = _seed_operational(tmp_path)
    client = _FakeValidationClient([_valid()])
    sleeper = _ScriptedSleeper(max_waits=1)
    with pytest.raises(ValueError):
        _monitor(repo, client, sleeper, interval=bad_interval)
