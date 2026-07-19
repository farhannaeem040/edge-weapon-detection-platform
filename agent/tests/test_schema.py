"""Unit tests for the Agent SQLite schema and versioned initialization (IP-02 T-35, §7, §9, §16.1).

Schema shape is verified through SQLite metadata (``sqlite_master``, ``PRAGMA table_info`` /
``index_list``) rather than by matching implementation-private SQL strings, so the tests assert the
*behaviour* IP-02 §7 fixes. Every test uses a temporary database under ``tmp_path``; none touches
``/opt`` or the network.
"""

from __future__ import annotations

import logging
import sqlite3
import stat
from pathlib import Path

import pytest

from weapon_detection_agent.config.paths import AgentPaths, modes_enforceable, resolve_paths
from weapon_detection_agent.persistence import schema as schema_module
from weapon_detection_agent.persistence.database import open_connection
from weapon_detection_agent.persistence.schema import (
    CURRENT_SCHEMA_VERSION,
    InvalidSchemaStateError,
    UnsupportedSchemaVersionError,
    initialize_database,
    initialize_schema,
    read_schema_version,
)

requires_posix_modes = pytest.mark.skipif(
    not modes_enforceable(),
    reason="POSIX permission modes are not enforceable on this platform (IP-02 §17)",
)

APPLICATION_TABLES = ("SchemaVersion", "DeviceIdentity", "ConfigCache")

# A recognisable fake secret used to prove no stored value ever reaches an error message or a log.
# Not a real credential (IP-02 §10 forbids committing real ones); a sentinel string only.
FAKE_SECRET_SENTINEL = "ZZZ-not-a-real-secret-sentinel-ZZZ"


def _provisioned_paths(tmp_path: Path) -> AgentPaths:
    return resolve_paths(tmp_path / "weapon-detection").provision()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    # PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk)
    return {row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})")}


# --- 9-12. A fresh database initializes with the three tables ----------------------------------


def test_initialize_creates_the_three_tables(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    version = initialize_database(paths.database_file)

    assert version == 1
    with open_connection(paths.database_file) as connection:
        assert _table_names(connection) == set(APPLICATION_TABLES)


# --- 13-16. Exact columns, types, primary keys, nullability ------------------------------------


def test_device_identity_columns_match_ip02(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        columns = _columns(connection, "DeviceIdentity")

    assert list(columns) == [
        "SingletonGuard",
        "DeviceId",
        "ProtectedSharedSecret",
        "ActivatedAt",
        "LastActivatedAt",
    ]
    assert columns["SingletonGuard"]["type"] == "INTEGER"
    assert columns["SingletonGuard"]["pk"] == 1
    for text_col in ("DeviceId", "ProtectedSharedSecret", "ActivatedAt", "LastActivatedAt"):
        assert columns[text_col]["type"] == "TEXT"
        assert columns[text_col]["notnull"] == 1  # NOT NULL


def test_config_cache_columns_match_ip02(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        columns = _columns(connection, "ConfigCache")

    assert list(columns) == ["SingletonGuard", "ConfigJson", "UpdatedAt"]
    assert columns["SingletonGuard"]["type"] == "INTEGER"
    assert columns["SingletonGuard"]["pk"] == 1
    for text_col in ("ConfigJson", "UpdatedAt"):
        assert columns[text_col]["type"] == "TEXT"
        assert columns[text_col]["notnull"] == 1


def test_schema_version_columns_match_ip02(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        columns = _columns(connection, "SchemaVersion")

    assert list(columns) == ["Version"]
    assert columns["Version"]["type"] == "INTEGER"
    assert columns["Version"]["notnull"] == 1
    assert columns["Version"]["pk"] == 0  # SchemaVersion has no primary key (IP-02 §7)


# --- 17. Singleton CHECK constraint + no speculative indexes -----------------------------------


@pytest.mark.parametrize("table", ("DeviceIdentity", "ConfigCache"))
def test_singleton_guard_rejects_second_row(tmp_path: Path, table: str) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    columns = (
        "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt)"
        if table == "DeviceIdentity"
        else "(SingletonGuard, ConfigJson, UpdatedAt)"
    )
    body = "'dev', 'sec', 't', 't'" if table == "DeviceIdentity" else "'{}', 't'"

    with open_connection(paths.database_file) as connection:
        connection.execute(f"INSERT INTO {table} {columns} VALUES (1, {body})")
        # A second SingletonGuard = 1 row collides on the primary key.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} {columns} VALUES (1, {body})")


@pytest.mark.parametrize("table", ("DeviceIdentity", "ConfigCache"))
def test_singleton_guard_rejects_non_one_value(tmp_path: Path, table: str) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    columns = (
        "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt)"
        if table == "DeviceIdentity"
        else "(SingletonGuard, ConfigJson, UpdatedAt)"
    )
    body = "'dev', 'sec', 't', 't'" if table == "DeviceIdentity" else "'{}', 't'"

    with open_connection(paths.database_file) as connection:
        # CHECK (SingletonGuard = 1) rejects any other guard value.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} {columns} VALUES (2, {body})")


def test_no_speculative_indexes_created(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        for table in APPLICATION_TABLES:
            indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()
            # IP-02 §7 defines no explicit indexes; INTEGER PRIMARY KEY is a rowid alias, not an
            # index. So no user index should exist on any table.
            assert indexes == []


# --- 18. Version recorded as 1 -----------------------------------------------------------------


def test_schema_version_recorded_as_one(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        assert read_schema_version(connection) == 1
        rows = connection.execute("SELECT Version FROM SchemaVersion").fetchall()
        assert len(rows) == 1 and rows[0][0] == 1


# --- 19-20. Idempotency; existing rows survive -------------------------------------------------


def test_initialization_is_idempotent(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    assert initialize_database(paths.database_file) == 1
    # A second run against the same database is a safe no-op returning the same version.
    assert initialize_database(paths.database_file) == 1

    with open_connection(paths.database_file) as connection:
        assert _table_names(connection) == set(APPLICATION_TABLES)
        # Still exactly one version row — the initializer did not insert a second.
        (count,) = connection.execute("SELECT COUNT(*) FROM SchemaVersion").fetchone()
        assert count == 1


def test_existing_rows_survive_reinitialization(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(
            "INSERT INTO DeviceIdentity "
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt) "
            "VALUES (1, 'device-xyz', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (FAKE_SECRET_SENTINEL,),
        )

    # Re-initialize; the row must be preserved untouched.
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        row = connection.execute("SELECT DeviceId FROM DeviceIdentity").fetchone()
        assert row["DeviceId"] == "device-xyz"


# --- 21-22. No seeded rows ---------------------------------------------------------------------


def test_initialization_seeds_no_identity_or_config_rows(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        (identities,) = connection.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()
        (configs,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()

    assert identities == 0
    assert configs == 0


# --- 23. Newer unsupported version rejected ----------------------------------------------------


def test_newer_schema_version_is_rejected(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute("UPDATE SchemaVersion SET Version = ?", (CURRENT_SCHEMA_VERSION + 1,))

        with pytest.raises(UnsupportedSchemaVersionError):
            initialize_schema(connection)

        # Nothing was modified: the newer version is left exactly as found.
        assert connection.execute("SELECT Version FROM SchemaVersion").fetchone()[0] == 2


# --- 24. Invalid schema-version state rejected -------------------------------------------------


def test_missing_version_row_is_invalid(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute("DELETE FROM SchemaVersion")
        with pytest.raises(InvalidSchemaStateError):
            initialize_schema(connection)


def test_multiple_version_rows_are_invalid(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute("INSERT INTO SchemaVersion (Version) VALUES (1)")
        with pytest.raises(InvalidSchemaStateError):
            initialize_schema(connection)


def test_non_positive_version_is_invalid(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute("UPDATE SchemaVersion SET Version = 0")
        with pytest.raises(InvalidSchemaStateError):
            initialize_schema(connection)


# --- 25. Failed migration rolls back all partial schema changes --------------------------------


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _provisioned_paths(tmp_path)

    # Substitute a migration whose second step is malformed SQL, so it fails mid-transaction after a
    # first table has been created (IP-02 §16 permits a controlled hook for this).
    monkeypatch.setattr(
        schema_module,
        "SCHEMA_V1_STATEMENTS",
        ("CREATE TABLE Canary (id INTEGER)", "CREATE TABLE Broken ("),
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(paths.database_file)

    # A fresh connection confirms nothing persisted — not the Canary table, not SchemaVersion.
    with open_connection(paths.database_file) as connection:
        assert _table_names(connection) == set()


# --- 26. Only agent.db is created; no deferred files/directories -------------------------------


def test_initialization_creates_only_the_database_file(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    # The managed layout is unchanged apart from agent.db appearing in database/.
    assert sorted(child.name for child in paths.root.iterdir()) == ["config", "database", "logs"]
    assert [child.name for child in paths.database_dir.iterdir()] == ["agent.db"]
    for deferred in ("snapshots", "recordings", "models", "pipeline", "runtime"):
        assert not (paths.root / deferred).exists()


# --- 27-28. Database file mode on POSIX; skipped on Windows -------------------------------------


@requires_posix_modes
def test_initialized_database_file_mode_is_owner_only(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    assert stat.S_IMODE(paths.database_file.stat().st_mode) == 0o600


# --- 29. No network access during initialization -----------------------------------------------


def test_initialization_performs_no_network_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def _forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema initialization must not open a socket")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    assert initialize_database(paths_db := _provisioned_paths(tmp_path).database_file) == 1
    assert paths_db.exists()


# --- 30. No secret or row value appears in errors or captured logs -----------------------------


def test_errors_and_logs_never_expose_stored_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = _provisioned_paths(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(
            "INSERT INTO DeviceIdentity "
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt) "
            "VALUES (1, 'device-xyz', ?, 't', 't')",
            (FAKE_SECRET_SENTINEL,),
        )
        # Force an invalid state whose error message must not echo any stored value.
        connection.execute("INSERT INTO SchemaVersion (Version) VALUES (1)")
        with pytest.raises(InvalidSchemaStateError) as excinfo:
            initialize_schema(connection)

    assert FAKE_SECRET_SENTINEL not in str(excinfo.value)
    assert FAKE_SECRET_SENTINEL not in caplog.text
    # The initialization log carried only structural events, never a row value.
    assert "device-xyz" not in caplog.text
