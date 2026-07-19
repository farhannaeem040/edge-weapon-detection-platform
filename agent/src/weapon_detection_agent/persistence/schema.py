"""SQLite schema definition and versioned initialization (IP-02 T-35, §7, §9, §10).

This module defines the version-1 schema (IP-02 §7 verbatim) and the single, idempotent initializer
that brings a database up to :data:`CURRENT_SCHEMA_VERSION`. The approach is deliberately small
(IP-02 D-6, Engineering Principle 9): one integer version guarded by a ``SchemaVersion`` table, DDL
applied inside one transaction, and no third-party migration framework — one version and three
tables do not justify a dependency.

What this module does **not** do (later tasks own these):

* It never reads or writes a ``DeviceIdentity`` or ``ConfigCache`` *record* — only the schema. The
  repositories are T-36.
* It seeds **no** rows. Initializing a fresh database records the schema version and nothing else;
  no fake device identity or config-cache row is inserted (IP-02 §8).
* It performs no directory creation, activation, Backend contact, or startup wiring.

Forward-only and idempotent: version-1 DDL uses ``CREATE TABLE IF NOT EXISTS`` and is only applied
when the database has no recorded version, so re-running against a current database is a safe no-op
that preserves every existing row. A future version 2 appends a new migration step; it never edits a
shipped one.
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
CURRENT_SCHEMA_VERSION = 1

# Version-1 DDL, verbatim from IP-02 §7. Exposed at module scope so the schema is defined in exactly
# one place (and so a test can substitute a deliberately failing step to prove rollback, IP-02 §16).
# Each statement is idempotent (IF NOT EXISTS); the ordered tuple is applied as one transaction.
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
    startup workflow (T-39) will use it. Returns the resulting schema version. Creating no directory
    and seeding no row, it only ensures the schema metadata exists.
    """
    with open_connection(database_path) as connection:
        return initialize_schema(connection)


def initialize_schema(connection: sqlite3.Connection) -> int:
    """Bring ``connection``'s database to :data:`CURRENT_SCHEMA_VERSION`, idempotently.

    * **Fresh database** (no ``SchemaVersion`` table): create all version-1 tables and record
      version 1, atomically. Returns 1.
    * **Already current** (version 1): do nothing destructive; preserve all data. Returns 1.
    * **Newer version**: raise :class:`UnsupportedSchemaVersionError` without modifying anything.
    * **Invalid version state**: raise :class:`InvalidSchemaStateError` without modifying anything.

    Returns the schema version now in effect.
    """
    version = read_schema_version(connection)

    if version is None:
        _LOGGER.info(
            "database_schema_initialization_started",
            extra={"target_version": CURRENT_SCHEMA_VERSION},
        )
        _apply_version_1(connection)
        _LOGGER.info(
            "database_schema_initialized",
            extra={"schema_version": CURRENT_SCHEMA_VERSION},
        )
        return CURRENT_SCHEMA_VERSION

    if version == CURRENT_SCHEMA_VERSION:
        _LOGGER.info("database_schema_already_current", extra={"schema_version": version})
        return version

    if version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"database schema version {version} is newer than the supported version "
            f"{CURRENT_SCHEMA_VERSION}; refusing to downgrade or modify it"
        )

    # A recorded version below the current one would be handled by forward migration steps once they
    # exist; none are defined for the version-1 milestone, so an in-range lower value cannot be
    # brought forward and is treated as an invalid state rather than silently rewritten.
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
    """Create the version-1 tables and record version 1 in one atomic transaction (IP-02 §7, §10).

    Any failure mid-way rolls the whole thing back, so the database is never left with only some
    tables or a version row without its tables.
    """
    with transaction(connection):
        for statement in SCHEMA_V1_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO SchemaVersion (Version) VALUES (?)", (CURRENT_SCHEMA_VERSION,)
        )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None
