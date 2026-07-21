"""SQLite connection management for the Jetson Agent (IP-02 T-35, §7).

This module owns *how* the Agent talks to its local SQLite store: opening a configured connection to
``<root>/database/agent.db``, closing it predictably, and providing an explicit transaction
boundary. It owns none of the *schema* (that is :mod:`weapon_detection_agent.persistence.schema`)
and none of the *records* (device identity and config cache are T-36's repositories).

Deliberate boundaries:

* **No import-time I/O.** Importing this module opens no connection, creates no file, and resolves
  no settings. A connection exists only after an explicit :func:`connect`/:func:`open_connection`
  call — there is no module-level connection (IP-02 §14).
* **No directory creation.** The parent ``database/`` directory is provisioned by T-34
  (:meth:`AgentPaths.provision`); a missing directory is a clear failure here, never a silent
  ``mkdir`` (IP-02 §6, §7).
* **No values in logs or errors.** Errors name at most a path — never a stored row, secret, or SQL
  parameter (IP-02 §12, §13).

Connection settings are deterministic (IP-02 §7):

* ``isolation_level=None`` — manual transaction control. Legacy sqlite3 auto-commits DDL that runs
  outside a transaction, which would make a partly-created schema impossible to roll back; manual
  mode plus an explicit ``BEGIN`` (see :func:`transaction`) is the mechanism that implements §7's
  "schema DDL inside one transaction" requirement. It is a transaction *mechanism*, not an isolation
  policy.
* ``row_factory = sqlite3.Row`` — named-column access for callers (the T-36 repositories).
* ``PRAGMA foreign_keys = ON`` — enforced per connection, since SQLite defaults it off. The
  version-1 tables define no foreign keys, so this is a no-op today; it is enabled so a later
  FK-bearing table is enforced without a connection change.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from weapon_detection_agent.config.paths import modes_enforceable

# The database file mode fixed by IP-02 §7 / ARCH-001 §13.3: owner read+write only, because the
# store holds the protected device shared secret. Applied on POSIX; skipped where not representable.
DATABASE_FILE_MODE = 0o600


class DatabaseInitializationError(RuntimeError):
    """A clear, safe failure setting up the SQLite store (e.g. the database directory is missing).

    Base class for the schema-state errors in :mod:`weapon_detection_agent.persistence.schema`. Its
    message never contains a stored value, secret, or SQL parameter — at most a path, which carries
    no credential (IP-02 §12).
    """


def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open a configured SQLite connection to ``database_path``; the caller owns closing it.

    Applies the deterministic settings documented in the module docstring and restricts the file to
    :data:`DATABASE_FILE_MODE` on POSIX. The parent directory must already exist — provisioning it
    is T-34's responsibility, not this module's — so a missing directory raises
    :class:`DatabaseInitializationError` rather than being silently created.
    """
    path = Path(database_path)
    if not path.parent.exists():
        # Do not create the directory (T-34 owns the filesystem layout); surface it clearly.
        raise DatabaseInitializationError(
            f"database directory does not exist: {path.parent} "
            "(provisioning the Agent filesystem layout is the layout task's responsibility)"
        )

    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    # PRAGMA foreign_keys must be set outside any transaction; connect() is that moment.
    connection.execute("PRAGMA foreign_keys = ON")
    _restrict_file_permissions(path)
    return connection


@contextmanager
def open_connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a configured connection as a context manager, closing it on exit (success or error).

    A bare ``sqlite3.Connection`` used as a context manager only commits/rolls back its transaction;
    it does **not** close. This wrapper guarantees the connection is closed predictably, so no
    connection is ever leaked or left module-global.
    """
    connection = connect(database_path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one explicit transaction: ``BEGIN`` on entry, ``COMMIT`` on success, ``ROLLBACK`` on any
    exception (IP-02 §7, §10).

    Requires a connection opened by :func:`connect` (``isolation_level=None``). Because SQLite rolls
    back DDL, a failure part-way through schema creation leaves the database exactly as it was —
    never a half-built schema or a version row without its tables.
    """
    connection.execute("BEGIN")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _restrict_file_permissions(path: Path) -> None:
    """Restrict the database file to owner read/write on POSIX (IP-02 §7).

    Idempotent (safe to reapply on every connect), and skipped where modes are not representable
    (Windows, §17), reusing the same platform gate the filesystem layout uses. Ownership is never
    changed — assigning the service user is the installer's job (T-41).
    """
    if modes_enforceable():
        os.chmod(path, DATABASE_FILE_MODE)
