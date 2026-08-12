"""SQLite schema definition and versioned initialization (IP-02 T-35, §7; IP-05 T-58; IP-07 T-85).

This module defines the schema and the single, idempotent initializer that brings a database up to
:data:`CURRENT_SCHEMA_VERSION`. The approach is deliberately small (IP-02 D-6, Engineering Principle
9): one integer version guarded by a ``SchemaVersion`` table, DDL applied in one transaction, and
no third-party migration framework — a handful of tables and a few migrations do not justify a
dependency.

**Versions.** Version 1 (IP-02 §7) created ``SchemaVersion`` / ``DeviceIdentity`` / ``ConfigCache``,
with ``DeviceIdentity.ProtectedSharedSecret`` ``NOT NULL``. Version 2 (IP-05 §7) makes that column
**nullable** and adds an ``OperationalState`` column with CHECK constraints enforcing the local
credential-state invariant (``Operational`` ⇒ a secret is present; ``ReactivationRequired`` ⇒ the
secret is ``NULL``). Because SQLite cannot relax a ``NOT NULL`` in place, the upgrade is a table
rebuild. Version 3 (FS-05 §7, IP-07 T-85) adds a new, purely additive ``DetectionEvent`` table (no
existing table is touched) plus one index supporting the future backend-delivery step's
pending-event query (``DeliveryStatus``, ``CreatedAtUtc``). Version 4 (FS-06 §7.2, IP-08 T-103) adds
``DetectionEvent.DeliveredAtUtc`` and widens the ``DeliveryStatus`` CHECK to permit ``'delivered'``
— the outbox-delivery feature's own required column/constraint change, via the same table-rebuild
technique version 2 already established.

Version 5 (FS-08 §6, IP-10 T-141) adds a new, purely additive ``SnapshotOutbox`` table (no existing
table is touched) plus its index — the exact DDL FS-08 §6 specifies, copied verbatim.

Version 6 (FS-09 §9/§10, IP-11 T-176) widens two CHECK constraints in the same migration: it
rebuilds ``DetectionEvent`` to permit ``DeliveryStatus = 'suppressed_by_quota'`` (a third terminal
state alongside ``'pending'``/``'delivered'``, FS-09 §9), and rebuilds ``SnapshotOutbox`` to permit
``UploadStatus = 'suppressed_by_quota'`` (FS-09 §10) — both using the same table-rebuild technique
version 2/4 already established, since SQLite cannot widen a CHECK constraint in place.

Forward-only and idempotent, appending steps rather than editing shipped ones:

* A **fresh** database applies the version-1 DDL and then migrates 1 → 2 → 3 → 4 → 5 → 6, so it ends
  at the latest version through the same migration path an existing database takes (a fresh
  database's rebuild copies zero rows). The shipped version-1 DDL is never edited.
* An existing **version-1** database is migrated 1 → 2 → 3 → 4 → 5 → 6.
* An existing **version-2** database is migrated 2 → 3 → 4 → 5 → 6.
* An existing **version-3** database is migrated 3 → 4 → 5 → 6.
* An existing **version-4** database is migrated 4 → 5 → 6.
* An existing **version-5** database is migrated 5 → 6.
* An existing **version-6** database is a safe no-op — no rebuild, no data change.
* A **newer** version raises :class:`UnsupportedSchemaVersionError` without modifying anything.

What this module does **not** do: read or write a ``DeviceIdentity``/``ConfigCache``/
``DetectionEvent`` *record* (the repositories are T-36/T-85), seed any row, create directories,
contact the Backend, or wire into startup. No value, row, or secret is ever written to a log or an
error — at most a version number or a table name, neither of which is sensitive.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from weapon_detection_agent.persistence.database import (
    DatabaseInitializationError,
    open_connection,
    transaction,
)

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.schema")

# The schema version this build understands. A database recording exactly this value is current; a
# higher value is unsupported (this build must not touch it); anything else is an invalid state.
CURRENT_SCHEMA_VERSION = 6

# Version-1 DDL, verbatim from IP-02 §7 — retained unedited as the shipped version-1 schema. Each
# statement is idempotent (IF NOT EXISTS); the ordered tuple is applied as one transaction. Exposed
# at module scope so the schema is defined in exactly one place (and so a test can substitute a
# deliberately failing step to prove rollback, IP-02 §16).
SCHEMA_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS SchemaVersion (
        Version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS DeviceIdentity (
        SingletonGuard        INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
        DeviceId              TEXT    NOT NULL,
        ProtectedSharedSecret TEXT    NOT NULL,
        ActivatedAt           TEXT    NOT NULL,  -- ISO-8601 UTC
        LastActivatedAt       TEXT    NOT NULL   -- ISO-8601 UTC; updated on each reactivation
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ConfigCache (
        SingletonGuard INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
        ConfigJson     TEXT    NOT NULL,
        UpdatedAt      TEXT    NOT NULL  -- ISO-8601 UTC
    )
    """,
)


def _device_identity_v2_ddl(table_name: str) -> str:
    """Return the ``CREATE TABLE`` DDL for the version-2 ``DeviceIdentity`` shape (IP-05 §7).

    Defined once and used both for the migration's replacement table and (indirectly) for a fresh
    database. ``table_name`` is a fixed literal (``DeviceIdentity`` or ``DeviceIdentity_v2``),
    never caller input. ``ProtectedSharedSecret`` is nullable; ``OperationalState`` is limited to
    the two permitted values; a table-level CHECK ties them together so the database rejects an
    ``Operational`` row without a secret or a ``ReactivationRequired`` row with one.
    """
    return f"""
    CREATE TABLE {table_name} (
        SingletonGuard        INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
        DeviceId              TEXT    NOT NULL,
        ProtectedSharedSecret TEXT    NULL,
        ActivatedAt           TEXT    NOT NULL,  -- ISO-8601 UTC
        LastActivatedAt       TEXT    NOT NULL,  -- ISO-8601 UTC; updated on each reactivation
        OperationalState      TEXT    NOT NULL
            CHECK (OperationalState IN ('Operational', 'ReactivationRequired')),
        CHECK (
            (OperationalState = 'Operational' AND ProtectedSharedSecret IS NOT NULL)
            OR
            (OperationalState = 'ReactivationRequired' AND ProtectedSharedSecret IS NULL)
        )
    )
    """


# The version-1 → version-2 migration, as one ordered sequence applied inside a single transaction
# (IP-05 §7). It rebuilds DeviceIdentity to make the secret nullable and add OperationalState,
# copying the existing row as 'Operational'. Exposed at module scope so a test can substitute a
# deliberately failing step to prove the whole rebuild rolls back (SQLite DDL is transactional).
_MIGRATION_V1_TO_V2_STATEMENTS: tuple[str, ...] = (
    _device_identity_v2_ddl("DeviceIdentity_v2"),
    """
    INSERT INTO DeviceIdentity_v2
        (SingletonGuard, DeviceId, ProtectedSharedSecret,
         ActivatedAt, LastActivatedAt, OperationalState)
    SELECT SingletonGuard, DeviceId, ProtectedSharedSecret,
           ActivatedAt, LastActivatedAt, 'Operational'
    FROM DeviceIdentity
    """,
    "DROP TABLE DeviceIdentity",
    "ALTER TABLE DeviceIdentity_v2 RENAME TO DeviceIdentity",
    "UPDATE SchemaVersion SET Version = 2",
)


# Version-3 DDL (FS-05 §7, IP-07 T-85): a new, purely additive `DetectionEvent` table — no existing
# table is rebuilt or altered. `EventId` is the primary key (Agent-generated UUID text), so no
# separate uniqueness index is needed. Every other required field is `NOT NULL`. `DeliveryStatus`
# permits the minimum two-state lifecycle the architecture already implies (approved T-85
# amendment):
#
#   pending    Persisted locally and not yet acknowledged by the central backend. Every event T-85
#              inserts starts here (this task's repository writes no other value). A temporary
#              transport failure leaves an event `pending` for a future retry — there is
#              deliberately no `failed` state without the future delivery specification (item 4 of
#              the amendment).
#   delivered  Successfully accepted by the central backend. No code in this task ever writes this
#              value — reaching it is exclusively the future outbox/backend-delivery feature's job
#              (no status-update method, retry worker, attempt counter, or failed-at timestamp is
#              added here).
#
# A wider set (`sending`, `failed`, ...) is deliberately deferred to that feature's own migration,
# following the same additive-migration discipline the v1 -> v2 rebuild already used — this shipped
# statement is never edited again to add a state, only widened by a future v3 -> v4 migration if
# needed. The index on (`DeliveryStatus`, `CreatedAtUtc`) is the one concrete future query this
# schema anticipates: the backend-delivery step will need to scan pending events in creation order.
# No other index is added (Engineering Principle 9 — no speculative indexes).
_DETECTION_EVENT_V3_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS DetectionEvent (
        EventId        TEXT    PRIMARY KEY,
        DeviceId       TEXT    NOT NULL,
        CameraId       TEXT    NOT NULL,
        SourceId       INTEGER NOT NULL,
        ClassId        INTEGER NOT NULL,
        ClassName      TEXT    NOT NULL,
        Confidence     REAL    NOT NULL,
        FrameNumber    INTEGER NOT NULL,
        DetectedAtUtc  TEXT    NOT NULL,  -- ISO-8601 UTC
        FrameWidth     INTEGER NOT NULL,
        FrameHeight    INTEGER NOT NULL,
        BboxLeft       REAL    NOT NULL,
        BboxTop        REAL    NOT NULL,
        BboxWidth      REAL    NOT NULL,
        BboxHeight     REAL    NOT NULL,
        DeliveryStatus TEXT    NOT NULL DEFAULT 'pending'
            CHECK (DeliveryStatus IN ('pending', 'delivered')),
        CreatedAtUtc   TEXT    NOT NULL  -- ISO-8601 UTC
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_detection_event_delivery_status_created_at_utc
        ON DetectionEvent (DeliveryStatus, CreatedAtUtc)
    """,
)


# The version-2 -> version-3 migration (FS-05 §7, IP-07 T-85), applied inside a single transaction.
# Purely additive: it creates the new table/index and nothing else, so
# `DeviceIdentity`/`ConfigCache` rows are never touched. Exposed at module scope so a test can
# substitute a deliberately failing step to prove the whole migration rolls back.
_MIGRATION_V2_TO_V3_STATEMENTS: tuple[str, ...] = (
    *_DETECTION_EVENT_V3_STATEMENTS,
    "UPDATE SchemaVersion SET Version = 3",
)


def _detection_event_v4_ddl(table_name: str) -> str:
    """Return the ``CREATE TABLE`` DDL for the version-4 ``DetectionEvent`` shape (FS-06 §7.2).

    Defined once and used for the migration's replacement table, mirroring
    :func:`_device_identity_v2_ddl`'s technique for the same "SQLite cannot alter a CHECK constraint
    in place" problem: ``DeliveredAtUtc`` is added and the ``DeliveryStatus`` CHECK widens from
    ``('pending')`` to ``('pending', 'delivered')`` — the only two differences from the version-3
    shape. ``table_name`` is a fixed literal (``DetectionEvent`` or ``DetectionEvent_v4``), never
    caller input.
    """
    return f"""
    CREATE TABLE {table_name} (
        EventId        TEXT    PRIMARY KEY,
        DeviceId       TEXT    NOT NULL,
        CameraId       TEXT    NOT NULL,
        SourceId       INTEGER NOT NULL,
        ClassId        INTEGER NOT NULL,
        ClassName      TEXT    NOT NULL,
        Confidence     REAL    NOT NULL,
        FrameNumber    INTEGER NOT NULL,
        DetectedAtUtc  TEXT    NOT NULL,  -- ISO-8601 UTC
        FrameWidth     INTEGER NOT NULL,
        FrameHeight    INTEGER NOT NULL,
        BboxLeft       REAL    NOT NULL,
        BboxTop        REAL    NOT NULL,
        BboxWidth      REAL    NOT NULL,
        BboxHeight     REAL    NOT NULL,
        DeliveryStatus TEXT    NOT NULL DEFAULT 'pending'
            CHECK (DeliveryStatus IN ('pending', 'delivered')),
        DeliveredAtUtc TEXT    NULL,  -- ISO-8601 UTC; set only when DeliveryStatus = 'delivered'
        CreatedAtUtc   TEXT    NOT NULL  -- ISO-8601 UTC
    )
    """


# The version-3 -> version-4 migration (FS-06 §7.2, IP-08 T-103), applied inside a single
# transaction. SQLite cannot widen a CHECK constraint in place, so this rebuilds `DetectionEvent`
# using the same create/copy/drop/rename technique `_MIGRATION_V1_TO_V2_STATEMENTS` already
# established for the analogous `DeviceIdentity` problem: every existing row's `DeliveryStatus`
# (necessarily 'pending', the only value version 3 permits) and every other column are copied
# unchanged; the new `DeliveredAtUtc` column is NULL for every migrated row. The index is dropped
# along with the old table (SQLite indexes do not survive `DROP TABLE`) and is recreated on the
# renamed table. `DeviceIdentity`/`ConfigCache` are never touched. Exposed at module scope so a test
# can substitute a deliberately failing step to prove the whole rebuild rolls back.
_MIGRATION_V3_TO_V4_STATEMENTS: tuple[str, ...] = (
    _detection_event_v4_ddl("DetectionEvent_v4"),
    """
    INSERT INTO DetectionEvent_v4
        (EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber,
         DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight,
         DeliveryStatus, DeliveredAtUtc, CreatedAtUtc)
    SELECT EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber,
           DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight,
           DeliveryStatus, NULL, CreatedAtUtc
    FROM DetectionEvent
    """,
    "DROP TABLE DetectionEvent",
    "ALTER TABLE DetectionEvent_v4 RENAME TO DetectionEvent",
    """
    CREATE INDEX IF NOT EXISTS idx_detection_event_delivery_status_created_at_utc
        ON DetectionEvent (DeliveryStatus, CreatedAtUtc)
    """,
    "UPDATE SchemaVersion SET Version = 4",
)


class UnsupportedSchemaVersionError(DatabaseInitializationError):
    """The database records a schema version newer than this build supports.

    The Agent refuses to downgrade or otherwise touch a database written by a newer build (IP-02
    §9), so a rollback of an accidental upgrade cannot silently corrupt data.
    """


class InvalidSchemaStateError(DatabaseInitializationError):
    """The ``SchemaVersion`` table is in an impossible state — no row, more than one row, or a
    non-positive/non-integer version — so the recorded version cannot be trusted. The Agent refuses
    to guess or auto-repair (IP-02 §9)."""


def initialize_database(database_path: str | Path) -> int:
    """Open ``database_path``, initialize/verify its schema, and close the connection.

    A convenience over :func:`initialize_schema` for callers that just want the database ready; the
    startup workflow uses it. Returns the schema version. Creating no directory and seeding
    no row, it only ensures the schema metadata exists.
    """
    with open_connection(database_path) as connection:
        return initialize_schema(connection)


def initialize_schema(connection: sqlite3.Connection) -> int:
    """Bring ``connection``'s database to :data:`CURRENT_SCHEMA_VERSION`, idempotently.

    * **Fresh database** (no ``SchemaVersion`` table): create the version-1 tables, then migrate
      1 → 2 → 3 → 4 → 5 → 6, atomically at each step. Returns 6.
    * **Version 1**: migrate 1 → 2 → 3 → 4 → 5 → 6. Returns 6.
    * **Version 2**: migrate 2 → 3 → 4 → 5 → 6. Returns 6.
    * **Version 3**: migrate 3 → 4 → 5 → 6. Returns 6.
    * **Version 4**: migrate 4 → 5 → 6. Returns 6.
    * **Version 5**: migrate 5 → 6. Returns 6.
    * **Already current** (version 6): do nothing destructive; preserve all data. Returns 6.
    * **Newer version**: raise :class:`UnsupportedSchemaVersionError` without modifying anything.
    * **Invalid version state**: raise :class:`InvalidSchemaStateError` without modifying anything.

    Returns the schema version now in effect.
    """
    version = read_schema_version(connection)
    migrated = False

    if version is None:
        _LOGGER.info(
            "database_schema_initialization_started",
            extra={"target_version": CURRENT_SCHEMA_VERSION},
        )
        _apply_version_1(connection)
        version = 1

    if version == 1:
        _LOGGER.info(
            "database_schema_migration_started", extra={"from_version": 1, "to_version": 2}
        )
        _migrate_v1_to_v2(connection)
        version = 2
        migrated = True

    if version == 2:
        _LOGGER.info(
            "database_schema_migration_started", extra={"from_version": 2, "to_version": 3}
        )
        _migrate_v2_to_v3(connection)
        version = 3
        migrated = True

    if version == 3:
        _LOGGER.info(
            "database_schema_migration_started", extra={"from_version": 3, "to_version": 4}
        )
        _migrate_v3_to_v4(connection)
        version = 4
        migrated = True

    if version == 4:
        _LOGGER.info(
            "database_schema_migration_started", extra={"from_version": 4, "to_version": 5}
        )
        _migrate_v4_to_v5(connection)
        version = 5
        migrated = True

    if version == 5:
        _LOGGER.info(
            "database_schema_migration_started", extra={"from_version": 5, "to_version": 6}
        )
        _migrate_v5_to_v6(connection)
        version = 6
        migrated = True

    if version == CURRENT_SCHEMA_VERSION:
        if migrated:
            _LOGGER.info(
                "database_schema_migrated", extra={"schema_version": CURRENT_SCHEMA_VERSION}
            )
        else:
            _LOGGER.info("database_schema_already_current", extra={"schema_version": version})
        return version

    if version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"database schema version {version} is newer than the supported version "
            f"{CURRENT_SCHEMA_VERSION}; refusing to downgrade or modify it"
        )

    # A version below 1 cannot occur (read_schema_version rejects it); this remains as a defensive
    # guard against any state with no known migration path.
    raise InvalidSchemaStateError(
        f"database schema version {version} has no known migration path to {CURRENT_SCHEMA_VERSION}"
    )


def read_schema_version(connection: sqlite3.Connection) -> int | None:
    """Return the recorded schema version, ``None`` for a fresh database, or raise on an invalid
    state.

    A fresh database is one with no ``SchemaVersion`` table. When the table exists it must hold
    exactly one row with a positive integer version; anything else raises
    :class:`InvalidSchemaStateError` (the message names counts only, never a stored value).
    """
    if not _table_exists(connection, "SchemaVersion"):
        return None

    rows = connection.execute("SELECT Version FROM SchemaVersion").fetchall()
    if len(rows) != 1:
        raise InvalidSchemaStateError(
            f"SchemaVersion must contain exactly one row; found {len(rows)}"
        )

    value = rows[0][0]
    # bool is a subclass of int; exclude it so a stray 0/1 boolean is not mistaken for a version.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidSchemaStateError("SchemaVersion holds an invalid version value")

    return value


def _apply_version_1(connection: sqlite3.Connection) -> None:
    """Create the version-1 tables and record version 1 in one atomic transaction (IP-02 §7).

    Any failure mid-way rolls the whole thing back, so the database is never left with only some
    tables or a version row without its tables.
    """
    with transaction(connection):
        for statement in SCHEMA_V1_STATEMENTS:
            connection.execute(statement)
        connection.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (1,))


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Upgrade a version-1 database to version 2 in one atomic transaction (IP-05 §7).

    Rebuilds DeviceIdentity to make ``ProtectedSharedSecret`` nullable and add ``OperationalState``
    (defaulting the migrated row to ``'Operational'``), preserving the ``DeviceId``, secret, and
    timestamps. Any failure at any step rolls the whole rebuild back — SQLite DDL is transactional —
    leaving original version-1 table, its row, its secret, and ``SchemaVersion = 1`` untouched, and
    no partial replacement table behind. No row content or secret is ever logged.
    """
    with transaction(connection):
        for statement in _MIGRATION_V1_TO_V2_STATEMENTS:
            connection.execute(statement)


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Upgrade a version-2 database to version 3 in one atomic transaction (FS-05 §7, IP-07 T-85).

    Adds the `DetectionEvent` table and its one justified index; `DeviceIdentity` and `ConfigCache`
    are never rebuilt, altered, or touched. A failure at any step rolls the whole migration back,
    leaving the database at version 2 with no partial `DetectionEvent` table or index behind.
    """
    with transaction(connection):
        for statement in _MIGRATION_V2_TO_V3_STATEMENTS:
            connection.execute(statement)


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Upgrade a version-3 database to version 4 in one atomic transaction (FS-06 §7.2, IP-08
    T-103).

    Rebuilds `DetectionEvent` to add `DeliveredAtUtc` and widen the `DeliveryStatus` CHECK to
    permit `'delivered'`, preserving every existing row's data (a version-3 row's `DeliveryStatus`
    is always `'pending'`, since no earlier code ever wrote `'delivered'`). `DeviceIdentity` and
    `ConfigCache` are never rebuilt, altered, or touched. A failure at any step rolls the whole
    rebuild back, leaving the database at version 3 with no partial replacement table or index
    behind.
    """
    with transaction(connection):
        for statement in _MIGRATION_V3_TO_V4_STATEMENTS:
            connection.execute(statement)


# Version-5 DDL (FS-08 §6, IP-10 T-141): a new, purely additive `SnapshotOutbox` table — copied
# verbatim from the frozen spec, no existing table is rebuilt or altered. `EventId` is both the
# primary key and a REFERENCES into the active `DetectionEvent` table only — the archive table
# (`DetectionEvent_archive_20260728`) is never referenced (FS-08 §6/§14). `permanent_failure` is
# deliberately not a permitted UploadStatus this increment (FS-08 §6) — a stuck row stays 'pending'
# and keeps retrying with capped backoff. The index supports the upload worker's oldest-first
# upload-ready scan (FS-08 §11/§12).
_SNAPSHOT_OUTBOX_V5_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS SnapshotOutbox (
        EventId          TEXT PRIMARY KEY REFERENCES DetectionEvent(EventId),
        LocalPath        TEXT NOT NULL,
        CaptureStatus    TEXT NOT NULL CHECK (CaptureStatus IN ('captured', 'capture_failed')),
        UploadStatus     TEXT NOT NULL CHECK (UploadStatus IN ('pending', 'uploaded'))
            DEFAULT 'pending',
        BackendAlertId   TEXT NULL,
        ContentType      TEXT NOT NULL,
        SizeBytes        INTEGER NOT NULL,
        Sha256           TEXT NOT NULL,
        CapturedAtUtc    TEXT NOT NULL,
        UploadedAtUtc    TEXT NULL,
        AttemptCount     INTEGER NOT NULL DEFAULT 0,
        LastAttemptAtUtc TEXT NULL,
        LastErrorCategory TEXT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_snapshot_outbox_upload_status_captured_at_utc
        ON SnapshotOutbox (UploadStatus, CapturedAtUtc)
    """,
)


# The version-4 -> version-5 migration (FS-08 §6, IP-10 T-141), applied inside a single transaction.
# Purely additive: it creates the new table/index and nothing else, so `DeviceIdentity`/
# `ConfigCache`/`DetectionEvent` rows are never touched. Exposed at module scope so a test can
# substitute a deliberately failing step to prove the whole migration rolls back.
_MIGRATION_V4_TO_V5_STATEMENTS: tuple[str, ...] = (
    *_SNAPSHOT_OUTBOX_V5_STATEMENTS,
    "UPDATE SchemaVersion SET Version = 5",
)


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    """Upgrade a version-4 database to version 5 in one atomic transaction (FS-08 §6, IP-10 T-141).

    Adds the `SnapshotOutbox` table and its one index; every other table is untouched. A failure at
    any step rolls the whole migration back, leaving the database at version 4 with no partial
    `SnapshotOutbox` table or index behind.
    """
    with transaction(connection):
        for statement in _MIGRATION_V4_TO_V5_STATEMENTS:
            connection.execute(statement)


def _detection_event_v6_ddl(table_name: str) -> str:
    """Return the ``CREATE TABLE`` DDL for the version-6 ``DetectionEvent`` shape (FS-09 §9).

    Adds two things to :func:`_detection_event_v4_ddl`'s shape: the ``DeliveryStatus`` CHECK widens
    to permit ``'suppressed_by_quota'`` (the Backend's ``quota_exceeded`` sync outcome maps to this
    terminal, never-resent status), and a new nullable ``FinalizedAtUtc`` column records when that
    terminal state was reached — deliberately separate from ``DeliveredAtUtc``, which keeps its
    narrower "successfully delivered" meaning. ``table_name`` is a fixed literal (``DetectionEvent``
    or ``DetectionEvent_v6``), never caller input.
    """
    return f"""
    CREATE TABLE {table_name} (
        EventId        TEXT    PRIMARY KEY,
        DeviceId       TEXT    NOT NULL,
        CameraId       TEXT    NOT NULL,
        SourceId       INTEGER NOT NULL,
        ClassId        INTEGER NOT NULL,
        ClassName      TEXT    NOT NULL,
        Confidence     REAL    NOT NULL,
        FrameNumber    INTEGER NOT NULL,
        DetectedAtUtc  TEXT    NOT NULL,  -- ISO-8601 UTC
        FrameWidth     INTEGER NOT NULL,
        FrameHeight    INTEGER NOT NULL,
        BboxLeft       REAL    NOT NULL,
        BboxTop        REAL    NOT NULL,
        BboxWidth      REAL    NOT NULL,
        BboxHeight     REAL    NOT NULL,
        DeliveryStatus TEXT    NOT NULL DEFAULT 'pending'
            CHECK (DeliveryStatus IN ('pending', 'delivered', 'suppressed_by_quota')),
        DeliveredAtUtc TEXT    NULL,  -- ISO-8601 UTC; set only when DeliveryStatus = 'delivered'
        FinalizedAtUtc TEXT    NULL,  -- ISO-8601 UTC; set only for 'suppressed_by_quota'
        CreatedAtUtc   TEXT    NOT NULL  -- ISO-8601 UTC
    )
    """


def _snapshot_outbox_v6_ddl(table_name: str) -> str:
    """Return the ``CREATE TABLE`` DDL for the version-6 ``SnapshotOutbox`` shape (FS-09 §10).

    Identical to the version-5 shape except the ``UploadStatus`` CHECK widens to permit
    ``'suppressed_by_quota'`` — set for any outbox row whose ``DetectionEvent`` was suppressed by
    the Branch daily Alert quota, so it is never selected for upload (FS-09 §10). ``table_name`` is
    a fixed literal (``SnapshotOutbox`` or ``SnapshotOutbox_v6``), never caller input.
    """
    return f"""
    CREATE TABLE {table_name} (
        EventId          TEXT PRIMARY KEY REFERENCES DetectionEvent(EventId),
        LocalPath        TEXT NOT NULL,
        CaptureStatus    TEXT NOT NULL CHECK (CaptureStatus IN ('captured', 'capture_failed')),
        UploadStatus     TEXT NOT NULL
            CHECK (UploadStatus IN ('pending', 'uploaded', 'suppressed_by_quota'))
            DEFAULT 'pending',
        BackendAlertId   TEXT NULL,
        ContentType      TEXT NOT NULL,
        SizeBytes        INTEGER NOT NULL,
        Sha256           TEXT NOT NULL,
        CapturedAtUtc    TEXT NOT NULL,
        UploadedAtUtc    TEXT NULL,
        AttemptCount     INTEGER NOT NULL DEFAULT 0,
        LastAttemptAtUtc TEXT NULL,
        LastErrorCategory TEXT NULL
    )
    """


# The version-5 -> version-6 migration (FS-09 §9/§10, IP-11 T-176), applied inside a single
# transaction. Rebuilds both `DetectionEvent` and `SnapshotOutbox` to widen their respective CHECK
# constraints; `DeviceIdentity`/`ConfigCache` are never touched. Every existing row's data is
# preserved unchanged (no pre-existing row can already hold `'suppressed_by_quota'`, since no
# earlier code ever wrote it). The `SnapshotOutbox` rebuild happens after the `DetectionEvent`
# rebuild so its `REFERENCES DetectionEvent(EventId)` always points at an existing table.
# Exposed at module scope so a test can substitute a deliberately failing step to prove the whole
# migration rolls back.
_MIGRATION_V5_TO_V6_STATEMENTS: tuple[str, ...] = (
    _detection_event_v6_ddl("DetectionEvent_v6"),
    """
    INSERT INTO DetectionEvent_v6
        (EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber,
         DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight,
         DeliveryStatus, DeliveredAtUtc, FinalizedAtUtc, CreatedAtUtc)
    SELECT EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber,
           DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight,
           DeliveryStatus, DeliveredAtUtc, NULL, CreatedAtUtc
    FROM DetectionEvent
    """,
    "DROP TABLE DetectionEvent",
    "ALTER TABLE DetectionEvent_v6 RENAME TO DetectionEvent",
    """
    CREATE INDEX IF NOT EXISTS idx_detection_event_delivery_status_created_at_utc
        ON DetectionEvent (DeliveryStatus, CreatedAtUtc)
    """,
    _snapshot_outbox_v6_ddl("SnapshotOutbox_v6"),
    """
    INSERT INTO SnapshotOutbox_v6
        (EventId, LocalPath, CaptureStatus, UploadStatus, BackendAlertId, ContentType, SizeBytes,
         Sha256, CapturedAtUtc, UploadedAtUtc, AttemptCount, LastAttemptAtUtc, LastErrorCategory)
    SELECT EventId, LocalPath, CaptureStatus, UploadStatus, BackendAlertId, ContentType, SizeBytes,
           Sha256, CapturedAtUtc, UploadedAtUtc, AttemptCount, LastAttemptAtUtc, LastErrorCategory
    FROM SnapshotOutbox
    """,
    "DROP TABLE SnapshotOutbox",
    "ALTER TABLE SnapshotOutbox_v6 RENAME TO SnapshotOutbox",
    """
    CREATE INDEX IF NOT EXISTS idx_snapshot_outbox_upload_status_captured_at_utc
        ON SnapshotOutbox (UploadStatus, CapturedAtUtc)
    """,
    "UPDATE SchemaVersion SET Version = 6",
)


def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
    """Upgrade a version-5 database to version 6 in one atomic transaction (FS-09 §9/§10, IP-11
    T-176).

    Rebuilds `DetectionEvent` and `SnapshotOutbox` to widen their `DeliveryStatus`/`UploadStatus`
    CHECK constraints to permit `'suppressed_by_quota'`; every other table is untouched. A failure
    at any step rolls the whole migration back, leaving the database at version 5 with no partial
    replacement table or index behind.

    Unlike the earlier rebuild migrations, this one temporarily disables foreign-key enforcement
    (only outside the transaction, where SQLite permits changing the pragma) for its duration:
    `SnapshotOutbox.EventId REFERENCES DetectionEvent(EventId)` would otherwise block `DROP TABLE
    DetectionEvent` while the old `SnapshotOutbox` row still references it, regardless of statement
    order — no ordering of the two rebuilds avoids that, since renaming one table never retargets an
    existing FK definition in the other. Re-enabled unconditionally in a `finally`, and since the
    whole rebuild is still one atomic transaction, a mid-way failure leaves both tables exactly as
    they were, with FK enforcement restored either way.
    """
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(connection):
            for statement in _MIGRATION_V5_TO_V6_STATEMENTS:
                connection.execute(statement)
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None
