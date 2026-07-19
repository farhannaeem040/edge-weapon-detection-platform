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
