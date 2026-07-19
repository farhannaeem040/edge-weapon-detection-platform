"""Unit tests for the Agent SQLite connection layer (IP-02 T-35, §7, §16.1).

Every test uses a temporary root (``tmp_path``) provisioned through the real T-34 API, never
``/opt``. Mode assertions run only where POSIX modes are representable (IP-02 §17). No test opens a
socket; the persistence layer performs no network I/O at all.
"""

from __future__ import annotations

import builtins
import importlib
import socket
import sqlite3
import stat
from pathlib import Path

import pytest

from weapon_detection_agent.config.paths import AgentPaths, modes_enforceable, resolve_paths
from weapon_detection_agent.persistence.database import (
    DATABASE_FILE_MODE,
    DatabaseInitializationError,
    connect,
    open_connection,
    transaction,
)

requires_posix_modes = pytest.mark.skipif(
    not modes_enforceable(),
    reason="POSIX permission modes are not enforceable on this platform (IP-02 §17)",
)


def _provisioned_paths(tmp_path: Path) -> AgentPaths:
    """Resolve and provision a temporary layout, so the database/ directory exists (T-34)."""
    return resolve_paths(tmp_path / "weapon-detection").provision()


# --- 1-3. Connection opens at the resolved path with the configured settings -------------------


def test_connect_opens_at_database_file(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    connection = connect(paths.database_file)
    try:
        assert paths.database_file.exists()
        # The path came from AgentPaths, not a hard-coded location.
        assert paths.database_file == paths.database_dir / "agent.db"
    finally:
        connection.close()


def test_connection_uses_row_factory(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    with open_connection(paths.database_file) as connection:
        assert connection.row_factory is sqlite3.Row
        row = connection.execute("SELECT 1 AS answer").fetchone()
        # Named-column access confirms the Row factory is actually in effect.
        assert row["answer"] == 1


def test_foreign_keys_pragma_is_enabled(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    with open_connection(paths.database_file) as connection:
        (enabled,) = connection.execute("PRAGMA foreign_keys").fetchone()
        assert enabled == 1


# --- 4-5. Context manager closes predictably; no lingering connection --------------------------


def test_open_connection_closes_on_exit(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    with open_connection(paths.database_file) as connection:
        connection.execute("SELECT 1")

    # After the block the connection is closed; using it raises rather than silently working.
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_open_connection_closes_even_on_error(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    captured: sqlite3.Connection | None = None
    with pytest.raises(RuntimeError):
        with open_connection(paths.database_file) as connection:
            captured = connection
            raise RuntimeError("boom")

    assert captured is not None
    with pytest.raises(sqlite3.ProgrammingError):
        captured.execute("SELECT 1")


# --- 6. No module-level global connection ------------------------------------------------------


def test_no_global_connection_in_persistence_modules() -> None:
    import weapon_detection_agent.persistence as package
    import weapon_detection_agent.persistence.database as database
    import weapon_detection_agent.persistence.schema as schema

    for module in (package, database, schema):
        for value in vars(module).values():
            assert not isinstance(value, sqlite3.Connection)


# --- 7. Missing parent directory fails without provisioning it ---------------------------------


def test_missing_parent_directory_fails_without_creating_it(tmp_path: Path) -> None:
    # Deliberately do NOT provision: the database/ directory does not exist.
    paths = resolve_paths(tmp_path / "weapon-detection")
    assert not paths.database_dir.exists()

    with pytest.raises(DatabaseInitializationError):
        connect(paths.database_file)

    # The failure must not have created the directory or the file (T-34 owns provisioning).
    assert not paths.database_dir.exists()
    assert not paths.database_file.exists()


# --- 8. Importing persistence performs no filesystem or network I/O ----------------------------


def test_import_persistence_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    def _forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing persistence must not open a database")

    def _forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing persistence must not open a file")

    def _forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing persistence must not open a socket")

    monkeypatch.setattr(sqlite3, "connect", _forbidden_connect)
    monkeypatch.setattr(builtins, "open", _forbidden_open)
    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    module_names = (
        "weapon_detection_agent.persistence",
        "weapon_detection_agent.persistence.database",
        "weapon_detection_agent.persistence.schema",
    )
    # Import *fresh* copies (proving a from-scratch import touches no file, db, or socket) without
    # mutating the modules other tests already imported: pop the originals aside, import anew, then
    # restore the originals so their class/function identities stay stable across the suite.
    saved = {name: sys.modules.pop(name) for name in module_names if name in sys.modules}
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# --- Transaction helper: commit / rollback -----------------------------------------------------


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    with open_connection(paths.database_file) as connection:
        connection.execute("CREATE TABLE t (v INTEGER)")
        with transaction(connection):
            connection.execute("INSERT INTO t (v) VALUES (1)")

        (count,) = connection.execute("SELECT COUNT(*) FROM t").fetchone()
        assert count == 1


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    with open_connection(paths.database_file) as connection:
        connection.execute("CREATE TABLE t (v INTEGER)")
        with pytest.raises(RuntimeError):
            with transaction(connection):
                connection.execute("INSERT INTO t (v) VALUES (1)")
                raise RuntimeError("boom")

        # The insert must have been rolled back with the transaction.
        (count,) = connection.execute("SELECT COUNT(*) FROM t").fetchone()
        assert count == 0


# --- File permissions (POSIX only) -------------------------------------------------------------


@requires_posix_modes
def test_database_file_mode_is_owner_only(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    connection = connect(paths.database_file)
    try:
        mode = stat.S_IMODE(paths.database_file.stat().st_mode)
        assert mode == DATABASE_FILE_MODE  # 0o600
        # Nothing is exposed to group or other for a store holding the shared secret.
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    finally:
        connection.close()


@requires_posix_modes
def test_database_file_mode_is_reapplied_on_reconnect(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    connect(paths.database_file).close()
    import os

    os.chmod(paths.database_file, 0o666)  # loosen behind the module's back

    connect(paths.database_file).close()  # a fresh connect must restore the strict mode

    assert stat.S_IMODE(paths.database_file.stat().st_mode) == DATABASE_FILE_MODE
