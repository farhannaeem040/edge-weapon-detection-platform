"""Local SQLite persistence for the Jetson Agent (IP-02 §7).

This subpackage is the Agent's Local Persistence Manager (ARCH-001 §10.2) at the module level. It
provides connection management (:mod:`~weapon_detection_agent.persistence.database`) and the
versioned schema initializer (:mod:`~weapon_detection_agent.persistence.schema`).

It stores **metadata only** — the device identity and cached configuration — never binary images or
video (BR-006, ADR-004). Importing this subpackage performs no I/O: it opens no connection, creates
no file, and initializes no schema. Those happen only through explicit function calls.

The device-identity and config-cache *repositories* (reading and writing those records) are T-36 and
are not part of this task; only the schema that will hold them exists here.
"""

from weapon_detection_agent.persistence.database import (
    DatabaseInitializationError,
    connect,
    open_connection,
    transaction,
)
from weapon_detection_agent.persistence.schema import (
    CURRENT_SCHEMA_VERSION,
    InvalidSchemaStateError,
    UnsupportedSchemaVersionError,
    initialize_database,
    initialize_schema,
    read_schema_version,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DatabaseInitializationError",
    "InvalidSchemaStateError",
    "UnsupportedSchemaVersionError",
    "connect",
    "initialize_database",
    "initialize_schema",
    "open_connection",
    "read_schema_version",
    "transaction",
]
