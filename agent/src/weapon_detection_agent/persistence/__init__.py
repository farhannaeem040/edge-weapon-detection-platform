"""Local SQLite persistence for the Jetson Agent (IP-02 §7; IP-07 T-85).

This subpackage is the Agent's Local Persistence Manager (ARCH-001 §10.2) at the module level. It
provides connection management (:mod:`~weapon_detection_agent.persistence.database`) and the
versioned schema initializer (:mod:`~weapon_detection_agent.persistence.schema`).

It stores **metadata only** — the device identity, cached configuration, and accepted detection
events — never binary images or video (BR-006, ADR-004). Importing this subpackage performs no I/O:
it opens no connection, creates no file, and initializes no schema. Those happen only through
explicit function calls.

The repositories (:class:`DeviceIdentityRepository`, :class:`ConfigCacheRepository`,
:class:`DetectionEventRepository`) read and — for the Device Identity and Detection Events — write
those records (T-36, T-85). The ``ConfigCache`` repository is load-only (its writer is deferred,
OI-2). None of this contacts the Backend, performs activation, imports ``pyds``/DeepStream, or wires
into application startup (T-37/T-38/T-39; T-86/T-87 for detection events).
"""

from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.database import (
    DatabaseInitializationError,
    connect,
    open_connection,
    transaction,
)
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import (
    DetectionEventAlreadyExistsError,
    IdentityAlreadyExistsError,
    InvalidConfigCacheStateError,
    InvalidDetectionEventStateError,
    InvalidIdentityStateError,
    RepositoryError,
)
from weapon_detection_agent.persistence.models import CachedConfiguration, DeviceIdentity
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
    "CachedConfiguration",
    "ConfigCacheRepository",
    "DatabaseInitializationError",
    "DetectionEventAlreadyExistsError",
    "DetectionEventRepository",
    "DeviceIdentity",
    "DeviceIdentityRepository",
    "IdentityAlreadyExistsError",
    "InvalidConfigCacheStateError",
    "InvalidDetectionEventStateError",
    "InvalidIdentityStateError",
    "InvalidSchemaStateError",
    "RepositoryError",
    "UnsupportedSchemaVersionError",
    "connect",
    "initialize_database",
    "initialize_schema",
    "open_connection",
    "read_schema_version",
    "transaction",
]
