"""Terminal outcome and lifecycle errors for the credential-validation loop (IP-05 T-59).

The polling lifecycle (``CredentialValidationMonitor``) ends in exactly one of three ways, and this
module names all three safely:

* a **terminal result** — :class:`CredentialValidationLoopResult` — returned when the loop has come
  to a definite, non-error stop. Today that is a single reason,
  :attr:`CredentialValidationLoopExitReason.REACTIVATION_REQUIRED`: either a confirmed rejection was
  persisted as the local lock, or the identity was already locked when the loop started. T-60 will
  consume this result to stop operational components;
* a **lifecycle error** — a :class:`CredentialValidationLoopError` subclass — raised when the loop
  cannot safely continue: there is no local identity to validate
  (:class:`DeviceIdentityUnavailableError`), or a confirmed rejection could not be persisted
  (:class:`CredentialLockPersistenceError`, a security-critical local-persistence failure that must
  never be mistaken for "credentials might still be valid");
* **cancellation** — ``asyncio.CancelledError`` — which is never converted into a result or a
  lifecycle error and simply propagates for a clean shutdown.

Nothing here carries credential material: neither the result nor any error message holds a Device
ID, a shared or protected secret, an Activation Key, a raw HTTP body, or request headers, so none
can leak through a ``repr``, a log line, or an exception chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CredentialValidationLoopExitReason(Enum):
    """Why the validation loop reached a definite, non-error stop."""

    # The credentials were confirmed revoked (or were already locked locally): the loop persisted /
    # observed ``ReactivationRequired`` and stopped. The only terminal reason in this milestone.
    REACTIVATION_REQUIRED = "reactivation_required"


@dataclass(frozen=True)
class CredentialValidationLoopResult:
    """The immutable terminal result of the validation loop. Carries no credential material.

    It reports only *why* the loop stopped (:class:`CredentialValidationLoopExitReason`) — never a
    Device ID, secret, key, response body, or header — so it is safe to log or ``repr``. T-60 reads
    ``exit_reason`` to decide how to stop operational components; it is not consumed here.
    """

    exit_reason: CredentialValidationLoopExitReason

    @property
    def is_reactivation_required(self) -> bool:
        return self.exit_reason is CredentialValidationLoopExitReason.REACTIVATION_REQUIRED

    @classmethod
    def reactivation_required(cls) -> CredentialValidationLoopResult:
        return cls(CredentialValidationLoopExitReason.REACTIVATION_REQUIRED)


class CredentialValidationLoopError(RuntimeError):
    """Base class for a validation-loop lifecycle failure whose message is always safe."""


class DeviceIdentityUnavailableError(CredentialValidationLoopError):
    """There is no usable local identity to validate — no stored identity, or an identity that
    cannot authenticate for a reason other than the ``ReactivationRequired`` lock.

    This is **not** a Backend-confirmed rejection: no request is made and no lock is applied. It is
    a distinct local-state failure so a caller never treats a missing identity as revoked.
    """


class CredentialLockPersistenceError(CredentialValidationLoopError):
    """A confirmed rejection was received but persisting the local ``ReactivationRequired`` lock
    failed — a security-critical local-persistence failure.

    The loop must not continue validating as though the credentials might still be valid, and must
    not return the successful reactivation-required result, because the lock was never committed.
    The message names only that persistence failed — never a Device ID, secret, or row content; the
    underlying repository/SQLite error is chained as ``__cause__`` (itself safe). T-60/T-61 define
    the fail-closed runtime reaction to this fatal error.
    """
