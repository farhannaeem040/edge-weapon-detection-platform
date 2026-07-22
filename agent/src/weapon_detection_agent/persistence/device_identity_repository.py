"""Repository for the Agent's persistent Device Identity (IP-02 T-36, §9, §10; ARCH-001 §13.2).

This is the only code that reads or writes the ``DeviceIdentity`` singleton row. It offers exactly
the three operations IP-02 grants — load, first-activation store, and atomic shared-secret
replacement — and nothing more. In particular it makes **no** decision about *whether* to
(re)activate or how to handle a returned Device ID that differs from the stored one: that policy is
the activation orchestration's (T-38). The Device ID is permanent here — no operation changes it.

Boundaries kept:

* No Backend contact, activation-key handling, or startup wiring (T-37/T-38/T-39).
* No directory creation and no schema initialization — the caller resolves
  ``AgentPaths.database_file`` (T-34) and initializes the schema (T-35) first; this repository only
  operates on an initialized database.
* No secret is logged or placed in an error; the ``DeviceId`` is a public identifier and may be
  (IP-02 §15).
* No module-level connection and no import-time I/O — a connection is opened only inside a call.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

from pydantic import SecretStr

from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.errors import (
    IdentityAlreadyExistsError,
    InvalidIdentityStateError,
)
from weapon_detection_agent.persistence.models import (
    DeviceIdentity,
    OperationalState,
    parse_iso_utc,
    to_iso_utc,
)

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.device_identity")

# A callable that opens a connection as a context manager. Defaults to open_connection(path); a test
# or later unit-of-work may inject its own, which is the seam the rollback test uses (§16.1).
ConnectionOpener = Callable[[], AbstractContextManager[sqlite3.Connection]]

_SELECT = (
    "SELECT DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, OperationalState "
    "FROM DeviceIdentity"
)
_INSERT = (
    "INSERT INTO DeviceIdentity "
    "(SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, "
    "LastActivatedAt, OperationalState) "
    "VALUES (1, ?, ?, ?, ?, ?)"
)
# Successful (re)activation: store the new secret, advance LastActivatedAt, back to Operational
# — retaining DeviceId and ActivatedAt (IP-05 §7). Setting OperationalState clears a lock.
_UPDATE_SECRET = (
    "UPDATE DeviceIdentity "
    "SET ProtectedSharedSecret = ?, LastActivatedAt = ?, OperationalState = 'Operational' "
    "WHERE SingletonGuard = 1"
)
# Confirmed revocation: atomically clear the secret and set the lock, preserving DeviceId and both
# timestamps (IP-05 §7). Both column changes commit together.
_MARK_REACTIVATION_REQUIRED = (
    "UPDATE DeviceIdentity "
    "SET ProtectedSharedSecret = NULL, OperationalState = 'ReactivationRequired' "
    "WHERE SingletonGuard = 1"
)


class DeviceIdentityRepository:
    """Load and persist the single ``DeviceIdentity`` row.

    Construct with the database path (the common case) or an explicit ``connection_factory``;
    exactly one must be given. A fresh, short-lived connection is used per operation — there is no
    retained connection.
    """

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        connection_factory: ConnectionOpener | None = None,
    ) -> None:
        if (database_path is None) == (connection_factory is None):
            raise ValueError("provide exactly one of database_path or connection_factory")
        if connection_factory is not None:
            self._open: ConnectionOpener = connection_factory
        else:
            resolved = Path(database_path)  # type: ignore[arg-type]
            self._open = lambda: open_connection(resolved)

    def load(self) -> DeviceIdentity | None:
        """Return the stored identity, or ``None`` when the Agent has not activated (no row).

        Raises :class:`InvalidIdentityStateError` for an impossible state — more than one row, or a
        stored timestamp that will not parse.
        """
        with self._open() as connection:
            rows = connection.execute(_SELECT).fetchall()

        if not rows:
            return None
        if len(rows) > 1:
            raise InvalidIdentityStateError("multiple device identity rows found")

        row = rows[0]
        try:
            activated_at = parse_iso_utc(row["ActivatedAt"])
            last_activated_at = parse_iso_utc(row["LastActivatedAt"])
        except ValueError as exc:
            raise InvalidIdentityStateError(
                "stored device identity has an invalid timestamp"
            ) from exc

        # The secret is NULL exactly when locked (ReactivationRequired). The CHECK keeps the
        # state/secret pair consistent; a corrupt state value or an inconsistent pair surfaces as a
        # safe InvalidIdentityStateError (its message names neither value).
        protected_secret = row["ProtectedSharedSecret"]
        shared_secret = SecretStr(protected_secret) if protected_secret is not None else None
        try:
            operational_state = OperationalState(row["OperationalState"])
            identity = DeviceIdentity(
                device_id=row["DeviceId"],
                shared_secret=shared_secret,
                activated_at=activated_at,
                last_activated_at=last_activated_at,
                operational_state=operational_state,
            )
        except ValueError as exc:
            raise InvalidIdentityStateError(
                "stored device identity is in an inconsistent state"
            ) from exc

        _LOGGER.info(
            "device_identity_loaded",
            extra={
                "device_id": identity.device_id,
                "operational_state": identity.operational_state.value,
            },
        )
        return identity

    def store(self, identity: DeviceIdentity) -> None:
        """Persist the identity produced by a first activation, in one transaction.

        Rejects the write with :class:`IdentityAlreadyExistsError` if an identity already exists —
        the repository never overwrites a stored Device ID (§10; the Device ID is permanent).
        """
        activated_at = to_iso_utc(identity.activated_at)
        last_activated_at = to_iso_utc(identity.last_activated_at)
        # A first-activation identity is always Operational with a secret (the model invariant), so
        # this is non-None; the guard keeps the type honest and the schema CHECK is the backstop.
        secret = identity.shared_secret.get_secret_value() if identity.shared_secret else None

        with self._open() as connection:
            with transaction(connection):
                existing = connection.execute(
                    "SELECT 1 FROM DeviceIdentity WHERE SingletonGuard = 1"
                ).fetchone()
                if existing is not None:
                    raise IdentityAlreadyExistsError("a device identity is already stored")
                connection.execute(
                    _INSERT,
                    (
                        identity.device_id,
                        secret,
                        activated_at,
                        last_activated_at,
                        identity.operational_state.value,
                    ),
                )

        _LOGGER.info("device_identity_saved", extra={"device_id": identity.device_id})

    def replace_shared_secret(
        self, *, shared_secret: SecretStr, last_activated_at: datetime
    ) -> None:
        """Persist a successful (re)activation: store the new secret and return to Operational.

        Atomically updates ``ProtectedSharedSecret``, ``LastActivatedAt``, and ``OperationalState``
        (to ``Operational``) in one transaction, **retaining the Device ID and ``ActivatedAt``**
        (§10, ADR-015; IP-05 §7). If the identity was locked (``ReactivationRequired``), this clears
        the lock; if it was already Operational, the state is simply reaffirmed. A failure mid-write
        rolls back, leaving the prior state — a null secret and the lock — intact (never torn
        or half-applied). Raises :class:`InvalidIdentityStateError` if there is no identity.
        This method never generates or changes the Device ID.
        """
        updated_at = to_iso_utc(last_activated_at)
        secret = shared_secret.get_secret_value()

        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(_UPDATE_SECRET, (secret, updated_at))
                if cursor.rowcount != 1:
                    raise InvalidIdentityStateError("no stored device identity to update")

        _LOGGER.info("device_identity_secret_replaced")

    def mark_reactivation_required(self) -> None:
        """Atomically clear the revoked secret and lock the identity into ``ReactivationRequired``.

        The persistence half of a confirmed credential revocation (IP-05 §7): in one transaction it
        sets ``ProtectedSharedSecret = NULL`` and ``OperationalState = 'ReactivationRequired'``,
        **preserving the Device ID and both timestamps**. Idempotent — calling on an already-locked
        identity leaves it locked with a null secret and unchanged fields. A failure mid-
        write rolls back, so the row is never left with the secret cleared but state unchanged (or
        vice versa). Raises :class:`InvalidIdentityStateError` if there is no identity to lock.

        This method only persists local state. It makes no Backend call, reads no HTTP response,
        schedules nothing, stops no operational component, deletes no key file, and attempts no
        activation — those belong to later tasks. No secret or row content is logged.
        """
        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(_MARK_REACTIVATION_REQUIRED)
                if cursor.rowcount != 1:
                    raise InvalidIdentityStateError("no stored device identity to lock")

        _LOGGER.info("device_identity_reactivation_required")
