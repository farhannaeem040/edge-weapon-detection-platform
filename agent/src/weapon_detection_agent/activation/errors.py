"""Typed errors for the Backend activation client (IP-02 T-37, §11, §13).

Every message is safe by construction: it carries at most an HTTP status code and the Backend's
approved public error code (e.g. ``INVALID_ACTIVATION_KEY``). No message ever contains the
Activation Key, the shared secret, the raw request body, the raw response body, response headers, or
an HTTPX request object — those are the exact things §13/§15 forbid from leaving the process.
"""

from __future__ import annotations


class ActivationClientError(RuntimeError):
    """Base class for any failure of a single activation request."""


class ActivationTimeoutError(ActivationClientError):
    """The activation request timed out before a response was received.

    Because ``POST /api/v1/activate`` consumes a one-time key and is not idempotent, a timeout is
    an **ambiguous outcome**: the Backend may or may not have committed the activation. The client
    never retries it (IP-02 §14, as amended); resolving the ambiguity is the orchestration's job
    (T-38).
    """


class ActivationTransportError(ActivationClientError):
    """The activation request could not reach the Backend (connection refused, DNS failure, reset,
    protocol error). No response was received; the client does not retry (IP-02 §14, as amended)."""


class ActivationRejectedError(ActivationClientError):
    """The Backend rejected the Activation Key with the expected uniform ``401`` /
    ``INVALID_ACTIVATION_KEY`` (FS-02 §5.6, §10.4, AC-15).

    A deterministic credential rejection: never retried. Carries only the safe status code and error
    code — never the presented key or any response body.
    """

    def __init__(self, *, status_code: int, error_code: str | None) -> None:
        self.status_code = status_code
        self.error_code = error_code
        detail = error_code or "no error code"
        super().__init__(f"the Backend rejected the activation key (HTTP {status_code}, {detail})")


class ActivationServerError(ActivationClientError):
    """The Backend returned an unexpected, unsuccessful status — a ``5xx`` server fault or any other
    status that is neither the ``200`` success nor the expected ``401`` rejection.

    Carries only the status code. Not retried (IP-02 §14, as amended).
    """

    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"the Backend returned an unexpected HTTP status ({status_code})")


class InvalidActivationResponseError(ActivationClientError):
    """A ``200`` response whose body the client cannot trust — not valid JSON, the wrong envelope
    shape, ``success`` not ``true``, or a missing/blank/mistyped ``deviceId``/``sharedSecret``/
    ``branchId``.

    The message names only the structural problem (and at most a JSON *field name*); it never
    contains a stored value or the shared secret (IP-02 §11, §13).
    """
