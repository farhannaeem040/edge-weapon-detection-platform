"""Unit tests for the activation orchestration service (IP-02 T-38, §9, §12, §14).

The Backend client is a fake (no network); the Device Identity repository is the **real** T-36
repository over a temporary SQLite database, except where a deliberate persistence failure is
injected. A fixed clock makes timestamps deterministic. Fake keys and secrets are used throughout
and must never surface in a repr, log, or error.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import logging
import socket
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.activation.errors import (
    ActivationKeyMissingError,
    ActivationOutcomeAmbiguousError,
    ActivationPersistenceError,
    ActivationRejectedError,
    ActivationServerError,
    ActivationTimeoutError,
    ActivationTransportError,
    DeviceIdentityMismatchError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import (
    ActivationOutcome,
    ActivationService,
)
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.database import connect, open_connection, transaction
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity

DEVICE_ID = "device-test-abc"
OTHER_DEVICE_ID = "device-test-different"
FAKE_SECRET_1 = "test-secret-one-ZZZ"
FAKE_SECRET_2 = "test-secret-two-ZZZ"
FAKE_KEY = "keyid.test-key-secret-ZZZ"
BRANCH_ID = "branch-test-xyz"
T_FIRST = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
T_LATER = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)


# --- Fakes and helpers -------------------------------------------------------------------------


class FakeBackendClient:
    """Duck-typed stand-in for BackendActivationClient; records calls, returns/raises per config."""

    def __init__(self, *, result: ActivationResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls = 0
        self.received_key: SecretStr | None = None

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.calls += 1
        self.received_key = activation_key
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FailingRepository:
    """A repository whose write fails, to exercise the Backend-success/persistence-failure path."""

    def __init__(
        self,
        *,
        existing: DeviceIdentity | None,
        fail_store: bool = False,
        fail_replace: bool = False,
    ):
        self._existing = existing
        self._fail_store = fail_store
        self._fail_replace = fail_replace
        self.store_calls = 0
        self.replace_calls = 0

    def load(self) -> DeviceIdentity | None:
        return self._existing

    def store(self, identity: DeviceIdentity) -> None:
        self.store_calls += 1
        if self._fail_store:
            raise RepositoryError("injected store failure")

    def replace_shared_secret(
        self, *, shared_secret: SecretStr, last_activated_at: datetime
    ) -> None:
        self.replace_calls += 1
        if self._fail_replace:
            raise RepositoryError("injected replace failure")


def _result(
    device_id: str = DEVICE_ID, secret: str = FAKE_SECRET_1, branch: str = BRANCH_ID
) -> ActivationResult:
    return ActivationResult(device_id=device_id, shared_secret=SecretStr(secret), branch_id=branch)


def _paths(tmp_path: Path) -> AgentPaths:
    return resolve_paths(tmp_path / "weapon-detection").provision()


def _ready(tmp_path: Path) -> tuple[DeviceIdentityRepository, AgentPaths]:
    paths = _paths(tmp_path)
    initialize_database(paths.database_file)
    return DeviceIdentityRepository(paths.database_file), paths


def _file_resolver(paths: AgentPaths, key: str = FAKE_KEY) -> ActivationKeyResolver:
    paths.activation_key_file.write_text(key, encoding="utf-8")
    return ActivationKeyResolver(environment_key=None, key_file_path=paths.activation_key_file)


def _env_resolver(paths: AgentPaths, key: str = FAKE_KEY) -> ActivationKeyResolver:
    return ActivationKeyResolver(
        environment_key=SecretStr(key), key_file_path=paths.activation_key_file
    )


def _no_key_resolver(paths: AgentPaths) -> ActivationKeyResolver:
    return ActivationKeyResolver(environment_key=None, key_file_path=paths.activation_key_file)


def _service(
    backend: object, repo: object, resolver: ActivationKeyResolver, clock: object = lambda: T_FIRST
) -> ActivationService:
    return ActivationService(
        backend_client=backend,  # type: ignore[arg-type]
        identity_repository=repo,  # type: ignore[arg-type]
        key_resolver=resolver,
        clock=clock,  # type: ignore[arg-type]
    )


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _prestore(repo: DeviceIdentityRepository, *, secret: str = FAKE_SECRET_1) -> None:
    repo.store(
        DeviceIdentity(
            device_id=DEVICE_ID,
            shared_secret=SecretStr(secret),
            activated_at=T_FIRST,
            last_activated_at=T_FIRST,
        )
    )


def _seed_config(paths: AgentPaths, config_json: str = '{"seeded": true}') -> None:
    with open_connection(paths.database_file) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
                (config_json, T_FIRST.isoformat()),
            )


def _config_row(paths: AgentPaths) -> tuple[str, str] | None:
    with open_connection(paths.database_file) as connection:
        row = connection.execute("SELECT ConfigJson, UpdatedAt FROM ConfigCache").fetchone()
    return None if row is None else (row["ConfigJson"], row["UpdatedAt"])


# --- 15-16. First activation: missing key ------------------------------------------------------


def test_first_activation_missing_key_raises(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    backend = FakeBackendClient(result=_result())
    service = _service(backend, repo, _no_key_resolver(paths))

    with pytest.raises(ActivationKeyMissingError):
        _run(service.activate())

    assert backend.calls == 0
    assert repo.load() is None


# --- 17-26. First activation: success ---------------------------------------------------------


def test_first_activation_calls_backend_once_and_stores(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    backend = FakeBackendClient(result=_result())
    service = _service(backend, repo, _file_resolver(paths))

    result = _run(service.activate())

    assert backend.calls == 1
    assert result.outcome is ActivationOutcome.FIRST_ACTIVATION
    stored = repo.load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID


def test_first_activation_passes_resolved_key_as_secretstr(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    backend = FakeBackendClient(result=_result())
    _run(_service(backend, repo, _file_resolver(paths, key=FAKE_KEY)).activate())

    assert isinstance(backend.received_key, SecretStr)
    assert backend.received_key.get_secret_value() == FAKE_KEY


def test_first_activation_stores_secret_and_equal_timestamps(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    backend = FakeBackendClient(result=_result())
    _run(_service(backend, repo, _file_resolver(paths), clock=lambda: T_FIRST).activate())

    stored = repo.load()
    assert stored is not None
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_1
    assert stored.activated_at == T_FIRST
    assert stored.last_activated_at == T_FIRST  # ActivatedAt == LastActivatedAt on first activation


def test_first_activation_returns_branch_id_in_result_only(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    backend = FakeBackendClient(result=_result())
    result = _run(_service(backend, repo, _file_resolver(paths)).activate())

    assert result.branch_id == BRANCH_ID
    # The persisted identity has no branch id — the schema does not carry one.
    assert not hasattr(repo.load(), "branch_id")


def test_first_activation_deletes_file_key_after_success(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    resolver = _file_resolver(paths)
    _run(_service(FakeBackendClient(result=_result()), repo, resolver).activate())

    assert not paths.activation_key_file.exists()


def test_first_activation_env_key_does_not_delete_file(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    resolver = _env_resolver(paths)

    _run(_service(FakeBackendClient(result=_result()), repo, resolver).activate())

    assert paths.activation_key_file.exists()  # env-sourced key never deletes the file


@pytest.mark.parametrize(
    "error",
    [
        ActivationRejectedError(status_code=401, error_code="INVALID_ACTIVATION_KEY"),
        ActivationTransportError("no route"),
        ActivationServerError(status_code=503),
        InvalidActivationResponseError("bad body"),
    ],
)
def test_first_activation_backend_failure_leaves_db_and_file_unchanged(
    tmp_path: Path, error: Exception
) -> None:
    repo, paths = _ready(tmp_path)
    resolver = _file_resolver(paths)
    backend = FakeBackendClient(error=error)

    with pytest.raises(type(error)):
        _run(_service(backend, repo, resolver).activate())

    assert backend.calls == 1  # exactly one request, no retry
    assert repo.load() is None
    assert paths.activation_key_file.exists()


def test_first_activation_timeout_maps_to_ambiguous(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    resolver = _file_resolver(paths)
    backend = FakeBackendClient(error=ActivationTimeoutError("timed out"))

    with pytest.raises(ActivationOutcomeAmbiguousError):
        _run(_service(backend, repo, resolver).activate())

    assert backend.calls == 1
    assert repo.load() is None
    assert paths.activation_key_file.exists()


# --- 33-37. First activation: persistence and cleanup failures --------------------------------


def test_first_activation_persistence_failure_keeps_file_and_no_retry(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    initialize_database(paths.database_file)
    resolver = _file_resolver(paths)
    backend = FakeBackendClient(result=_result())
    repo = FailingRepository(existing=None, fail_store=True)

    with pytest.raises(ActivationPersistenceError):
        _run(_service(backend, repo, resolver).activate())

    assert backend.calls == 1  # no second Backend call
    assert repo.store_calls == 1
    assert paths.activation_key_file.exists()  # file intact


def test_first_activation_cleanup_failure_reported_but_identity_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from weapon_detection_agent.activation.errors import ActivationKeyCleanupError

    repo, paths = _ready(tmp_path)
    resolver = _file_resolver(paths)

    def _raise() -> None:
        raise ActivationKeyCleanupError("cleanup failed")

    monkeypatch.setattr(resolver, "delete_key_file", _raise)

    with pytest.raises(ActivationKeyCleanupError):
        _run(_service(FakeBackendClient(result=_result()), repo, resolver).activate())

    # Identity persistence is committed and must not be rolled back by a cleanup failure.
    assert repo.load() is not None


# --- 38-42. Already activated / no key (no-op) ------------------------------------------------


def test_already_activated_no_key_makes_no_backend_call(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    backend = FakeBackendClient(result=_result())

    result = _run(_service(backend, repo, _no_key_resolver(paths)).activate())

    assert backend.calls == 0
    assert result.outcome is ActivationOutcome.ALREADY_ACTIVATED


def test_already_activated_no_key_does_not_change_identity(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    _run(_service(FakeBackendClient(result=_result()), repo, _no_key_resolver(paths)).activate())

    stored = repo.load()
    assert stored is not None
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_1
    assert stored.activated_at == T_FIRST
    assert stored.last_activated_at == T_FIRST


def test_already_activated_no_key_leaves_config_cache_unchanged(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    _seed_config(paths)
    before = _config_row(paths)

    _run(_service(FakeBackendClient(result=_result()), repo, _no_key_resolver(paths)).activate())

    assert _config_row(paths) == before


# --- 43-50. Reactivation: success -------------------------------------------------------------


def test_reactivation_replaces_secret_and_preserves_identity(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo, secret=FAKE_SECRET_1)
    backend = FakeBackendClient(result=_result(secret=FAKE_SECRET_2))
    resolver = _file_resolver(paths)

    result = _run(_service(backend, repo, resolver, clock=lambda: T_LATER).activate())

    assert backend.calls == 1
    assert result.outcome is ActivationOutcome.REACTIVATION
    stored = repo.load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID  # unchanged
    assert stored.activated_at == T_FIRST  # original preserved
    assert stored.last_activated_at == T_LATER  # advanced by the clock
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_2  # replaced


def test_reactivation_deletes_file_key_after_success(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    resolver = _file_resolver(paths)

    _run(
        _service(
            FakeBackendClient(result=_result(secret=FAKE_SECRET_2)),
            repo,
            resolver,
            clock=lambda: T_LATER,
        ).activate()
    )

    assert not paths.activation_key_file.exists()


def test_reactivation_env_key_leaves_file_untouched(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    resolver = _env_resolver(paths)

    _run(
        _service(
            FakeBackendClient(result=_result(secret=FAKE_SECRET_2)),
            repo,
            resolver,
            clock=lambda: T_LATER,
        ).activate()
    )

    assert paths.activation_key_file.exists()


def test_reactivation_leaves_config_cache_unchanged(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    _seed_config(paths)
    before = _config_row(paths)

    _run(
        _service(
            FakeBackendClient(result=_result(secret=FAKE_SECRET_2)),
            repo,
            _file_resolver(paths),
            clock=lambda: T_LATER,
        ).activate()
    )

    assert _config_row(paths) == before


# --- 51-54. Reactivation: Device ID mismatch --------------------------------------------------


def test_reactivation_device_mismatch_raises_and_preserves_everything(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo, secret=FAKE_SECRET_1)
    resolver = _file_resolver(paths)
    backend = FakeBackendClient(result=_result(device_id=OTHER_DEVICE_ID, secret=FAKE_SECRET_2))

    with pytest.raises(DeviceIdentityMismatchError):
        _run(_service(backend, repo, resolver, clock=lambda: T_LATER).activate())

    stored = repo.load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID  # not replaced
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_1  # not modified
    assert stored.activated_at == T_FIRST
    assert stored.last_activated_at == T_FIRST  # timestamps unchanged
    assert paths.activation_key_file.exists()  # key file not deleted


# --- 55-60. Reactivation: persistence failure and timeout -------------------------------------


class _CommitFailingConnection:
    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        if str(sql).strip().upper().startswith("COMMIT"):
            raise sqlite3.OperationalError("injected commit failure")
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._real.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@contextmanager
def _commit_failing(path: Path) -> Iterator[_CommitFailingConnection]:
    real = connect(path)
    try:
        yield _CommitFailingConnection(real)
    finally:
        real.close()


def test_reactivation_replacement_failure_rolls_back(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo, secret=FAKE_SECRET_1)
    resolver = _file_resolver(paths)
    # A repo whose COMMIT fails during the replacement transaction.
    failing_repo = DeviceIdentityRepository(
        connection_factory=lambda: _commit_failing(paths.database_file)
    )
    backend = FakeBackendClient(result=_result(secret=FAKE_SECRET_2))

    with pytest.raises(ActivationPersistenceError):
        _run(_service(backend, failing_repo, resolver, clock=lambda: T_LATER).activate())

    # The prior secret survives, and the key file remains.
    stored = repo.load()
    assert stored is not None
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_1
    assert stored.last_activated_at == T_FIRST
    assert paths.activation_key_file.exists()


def test_reactivation_timeout_leaves_identity_and_file(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo, secret=FAKE_SECRET_1)
    resolver = _file_resolver(paths)
    backend = FakeBackendClient(error=ActivationTimeoutError("timed out"))

    with pytest.raises(ActivationOutcomeAmbiguousError):
        _run(_service(backend, repo, resolver, clock=lambda: T_LATER).activate())

    assert backend.calls == 1
    stored = repo.load()
    assert stored is not None
    assert stored.shared_secret.get_secret_value() == FAKE_SECRET_1
    assert stored.last_activated_at == T_FIRST
    assert paths.activation_key_file.exists()


# --- 61-68. Security -------------------------------------------------------------------------


def test_key_and_secret_absent_from_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo, paths = _ready(tmp_path)
    resolver = _file_resolver(paths, key=FAKE_KEY)
    backend = FakeBackendClient(result=_result(secret=FAKE_SECRET_1))

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        _run(_service(backend, repo, resolver).activate())

    assert FAKE_KEY not in caplog.text
    assert FAKE_SECRET_1 not in caplog.text
    for record in caplog.records:
        assert FAKE_KEY not in str(record.__dict__)
        assert FAKE_SECRET_1 not in str(record.__dict__)


def test_secret_absent_from_service_result_repr(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    result = _run(
        _service(
            FakeBackendClient(result=_result(secret=FAKE_SECRET_1)), repo, _file_resolver(paths)
        ).activate()
    )

    assert FAKE_SECRET_1 not in repr(result)
    assert FAKE_SECRET_1 not in str(result)


def test_secret_absent_from_persistence_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    initialize_database(paths.database_file)
    repo = FailingRepository(existing=None, fail_store=True)
    backend = FakeBackendClient(result=_result(secret=FAKE_SECRET_1))

    with pytest.raises(ActivationPersistenceError) as excinfo:
        _run(_service(backend, repo, _file_resolver(paths)).activate())

    assert FAKE_SECRET_1 not in str(excinfo.value)


def test_config_json_absent_from_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo, paths = _ready(tmp_path)
    _prestore(repo)
    _seed_config(paths, config_json='{"marker": "CONFIG-SENTINEL-ZZZ"}')

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        _run(
            _service(FakeBackendClient(result=_result()), repo, _no_key_resolver(paths)).activate()
        )

    assert "CONFIG-SENTINEL-ZZZ" not in caplog.text


# --- 69-75. Import and boundary ---------------------------------------------------------------


def test_import_service_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing the service must not perform I/O")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(sqlite3, "connect", _forbidden)

    module_names = (
        "weapon_detection_agent.activation.service",
        "weapon_detection_agent.activation.key_resolver",
        "weapon_detection_agent.activation",
    )
    saved = {name: sys.modules.pop(name) for name in module_names if name in sys.modules}
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_importing_main_remains_side_effect_free() -> None:
    module = importlib.reload(importlib.import_module("weapon_detection_agent.main"))
    assert module.app.title == "Weapon Detection Agent"


def test_service_does_not_create_config_cache_rows(tmp_path: Path) -> None:
    repo, paths = _ready(tmp_path)
    _run(_service(FakeBackendClient(result=_result()), repo, _file_resolver(paths)).activate())

    with open_connection(paths.database_file) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()
    assert count == 0  # T-38 never writes ConfigCache (OI-2)
