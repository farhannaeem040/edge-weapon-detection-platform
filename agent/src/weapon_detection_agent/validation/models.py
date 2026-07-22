"""Typed outcome of one credential-validation attempt (IP-05 T-57).

The three semantics the Agent acts on (only T-59 acts; this task classifies):

* **Valid** — the Backend affirmed the current credentials (HTTP 200, standard success envelope).
* **ConfirmedRejected** — the Backend confirmed the credentials are invalid/revoked (HTTP 401 with
  the exact ``INVALID_DEVICE_CREDENTIALS`` failure envelope). Only this outcome may later lock the
  Agent (T-59); nothing else may.
* **Indeterminate** — anything that cannot be safely classified as either of the above: a transport
  failure, a timeout, a server fault, an unexpected status, a redirect, or a malformed response. An
  Indeterminate outcome never locks the Agent.

The result carries only safe metadata — the outcome, the HTTP status code when there was a response,
and a coarse diagnostic category for Indeterminate. It holds **no** Device ID, presented or stored
secret, Activation Key, response headers, or response body, so it cannot leak anything through a
``repr`` or a log.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CredentialValidationOutcome(Enum):
    """The classification of a single validation attempt."""

    VALID = "valid"
    CONFIRMED_REJECTED = "confirmed_rejected"
    INDETERMINATE = "indeterminate"


class IndeterminateReason(Enum):
    """A coarse, safe diagnostic category for an Indeterminate outcome — never raw error text."""

    TIMEOUT = "timeout"
    TRANSPORT_FAILURE = "transport_failure"
    SERVER_FAILURE = "server_failure"
    UNEXPECTED_STATUS = "unexpected_status"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class CredentialValidationResult:
    """The immutable result of one validation attempt. Carries no credential material.

    ``status_code`` is the HTTP status when a response arrived (``None`` for a transport failure
    or timeout, where there is no response). ``indeterminate_reason`` is present exactly when the
    outcome is Indeterminate.
    """

    outcome: CredentialValidationOutcome
    status_code: int | None = None
    indeterminate_reason: IndeterminateReason | None = None

    def __post_init__(self) -> None:
        is_indeterminate = self.outcome is CredentialValidationOutcome.INDETERMINATE
        if is_indeterminate and self.indeterminate_reason is None:
            raise ValueError("an Indeterminate result requires a reason")
        if not is_indeterminate and self.indeterminate_reason is not None:
            raise ValueError("only an Indeterminate result may carry a reason")

    @property
    def is_valid(self) -> bool:
        return self.outcome is CredentialValidationOutcome.VALID

    @property
    def is_confirmed_rejected(self) -> bool:
        return self.outcome is CredentialValidationOutcome.CONFIRMED_REJECTED

    @property
    def is_indeterminate(self) -> bool:
        return self.outcome is CredentialValidationOutcome.INDETERMINATE

    @classmethod
    def valid(cls, status_code: int) -> CredentialValidationResult:
        return cls(CredentialValidationOutcome.VALID, status_code)

    @classmethod
    def confirmed_rejected(cls, status_code: int) -> CredentialValidationResult:
        return cls(CredentialValidationOutcome.CONFIRMED_REJECTED, status_code)

    @classmethod
    def indeterminate(
        cls, reason: IndeterminateReason, status_code: int | None = None
    ) -> CredentialValidationResult:
        return cls(CredentialValidationOutcome.INDETERMINATE, status_code, reason)
