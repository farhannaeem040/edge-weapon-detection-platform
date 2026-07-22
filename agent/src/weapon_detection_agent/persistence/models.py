"""Immutable typed records for the local SQLite store (IP-02 T-36, §7, §10).

Each model mirrors one row of the T-35 schema exactly — same fields, same meaning — with two
deliberate refinements over the raw row:

* **Timestamps are timezone-aware UTC ``datetime`` objects**, not strings. The schema stores them as
  ISO-8601 UTC ``TEXT``; :func:`to_iso_utc` / :func:`parse_iso_utc` are the single conversion point,
  so callers work with real datetimes and the on-disk representation stays consistent.
* **The device shared secret is a ``SecretStr``**, so it is redacted in every ``repr``/``str`` and
  by the logging redactor (IP-02 §10, §15). "Protected" on the Jetson means file-permission-
  protected (D-4): the value is stored as-is inside the ``0600`` database, with no application-layer
  encryption — this model neither adds nor claims encryption.

The configuration cache carries its stored ``ConfigJson`` as **raw text**. No configuration schema
is defined in this milestone (OI-2), so nothing here parses, validates, or invents configuration
fields; a later configuration feature owns that contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from pydantic import SecretStr


class OperationalState(str, Enum):
    """The Agent's local operational state (IP-05 T-58, §4.3, §7).

    This is a Jetson-local concept, deliberately distinct from the Backend's
    ``DeviceActivationStatus`` — the Agent has no ``DeviceIdentity`` row before first activation,
    so there is no local ``Unactivated`` state, and ``Offline`` is reserved for future connectivity.

    * ``OPERATIONAL`` — the stored credentials are usable; operational components may run.
    * ``REACTIVATION_REQUIRED`` — the credentials were revoked (a Backend key regeneration, detected
      by a confirmed ``401 INVALID_DEVICE_CREDENTIALS``); the local secret is cleared and it is
      locked until it manually reactivates.

    A ``str`` enum so its member ``value`` is exactly the text persisted in the ``OperationalState``
    column ("Operational" / "ReactivationRequired") — the same values the schema's CHECK enforces.
    """

    OPERATIONAL = "Operational"
    REACTIVATION_REQUIRED = "ReactivationRequired"


def to_iso_utc(value: datetime) -> str:
    """Serialize a timezone-aware datetime to ISO-8601 in UTC (the schema's storage form).

    Rejects a naive datetime rather than guessing its zone — a stored timestamp with an ambiguous
    offset would undermine the "ISO-8601 UTC" schema contract (IP-02 §7).
    """
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp back to a timezone-aware UTC datetime.

    Raises ``ValueError`` for text that is not parseable or lacks an offset; the caller turns that
    into a safe repository error (the raw text is never echoed).
    """
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DeviceIdentity:
    """The Agent's persistent local identity (one ``DeviceIdentity`` row, schema v2).

    ``device_id`` is a public identifier — it travels in a header on operational requests and is
    shown in the Dashboard (FS-02 §5.4), so it appears normally. ``shared_secret`` is a credential
    and is held as a ``SecretStr`` so it can never leak through a ``repr``, ``str``, log line, or
    exception; it is ``None`` exactly when the identity is locked (``REACTIVATION_REQUIRED``), where
    the revoked secret has been cleared (IP-05 §7). ``activated_at`` is set at first activation and
    never changes; ``last_activated_at`` advances on reactivation. ``operational_state`` is the
    local lock state.

    The state/secret invariant (mirroring the schema's CHECK, IP-05 §7) is enforced here too, so
    application code cannot construct an inconsistent identity: ``OPERATIONAL`` requires a secret,
    ``REACTIVATION_REQUIRED`` requires no secret. ``operational_state`` defaults to ``OPERATIONAL``
    (the first-activation and ordinary case), so the reactivation writer sets it explicitly.
    """

    device_id: str
    shared_secret: SecretStr | None
    activated_at: datetime
    last_activated_at: datetime
    operational_state: OperationalState = OperationalState.OPERATIONAL

    def __post_init__(self) -> None:
        if self.operational_state is OperationalState.OPERATIONAL and self.shared_secret is None:
            raise ValueError("an Operational device identity must have a shared secret")
        if (
            self.operational_state is OperationalState.REACTIVATION_REQUIRED
            and self.shared_secret is not None
        ):
            raise ValueError("a ReactivationRequired device identity must not have a shared secret")

    @property
    def can_authenticate(self) -> bool:
        """True only when the identity is Operational and a stored secret is present (IP-05 §2.1).

        A local, non-cryptographic gate: it says the credentials are *usable*, not that a secret is
        correct. A locked (``REACTIVATION_REQUIRED``) identity — even one that, through inconsistent
        data, still carried a secret — is never authenticatable, because the state is checked first.
        """
        return (
            self.operational_state is OperationalState.OPERATIONAL
            and self.shared_secret is not None
        )


@dataclass(frozen=True)
class CachedConfiguration:
    """The last synchronized configuration (one ``ConfigCache`` row).

    ``config_json`` is the stored payload as **raw text**: this milestone defines no configuration
    schema (OI-2), so it is neither parsed nor interpreted here. It is never logged or placed in an
    error message. ``updated_at`` is a timezone-aware UTC datetime.
    """

    config_json: str
    updated_at: datetime
