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

APPLICATION_TABLES = (
    "SchemaVersion",
    "DeviceIdentity",
    "ConfigCache",
    "DetectionEvent",
    "SnapshotOutbox",
)

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

    assert version == CURRENT_SCHEMA_VERSION
    with open_connection(paths.database_file) as connection:
        assert _table_names(connection) == set(APPLICATION_TABLES)


# --- 13-16. Exact columns, types, primary keys, nullability (v2) --------------------------------


def test_device_identity_columns_match_v2(tmp_path: Path) -> None:
    # IP-05 §7: v2 makes ProtectedSharedSecret nullable and adds OperationalState.
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
        "OperationalState",
    ]
    assert columns["SingletonGuard"]["type"] == "INTEGER"
    assert columns["SingletonGuard"]["pk"] == 1
    # ProtectedSharedSecret is now nullable; every other TEXT column stays NOT NULL.
    assert columns["ProtectedSharedSecret"]["type"] == "TEXT"
    assert columns["ProtectedSharedSecret"]["notnull"] == 0
    for text_col in ("DeviceId", "ActivatedAt", "LastActivatedAt", "OperationalState"):
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

    if table == "DeviceIdentity":
        columns = (
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, "
            "ActivatedAt, LastActivatedAt, OperationalState)"
        )
        body = "'dev', 'sec', 't', 't', 'Operational'"
    else:
        columns = "(SingletonGuard, ConfigJson, UpdatedAt)"
        body = "'{}', 't'"

    with open_connection(paths.database_file) as connection:
        connection.execute(f"INSERT INTO {table} {columns} VALUES (1, {body})")
        # A second SingletonGuard = 1 row collides on the primary key.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} {columns} VALUES (1, {body})")


@pytest.mark.parametrize("table", ("DeviceIdentity", "ConfigCache"))
def test_singleton_guard_rejects_non_one_value(tmp_path: Path, table: str) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    if table == "DeviceIdentity":
        columns = (
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, "
            "ActivatedAt, LastActivatedAt, OperationalState)"
        )
        body = "'dev', 'sec', 't', 't', 'Operational'"
    else:
        columns = "(SingletonGuard, ConfigJson, UpdatedAt)"
        body = "'{}', 't'"

    with open_connection(paths.database_file) as connection:
        # CHECK (SingletonGuard = 1) rejects any other guard value.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} {columns} VALUES (2, {body})")


def test_no_speculative_indexes_created(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        for table in ("SchemaVersion", "DeviceIdentity", "ConfigCache"):
            indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()
            # IP-02 §7 defines no explicit indexes; INTEGER PRIMARY KEY is a rowid alias, not an
            # index. So no user index should exist on any of these tables.
            assert indexes == []
        # DetectionEvent gets exactly the one FS-05 §7-justified index (pending-event lookup) and no
        # other. EventId's TEXT PRIMARY KEY is a real index in SQLite (unlike an INTEGER rowid
        # alias), so it appears here too.
        detection_event_indexes = {
            row["name"]
            for row in connection.execute("PRAGMA index_list(DetectionEvent)").fetchall()
        }
        assert detection_event_indexes == {
            "idx_detection_event_delivery_status_created_at_utc",
            "sqlite_autoindex_DetectionEvent_1",
        }


# --- 18. Version recorded as the current version (2) -------------------------------------------


def test_schema_version_recorded_as_current(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        rows = connection.execute("SELECT Version FROM SchemaVersion").fetchall()
        assert len(rows) == 1 and rows[0][0] == CURRENT_SCHEMA_VERSION


# --- 19-20. Idempotency; existing rows survive -------------------------------------------------


def test_initialization_is_idempotent(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION
    # A second run against the same database is a safe no-op returning the same version.
    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION

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
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, "
            "OperationalState) "
            "VALUES (1, 'device-xyz', ?, '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00', 'Operational')",
            (FAKE_SECRET_SENTINEL,),
        )

    # Re-initialize; the row must be preserved untouched (v2 → no-op).
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
        assert (
            connection.execute("SELECT Version FROM SchemaVersion").fetchone()[0]
            == CURRENT_SCHEMA_VERSION + 1
        )


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
    assert sorted(child.name for child in paths.root.iterdir()) == [
        "config",
        "database",
        "logs",
        "runtime",
        "snapshots",
    ]
    assert [child.name for child in paths.database_dir.iterdir()] == ["agent.db"]
    for deferred in ("recordings", "models", "pipeline"):
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

    assert (
        initialize_database(paths_db := _provisioned_paths(tmp_path).database_file)
        == CURRENT_SCHEMA_VERSION
    )
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
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, "
            "OperationalState) "
            "VALUES (1, 'device-xyz', ?, 't', 't', 'Operational')",
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


# --- IP-05 T-58: v2 constraints, nullable secret, and the v1 → v2 migration ---------------------

_MIG_ACTIVATED_AT = "2026-01-01T00:00:00+00:00"
_MIG_LAST_ACTIVATED_AT = "2026-02-02T00:00:00+00:00"

_V2_INSERT = (
    "INSERT INTO DeviceIdentity "
    "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, "
    "OperationalState) VALUES (?, ?, ?, ?, ?, ?)"
)


def _build_v1_database_with_row(database_file: Path, *, device_id: str, secret: str) -> None:
    """Create a genuine schema-version-1 database (NOT NULL secret, no OperationalState) with a row.

    Used to exercise the real v1 → v2 migration. Applies the shipped v1 DDL directly rather than
    the current initializer (which would migrate straight to v2).
    """
    with open_connection(database_file) as connection:
        schema_module._apply_version_1(connection)
        connection.execute(
            "INSERT INTO DeviceIdentity "
            "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt) "
            "VALUES (1, ?, ?, ?, ?)",
            (device_id, secret, _MIG_ACTIVATED_AT, _MIG_LAST_ACTIVATED_AT),
        )


def test_reactivation_required_row_with_null_secret_is_accepted(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(_V2_INSERT, (1, "dev", None, "t", "t", "ReactivationRequired"))
        row = connection.execute(
            "SELECT ProtectedSharedSecret, OperationalState FROM DeviceIdentity"
        ).fetchone()
        assert row["ProtectedSharedSecret"] is None
        assert row["OperationalState"] == "ReactivationRequired"


@pytest.mark.parametrize(
    ("secret", "state"),
    [
        (None, "Operational"),  # Operational must have a secret
        ("sec", "ReactivationRequired"),  # ReactivationRequired must not have a secret
        ("sec", "Bogus"),  # OperationalState must be one of the two permitted values
    ],
)
def test_schema_check_constraints_reject_invalid_combinations(
    tmp_path: Path, secret: str | None, state: str
) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_V2_INSERT, (1, "dev", secret, "t", "t", state))


def test_migration_from_v1_preserves_data_and_assigns_operational(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v1_database_with_row(database_file, device_id="device-mig", secret=FAKE_SECRET_SENTINEL)

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 1

    # Opening the v1 database with the current initializer cascades it through v2, v3, to v4.
    assert initialize_database(database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        assert "DetectionEvent" in _table_names(connection)
        columns = _columns(connection, "DeviceIdentity")
        assert "OperationalState" in columns
        assert columns["ProtectedSharedSecret"]["notnull"] == 0  # now nullable

        row = connection.execute(
            "SELECT DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, "
            "OperationalState FROM DeviceIdentity"
        ).fetchone()
        assert row["DeviceId"] == "device-mig"
        assert row["ProtectedSharedSecret"] == FAKE_SECRET_SENTINEL  # secret preserved
        assert row["ActivatedAt"] == _MIG_ACTIVATED_AT
        assert row["LastActivatedAt"] == _MIG_LAST_ACTIVATED_AT
        assert row["OperationalState"] == "Operational"  # migrated row is Operational


def test_failed_v1_to_v2_migration_rolls_back_and_preserves_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v1_database_with_row(database_file, device_id="device-rb", secret=FAKE_SECRET_SENTINEL)

    # Append a malformed final step so the rebuild fails after creating/copying/dropping/renaming;
    # SQLite's transactional DDL must roll the entire rebuild back.
    monkeypatch.setattr(
        schema_module,
        "_MIGRATION_V1_TO_V2_STATEMENTS",
        (*schema_module._MIGRATION_V1_TO_V2_STATEMENTS, "THIS IS NOT VALID SQL ("),
    )

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        initialize_database(database_file)

    with open_connection(database_file) as connection:
        # SchemaVersion unchanged, no partial v2 table, original v1 table + row + secret intact.
        assert read_schema_version(connection) == 1
        assert "DeviceIdentity_v2" not in _table_names(connection)
        columns = _columns(connection, "DeviceIdentity")
        assert "OperationalState" not in columns
        assert columns["ProtectedSharedSecret"]["notnull"] == 1  # still the v1 NOT NULL shape

        row = connection.execute(
            "SELECT DeviceId, ProtectedSharedSecret FROM DeviceIdentity"
        ).fetchone()
        assert row["DeviceId"] == "device-rb"
        assert row["ProtectedSharedSecret"] == FAKE_SECRET_SENTINEL

    # The migration error carries no row content or secret.
    assert FAKE_SECRET_SENTINEL not in str(excinfo.value)
    assert "device-rb" not in str(excinfo.value)


def test_reopening_current_version_is_a_no_op_that_does_not_modify_data(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    initialize_database(database_file)  # fresh → current version (4)

    with open_connection(database_file) as connection:
        connection.execute(
            _V2_INSERT,
            (
                1,
                "device-idem",
                FAKE_SECRET_SENTINEL,
                _MIG_ACTIVATED_AT,
                _MIG_LAST_ACTIVATED_AT,
                "Operational",
            ),
        )

    # Re-initializing an already-current database changes nothing.
    assert initialize_database(database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        row = connection.execute(
            "SELECT DeviceId, ProtectedSharedSecret, OperationalState FROM DeviceIdentity"
        ).fetchone()
        assert row["DeviceId"] == "device-idem"
        assert row["ProtectedSharedSecret"] == FAKE_SECRET_SENTINEL
        assert row["OperationalState"] == "Operational"


# --- IP-07 T-85: v2 -> v3 migration and the DetectionEvent table -------------------------------


def _build_v2_database_with_rows(
    database_file: Path, *, device_id: str, secret: str, config_json: str
) -> None:
    """Create a genuine schema-version-2 database with a DeviceIdentity and a ConfigCache row.

    Applies the shipped v1 DDL, migrates 1 -> 2, then seeds both singleton rows directly — used to
    exercise the real v2 -> v3 migration in isolation, mirroring
    :func:`_build_v1_database_with_row`'s role for the v1 -> v2 migration.
    """
    with open_connection(database_file) as connection:
        schema_module._apply_version_1(connection)
        schema_module._migrate_v1_to_v2(connection)
        connection.execute(
            _V2_INSERT,
            (1, device_id, secret, _MIG_ACTIVATED_AT, _MIG_LAST_ACTIVATED_AT, "Operational"),
        )
        connection.execute(
            "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
            (config_json, _MIG_ACTIVATED_AT),
        )


_DETECTION_EVENT_COLUMNS = [
    "EventId",
    "DeviceId",
    "CameraId",
    "SourceId",
    "ClassId",
    "ClassName",
    "Confidence",
    "FrameNumber",
    "DetectedAtUtc",
    "FrameWidth",
    "FrameHeight",
    "BboxLeft",
    "BboxTop",
    "BboxWidth",
    "BboxHeight",
    "DeliveryStatus",
    "DeliveredAtUtc",
    "FinalizedAtUtc",
    "CreatedAtUtc",
]


def test_fresh_database_creates_detection_event_table(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(paths.database_file) as connection:
        assert "DetectionEvent" in _table_names(connection)


def test_detection_event_columns_and_types(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        columns = _columns(connection, "DetectionEvent")

    assert list(columns) == _DETECTION_EVENT_COLUMNS
    assert columns["EventId"]["type"] == "TEXT"
    assert columns["EventId"]["pk"] == 1
    for int_col in ("SourceId", "ClassId", "FrameNumber", "FrameWidth", "FrameHeight"):
        assert columns[int_col]["type"] == "INTEGER"
        assert columns[int_col]["notnull"] == 1
    for real_col in ("Confidence", "BboxLeft", "BboxTop", "BboxWidth", "BboxHeight"):
        assert columns[real_col]["type"] == "REAL"
        assert columns[real_col]["notnull"] == 1
    for text_col in (
        "DeviceId",
        "CameraId",
        "ClassName",
        "DetectedAtUtc",
        "DeliveryStatus",
        "CreatedAtUtc",
    ):
        assert columns[text_col]["type"] == "TEXT"
        assert columns[text_col]["notnull"] == 1
    # DeliveredAtUtc (FS-06 §7.2) is nullable — NULL until delivery confirmed.
    assert columns["DeliveredAtUtc"]["type"] == "TEXT"
    assert columns["DeliveredAtUtc"]["notnull"] == 0
    # FinalizedAtUtc (FS-09 §9) is nullable — NULL until quota-suppressed.
    assert columns["FinalizedAtUtc"]["type"] == "TEXT"
    assert columns["FinalizedAtUtc"]["notnull"] == 0


def test_detection_event_delivery_status_defaults_to_pending(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, CreatedAtUtc) "
            "VALUES ('evt-1', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
            "0, 0, 10, 10, 't')"
        )
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-1'"
        ).fetchone()

    assert row["DeliveryStatus"] == "pending"


def test_detection_event_accepts_delivered_status(tmp_path: Path) -> None:
    # 'delivered' is the terminal state a future backend-delivery feature will write; T-85 itself
    # never writes it (the repository always writes 'pending'), but the schema must permit it so
    # that feature needs no further table-rebuild migration (approved T-85 amendment).
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, DeliveryStatus, CreatedAtUtc) "
            "VALUES ('evt-2', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
            "0, 0, 10, 10, 'delivered', 't')"
        )
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-2'"
        ).fetchone()

    assert row["DeliveryStatus"] == "delivered"


@pytest.mark.parametrize("bogus_status", ("unknown", "sending", "failed", "", "Pending"))
def test_detection_event_rejects_values_outside_the_permitted_lifecycle(
    tmp_path: Path, bogus_status: str
) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO DetectionEvent "
                "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
                "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
                "BboxWidth, BboxHeight, DeliveryStatus, CreatedAtUtc) "
                "VALUES ('evt-bogus', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
                "0, 0, 10, 10, ?, 't')",
                (bogus_status,),
            )


def test_detection_event_rejects_duplicate_event_id(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)
    insert = (
        "INSERT INTO DetectionEvent "
        "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
        "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
        "BboxWidth, BboxHeight, CreatedAtUtc) "
        "VALUES ('evt-dup', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, 0, 0, 10, 10, 't')"
    )

    with open_connection(paths.database_file) as connection:
        connection.execute(insert)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert)


def test_migration_from_v2_preserves_device_identity_and_config_cache(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v2_database_with_rows(
        database_file,
        device_id="device-v2mig",
        secret=FAKE_SECRET_SENTINEL,
        config_json='{"marker": "ZZZ-config-must-survive-ZZZ"}',
    )

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 2

    assert initialize_database(database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        assert "DetectionEvent" in _table_names(connection)

        identity_row = connection.execute(
            "SELECT DeviceId, ProtectedSharedSecret FROM DeviceIdentity"
        ).fetchone()
        assert identity_row["DeviceId"] == "device-v2mig"
        assert identity_row["ProtectedSharedSecret"] == FAKE_SECRET_SENTINEL

        config_row = connection.execute("SELECT ConfigJson FROM ConfigCache").fetchone()
        assert config_row["ConfigJson"] == '{"marker": "ZZZ-config-must-survive-ZZZ"}'

        (event_count,) = connection.execute("SELECT COUNT(*) FROM DetectionEvent").fetchone()
        assert event_count == 0


def test_failed_v2_to_v3_migration_rolls_back_and_preserves_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v2_database_with_rows(
        database_file,
        device_id="device-v3rb",
        secret=FAKE_SECRET_SENTINEL,
        config_json="{}",
    )

    # Append a malformed final step so the migration fails after the table/index are created;
    # SQLite's transactional DDL must roll the entire migration back.
    monkeypatch.setattr(
        schema_module,
        "_MIGRATION_V2_TO_V3_STATEMENTS",
        (*schema_module._MIGRATION_V2_TO_V3_STATEMENTS, "THIS IS NOT VALID SQL ("),
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(database_file)

    with open_connection(database_file) as connection:
        # SchemaVersion unchanged, no partial DetectionEvent table, original data intact.
        assert read_schema_version(connection) == 2
        assert "DetectionEvent" not in _table_names(connection)
        row = connection.execute("SELECT DeviceId FROM DeviceIdentity").fetchone()
        assert row["DeviceId"] == "device-v3rb"


def test_reinitializing_v3_database_does_not_touch_detection_events(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)
    initialize_database(paths.database_file)

    with open_connection(paths.database_file) as connection:
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, CreatedAtUtc) "
            "VALUES ('evt-keep', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
            "0, 0, 10, 10, 't')"
        )

    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(paths.database_file) as connection:
        row = connection.execute(
            "SELECT EventId FROM DetectionEvent WHERE EventId = 'evt-keep'"
        ).fetchone()
    assert row is not None


# --- IP-08 T-103: v3 -> v4 migration (DeliveredAtUtc, widened DeliveryStatus CHECK) -------------

_PENDING_ROW_VALUES = (
    "'evt-pending', 'device-v4mig', 'cam', 0, 0, 'gun', 0.91, 12345, "
    "'2026-07-24T18:30:00+00:00', 640, 640, 210.0, 130.0, 95.0, 70.0, "
    "'pending', '2026-07-24T18:30:01+00:00'"
)


def _build_v3_database_with_pending_row(database_file: Path) -> None:
    """Create a genuine schema-version-3 database with one 'pending' DetectionEvent row.

    Applies the shipped v1 DDL, migrates 1 -> 2 -> 3, then inserts directly (the version-3 table has
    no DeliveredAtUtc column at all) — used to exercise the real v3 -> v4 migration in isolation,
    mirroring `_build_v1_database_with_row`/`_build_v2_database_with_rows`.
    """
    with open_connection(database_file) as connection:
        schema_module._apply_version_1(connection)
        schema_module._migrate_v1_to_v2(connection)
        schema_module._migrate_v2_to_v3(connection)
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, DeliveryStatus, CreatedAtUtc) "
            f"VALUES ({_PENDING_ROW_VALUES})"
        )


def test_fresh_database_ends_at_v4_with_new_column_and_widened_constraint(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(paths.database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        columns = _columns(connection, "DetectionEvent")
        assert "DeliveredAtUtc" in columns
        assert columns["DeliveredAtUtc"]["notnull"] == 0


def test_migration_from_v3_preserves_pending_row_unchanged(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v3_database_with_pending_row(database_file)

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 3

    assert initialize_database(database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        row = connection.execute(
            "SELECT EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, "
            "BboxHeight, DeliveryStatus, DeliveredAtUtc, CreatedAtUtc "
            "FROM DetectionEvent WHERE EventId = 'evt-pending'"
        ).fetchone()

    assert row is not None
    assert row["DeviceId"] == "device-v4mig"
    assert row["CameraId"] == "cam"
    assert row["SourceId"] == 0
    assert row["ClassId"] == 0
    assert row["ClassName"] == "gun"
    assert row["Confidence"] == 0.91
    assert row["FrameNumber"] == 12345
    assert row["DetectedAtUtc"] == "2026-07-24T18:30:00+00:00"
    assert row["FrameWidth"] == 640
    assert row["FrameHeight"] == 640
    assert row["BboxLeft"] == 210.0
    assert row["BboxTop"] == 130.0
    assert row["BboxWidth"] == 95.0
    assert row["BboxHeight"] == 70.0
    assert row["CreatedAtUtc"] == "2026-07-24T18:30:01+00:00"
    # The migration's own contract: the pending row's delivery state is untouched.
    assert row["DeliveryStatus"] == "pending"
    assert row["DeliveredAtUtc"] is None


def test_migrated_schema_accepts_delivered_status(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v3_database_with_pending_row(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        connection.execute(
            "UPDATE DetectionEvent SET DeliveryStatus = 'delivered', DeliveredAtUtc = ? "
            "WHERE EventId = 'evt-pending'",
            ("2026-07-24T18:30:05+00:00",),
        )
        row = connection.execute(
            "SELECT DeliveryStatus, DeliveredAtUtc FROM DetectionEvent "
            "WHERE EventId = 'evt-pending'"
        ).fetchone()

    assert row["DeliveryStatus"] == "delivered"
    assert row["DeliveredAtUtc"] == "2026-07-24T18:30:05+00:00"


def test_migrated_schema_still_rejects_values_outside_the_permitted_lifecycle(
    tmp_path: Path,
) -> None:
    # Proves the CHECK constraint was widened, not dropped (IP-08 T-103 requirement).
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v3_database_with_pending_row(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE DetectionEvent SET DeliveryStatus = 'invalid_value' "
                "WHERE EventId = 'evt-pending'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO DetectionEvent "
                "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
                "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
                "BboxWidth, BboxHeight, DeliveryStatus, CreatedAtUtc) "
                "VALUES ('evt-bogus-v4', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
                "0, 0, 10, 10, 'invalid_value', 't')"
            )


def test_failed_v3_to_v4_migration_rolls_back_and_preserves_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v3_database_with_pending_row(database_file)

    monkeypatch.setattr(
        schema_module,
        "_MIGRATION_V3_TO_V4_STATEMENTS",
        (*schema_module._MIGRATION_V3_TO_V4_STATEMENTS, "THIS IS NOT VALID SQL ("),
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(database_file)

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 3
        assert "DetectionEvent_v4" not in _table_names(connection)
        columns = _columns(connection, "DetectionEvent")
        assert "DeliveredAtUtc" not in columns
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-pending'"
        ).fetchone()
        assert row["DeliveryStatus"] == "pending"


# --- IP-11 T-176: v5 -> v6 migration (widened DeliveryStatus/UploadStatus CHECK constraints) -----

_V5_PENDING_DETECTION_EVENT_ROW_VALUES = (
    "'evt-pending', 'device-v4mig', 'cam', 0, 0, 'gun', 0.91, 12345, "
    "'2026-07-24T18:30:00+00:00', 640, 640, 210.0, 130.0, 95.0, 70.0, "
    "'pending', NULL, '2026-07-24T18:30:01+00:00'"
)

_V5_SNAPSHOT_ROW_VALUES = (
    "'evt-pending', '/tmp/evt-pending.jpg', 'captured', 'pending', "
    "'image/jpeg', 12345, "
    "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd', "
    "'2026-07-24T18:30:02+00:00'"
)


def _build_v5_database_with_rows(database_file: Path) -> None:
    """Create a genuine schema-version-5 database with one 'pending' DetectionEvent row and one
    'pending'-upload SnapshotOutbox row referencing it.

    Applies the shipped v1 DDL, migrates 1 -> 2 -> 3 -> 4 -> 5, then inserts directly — used to
    exercise the real v5 -> v6 migration in isolation, mirroring
    `_build_v3_database_with_pending_row`.
    """
    with open_connection(database_file) as connection:
        schema_module._apply_version_1(connection)
        schema_module._migrate_v1_to_v2(connection)
        schema_module._migrate_v2_to_v3(connection)
        schema_module._migrate_v3_to_v4(connection)
        schema_module._migrate_v4_to_v5(connection)
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, DeliveryStatus, DeliveredAtUtc, CreatedAtUtc) "
            f"VALUES ({_V5_PENDING_DETECTION_EVENT_ROW_VALUES})"
        )
        connection.execute(
            "INSERT INTO SnapshotOutbox "
            "(EventId, LocalPath, CaptureStatus, UploadStatus, ContentType, SizeBytes, Sha256, "
            "CapturedAtUtc) "
            f"VALUES ({_V5_SNAPSHOT_ROW_VALUES})"
        )


def test_fresh_database_ends_at_v6_with_widened_constraints(tmp_path: Path) -> None:
    paths = _provisioned_paths(tmp_path)

    assert initialize_database(paths.database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(paths.database_file) as connection:
        assert read_schema_version(connection) == CURRENT_SCHEMA_VERSION
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, DeliveryStatus, CreatedAtUtc) "
            "VALUES ('evt-fresh-v6', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 't', 640, 480, "
            "0, 0, 10, 10, 'suppressed_by_quota', 't')"
        )
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-fresh-v6'"
        ).fetchone()
        assert row["DeliveryStatus"] == "suppressed_by_quota"


def test_migration_from_v5_preserves_existing_rows_unchanged(tmp_path: Path) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 5

    assert initialize_database(database_file) == CURRENT_SCHEMA_VERSION

    with open_connection(database_file) as connection:
        event_row = connection.execute(
            "SELECT DeliveryStatus, DeliveredAtUtc FROM DetectionEvent "
            "WHERE EventId = 'evt-pending'"
        ).fetchone()
        assert event_row["DeliveryStatus"] == "pending"
        assert event_row["DeliveredAtUtc"] is None

        outbox_row = connection.execute(
            "SELECT CaptureStatus, UploadStatus, ContentType, SizeBytes, Sha256 "
            "FROM SnapshotOutbox WHERE EventId = 'evt-pending'"
        ).fetchone()
        assert outbox_row["CaptureStatus"] == "captured"
        assert outbox_row["UploadStatus"] == "pending"
        assert outbox_row["ContentType"] == "image/jpeg"
        assert outbox_row["SizeBytes"] == 12345


def test_migrated_schema_accepts_suppressed_by_quota_detection_event_status(
    tmp_path: Path,
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        connection.execute(
            "UPDATE DetectionEvent SET DeliveryStatus = 'suppressed_by_quota' "
            "WHERE EventId = 'evt-pending'"
        )
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-pending'"
        ).fetchone()

    assert row["DeliveryStatus"] == "suppressed_by_quota"


def test_migrated_schema_accepts_suppressed_by_quota_snapshot_outbox_status(
    tmp_path: Path,
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        connection.execute(
            "UPDATE SnapshotOutbox SET UploadStatus = 'suppressed_by_quota' "
            "WHERE EventId = 'evt-pending'"
        )
        row = connection.execute(
            "SELECT UploadStatus FROM SnapshotOutbox WHERE EventId = 'evt-pending'"
        ).fetchone()

    assert row["UploadStatus"] == "suppressed_by_quota"


def test_migrated_schema_still_rejects_detection_event_values_outside_the_permitted_lifecycle_v6(
    tmp_path: Path,
) -> None:
    # Proves the CHECK constraint was widened, not dropped (IP-11 T-176 requirement).
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE DetectionEvent SET DeliveryStatus = 'invalid_value' "
                "WHERE EventId = 'evt-pending'"
            )


def test_migrated_schema_still_rejects_snapshot_outbox_values_outside_the_permitted_lifecycle(
    tmp_path: Path,
) -> None:
    # Proves the CHECK constraint was widened, not dropped (IP-11 T-176 requirement).
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)
    initialize_database(database_file)

    with open_connection(database_file) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE SnapshotOutbox SET UploadStatus = 'invalid_value' "
                "WHERE EventId = 'evt-pending'"
            )


def test_failed_v5_to_v6_migration_rolls_back_and_preserves_v5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_file = _provisioned_paths(tmp_path).database_file
    _build_v5_database_with_rows(database_file)

    monkeypatch.setattr(
        schema_module,
        "_MIGRATION_V5_TO_V6_STATEMENTS",
        (*schema_module._MIGRATION_V5_TO_V6_STATEMENTS, "THIS IS NOT VALID SQL ("),
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(database_file)

    with open_connection(database_file) as connection:
        assert read_schema_version(connection) == 5
        assert "DetectionEvent_v6" not in _table_names(connection)
        assert "SnapshotOutbox_v6" not in _table_names(connection)
        event_row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = 'evt-pending'"
        ).fetchone()
        assert event_row["DeliveryStatus"] == "pending"
        outbox_row = connection.execute(
            "SELECT UploadStatus FROM SnapshotOutbox WHERE EventId = 'evt-pending'"
        ).fetchone()
        assert outbox_row["UploadStatus"] == "pending"
