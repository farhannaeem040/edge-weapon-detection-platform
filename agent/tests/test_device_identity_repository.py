"""Unit tests for the Device Identity repository (IP-02 T-36, §9, §10, §16.1).

Every test resolves a temporary root (``tmp_path``), provisions it (T-34), initializes the schema
(T-35), then constructs the repository with the explicit database path — never ``/opt``. A fixed
fake secret sentinel is used only for internal assertions; it must never appear in captured logs,
reprs, or error messages, which is exactly what several tests assert.
"""

from __future__ import annotations

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

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.database import connect, open_connection, transaction
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import (
    IdentityAlreadyExistsError,
    InvalidIdentityStateError,
)
from weapon_detection_agent.persistence.models import DeviceIdentity

# A recognisable non-credential sentinel — never a real secret (IP-02 §10 forbids committing one).
FAKE_SECRET_A = "ZZZ-fake-secret-A-must-never-appear-ZZZ"
FAKE_SECRET_B = "ZZZ-fake-secret-B-must-never-appear-ZZZ"

DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER_AT = datetime(2026, 6, 7, 8, 9, 10, tzinfo=timezone.utc)


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _identity(
    *,
    device_id: str = DEVICE_ID,
    secret: str = FAKE_SECRET_A,
    activated_at: datetime = ACTIVATED_AT,
    last_activated_at: datetime = ACTIVATED_AT,
) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=device_id,
        shared_secret=SecretStr(secret),
        activated_at=activated_at,
        last_activated_at=last_activated_at,
    )


# --- 1-7. Store / load round-trip and singleton ------------------------------------------------


def test_absent_identity_returns_none(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))

    assert repo.load() is None


def test_first_store_succeeds_and_loads(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))

    repo.store(_identity())
    loaded = repo.load()

    assert loaded is not None
    assert loaded.device_id == DEVICE_ID


def test_loaded_secret_uses_safe_type(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    loaded = repo.load()

    assert loaded is not None
    assert isinstance(loaded.shared_secret, SecretStr)
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_A


def test_activated_at_round_trips(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity(activated_at=ACTIVATED_AT, last_activated_at=LATER_AT))

    loaded = repo.load()

    assert loaded is not None
    assert loaded.activated_at == ACTIVATED_AT


def test_last_activated_at_round_trips(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity(activated_at=ACTIVATED_AT, last_activated_at=LATER_AT))

    loaded = repo.load()

    assert loaded is not None
    assert loaded.last_activated_at == LATER_AT


def test_exactly_one_row_after_store(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())

    with open_connection(db) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()
        (guard,) = connection.execute("SELECT SingletonGuard FROM DeviceIdentity").fetchone()

    assert count == 1
    assert guard == 1


# --- 8-9. Second store is rejected and non-destructive -----------------------------------------


def test_second_store_is_rejected(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    with pytest.raises(IdentityAlreadyExistsError):
        repo.store(_identity(device_id="a-different-device", secret=FAKE_SECRET_B))


def test_rejected_second_store_does_not_modify_first_row(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    with pytest.raises(IdentityAlreadyExistsError):
        repo.store(_identity(device_id="a-different-device", secret=FAKE_SECRET_B))

    loaded = repo.load()
    assert loaded is not None
    # The original identity is untouched — no partial overwrite of id or secret.
    assert loaded.device_id == DEVICE_ID
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_A


# --- 10-14. Atomic secret replacement ----------------------------------------------------------


def test_replace_shared_secret_succeeds(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT)

    loaded = repo.load()
    assert loaded is not None
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_B


def test_replacement_retains_device_id_and_activated_at(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity(activated_at=ACTIVATED_AT, last_activated_at=ACTIVATED_AT))

    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT)

    loaded = repo.load()
    assert loaded is not None
    assert loaded.device_id == DEVICE_ID  # Device ID is permanent (§10)
    assert loaded.activated_at == ACTIVATED_AT  # original activation time preserved


def test_replacement_updates_last_activated_at_only(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity(activated_at=ACTIVATED_AT, last_activated_at=ACTIVATED_AT))

    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT)

    loaded = repo.load()
    assert loaded is not None
    # Only the secret and LastActivatedAt change; id and ActivatedAt stay put.
    assert loaded.last_activated_at == LATER_AT
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_B
    assert loaded.device_id == DEVICE_ID
    assert loaded.activated_at == ACTIVATED_AT


# --- 15. Replacement with no identity fails safely ---------------------------------------------


def test_replace_without_identity_fails(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))

    with pytest.raises(InvalidIdentityStateError):
        repo.replace_shared_secret(
            shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT
        )


# --- 16-17. Forced mid-write failure rolls back; prior secret survives -------------------------


class _CommitFailingConnection:
    """Wraps a real connection but raises when asked to COMMIT — to simulate a crash between the
    UPDATE and the commit, proving the write is atomic."""

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
def _commit_failing_opener(path: Path) -> Iterator[_CommitFailingConnection]:
    real = connect(path)
    try:
        yield _CommitFailingConnection(real)
    finally:
        real.close()  # closing with an open transaction discards the uncommitted UPDATE


def test_failed_replacement_rolls_back_and_preserves_secret(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())

    failing_repo = DeviceIdentityRepository(connection_factory=lambda: _commit_failing_opener(db))
    with pytest.raises(sqlite3.OperationalError):
        failing_repo.replace_shared_secret(
            shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT
        )

    # A fresh, healthy repository shows the original secret — the failed write left no trace.
    loaded = DeviceIdentityRepository(db).load()
    assert loaded is not None
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_A
    assert loaded.last_activated_at == ACTIVATED_AT


# --- 18. Repository remains usable after a handled failure -------------------------------------


def test_repository_usable_after_handled_error(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    with pytest.raises(IdentityAlreadyExistsError):
        repo.store(_identity(secret=FAKE_SECRET_B))

    # After the handled error the database is still usable for reads and a valid replacement.
    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT)
    loaded = repo.load()
    assert loaded is not None
    assert loaded.shared_secret.get_secret_value() == FAKE_SECRET_B


# --- 19-20. Invalid timestamps -----------------------------------------------------------------


def test_naive_datetime_is_rejected_on_store(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    naive = datetime(2026, 1, 2, 3, 4, 5)  # no tzinfo

    with pytest.raises(ValueError):
        repo.store(_identity(activated_at=naive))


def test_malformed_stored_timestamp_is_rejected(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    # Seed a row directly with an unparseable ActivatedAt, bypassing the repository.
    with open_connection(db) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO DeviceIdentity "
                "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt) "
                "VALUES (1, ?, ?, ?, ?)",
                (DEVICE_ID, FAKE_SECRET_A, "not-a-timestamp", "not-a-timestamp"),
            )

    with pytest.raises(InvalidIdentityStateError):
        DeviceIdentityRepository(db).load()


# --- 21-24. Secret never leaks -----------------------------------------------------------------


def test_secret_absent_from_repr_and_str(tmp_path: Path) -> None:
    identity = _identity()

    assert FAKE_SECRET_A not in repr(identity)
    assert FAKE_SECRET_A not in str(identity)


def test_secret_absent_from_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        repo.store(_identity())
        repo.load()
        repo.replace_shared_secret(
            shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=LATER_AT
        )

    # Logging happened, and the device id (a safe identifier) is the structured field carried.
    assert any(record.msg == "device_identity_saved" for record in caplog.records)
    assert getattr(caplog.records[0], "device_id", None) == DEVICE_ID
    # No secret appears in the rendered text or in any record attribute.
    assert FAKE_SECRET_A not in caplog.text
    assert FAKE_SECRET_B not in caplog.text
    for record in caplog.records:
        assert FAKE_SECRET_A not in str(record.__dict__)
        assert FAKE_SECRET_B not in str(record.__dict__)


def test_secret_absent_from_exception_messages(tmp_path: Path) -> None:
    repo = DeviceIdentityRepository(_ready_db(tmp_path))
    repo.store(_identity())

    with pytest.raises(IdentityAlreadyExistsError) as excinfo:
        repo.store(_identity(secret=FAKE_SECRET_B))

    assert FAKE_SECRET_A not in str(excinfo.value)
    assert FAKE_SECRET_B not in str(excinfo.value)


# --- 25. Importing the repository performs no filesystem I/O -----------------------------------


def test_repository_import_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing the repository must not perform I/O")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    module_names = (
        "weapon_detection_agent.persistence",
        "weapon_detection_agent.persistence.errors",
        "weapon_detection_agent.persistence.models",
        "weapon_detection_agent.persistence.database",
        "weapon_detection_agent.persistence.schema",
        "weapon_detection_agent.persistence.device_identity_repository",
        "weapon_detection_agent.persistence.config_cache_repository",
    )
    # Import fresh copies without disturbing the modules other tests already hold: pop the originals
    # aside, re-import, then restore so class identities stay stable across the suite.
    saved = {name: sys.modules.pop(name) for name in module_names if name in sys.modules}
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
