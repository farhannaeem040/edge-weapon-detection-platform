"""Typed persistence-repository errors (IP-02 T-36, §10, §16.1).

These sit apart from the schema/connection errors in
:mod:`weapon_detection_agent.persistence.database` — those cover *setting up* the store, these cover
*operating on* its records. Every message names only structural facts (a row count, "a value is
invalid"); no stored value, secret, activation key, configuration payload, or SQL parameter ever
appears in one (IP-02 §10, §11).
"""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base class for a persistence-repository operation failure whose message is always safe."""


class IdentityAlreadyExistsError(RepositoryError):
    """A first-activation store was attempted while a Device Identity already exists.

    The repository never overwrites a stored identity: the Device ID is permanent (FS-02 §1.3,
    FR-BRN-007), and any decision about a *new* identity belongs to the activation/reactivation
    orchestration (T-38), not to this persistence layer.
    """


class InvalidIdentityStateError(RepositoryError):
    """The stored Device Identity is in a state that should not occur — more than one row, a missing
    row where one was required to update, or an unparseable stored timestamp."""


class InvalidConfigCacheStateError(RepositoryError):
    """The stored configuration cache is in a state that should not occur — more than one row, or an
    unparseable stored timestamp."""


class DetectionEventAlreadyExistsError(RepositoryError):
    """A detection event insert used an ``event_id`` that already has a stored row.

    The repository never overwrites an already-persisted detection event (IP-07 T-85) — a duplicate
    is a defensive, expected-to-be-unreachable condition (the cooldown tracker should prevent it in
    practice), surfaced clearly rather than silently replacing a real security event.
    """


class InvalidDetectionEventStateError(RepositoryError):
    """A stored detection event row is in a state that should not occur — an unparseable stored
    timestamp or an invalid ``event_id`` — so it cannot be reconstructed as a trustworthy
    :class:`~weapon_detection_agent.detection.models.DetectionEvent`."""


class SnapshotOutboxAlreadyExistsError(RepositoryError):
    """A ``SnapshotOutbox`` create-captured-row call used an ``event_id`` that already has a row.

    The repository never overwrites an already-captured snapshot row (FS-08 §6/IP-10 T-142) — one
    file per accepted ``EventId``, enforced by the ``EventId`` primary key.
    """


class SnapshotOutboxAlertConflictError(RepositoryError):
    """``associate_alert_id`` was called with an ``AlertId`` that conflicts with the one already
    stored for that ``EventId``.

    Idempotent re-association with the *same* ``AlertId`` is a no-op; naming a *different* one is
    treated as an integrity issue (mirrors FS-06 §5.2's ``EVENT_DATA_CONFLICT`` discipline) and is
    never silently overwritten.
    """
