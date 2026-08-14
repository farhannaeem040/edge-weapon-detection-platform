"""The Agent's credential-validation polling lifecycle (IP-05 T-59, §6, OI-C).

``CredentialValidationMonitor`` connects the three pieces the earlier tasks built:

* **T-56** — ``settings.credential_validation_interval_seconds``: how long to wait between attempts;
* **T-57** — :class:`~weapon_detection_agent.validation.client.CredentialValidationClient`: performs
  one detect-only validation request and returns a typed, already-classified outcome;
* **T-58** — ``DeviceIdentityRepository`` and the persistent ``OperationalState``: the local
  identity and the atomic lock transition.

The lifecycle is a simple **serial** loop — validate → classify → wait one interval → validate again
— run inside a single awaitable, :meth:`run`. The first validation happens immediately (no initial
wait); every subsequent attempt happens only after one configured interval. Because the loop awaits
each request fully before waiting, and never starts a background task, there is at most one request
in flight and requests never overlap. The detection bound while connected is therefore one interval
plus one request timeout — not instantaneous.

What each outcome does:

* **Valid** — nothing is persisted; the identity, secret, state, and timestamps are all preserved;
  the loop simply waits and validates again. A debug event only (never noisy at info per 30 s).
* **Indeterminate** — a transport failure, timeout, server fault, unexpected status, or malformed
  response: the loop does **not** lock, does **not** clear the secret, and adds **no** immediate
  retry; it just waits one interval and tries again. Backend unavailability never revokes.
* **ConfirmedRejected** — the one outcome that may lock: it atomically clears the secret and sets
  ``ReactivationRequired`` (T-58), then stops and returns the terminal reactivation-required result.
  A failure to persist that lock is fatal and surfaces as a lifecycle error — never a "maybe valid".

Boundaries (later tasks own these): the monitor stops **no** operational component, runs **no**
operational-state coordinator (T-60), changes **no** FastAPI startup policy and is **not** wired
into the lifespan (T-61), and never activates — it never calls ``/api/v1/activate``, reads or
deletes an Activation Key, or retrieves a replacement. It never touches Online/Offline state,
last-seen, health, configuration, alerts, or branch information. Its only writes are, on a confirmed
rejection, the single T-58 lock operation.

Resource ownership: the validation client is **injected and caller-owned** — the monitor reuses that
one client for every iteration (it never constructs a client per request) and never closes it.
Cancellation is honoured cleanly: ``asyncio.CancelledError`` (raised while waiting or mid-request)
is never caught and never converted into Valid/Indeterminate/ConfirmedRejected/ReactivationRequired.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable

from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.validation.client import CredentialValidationClient
from weapon_detection_agent.validation.loop import (
    CredentialLockPersistenceError,
    CredentialValidationLoopResult,
    DeviceIdentityUnavailableError,
)
from weapon_detection_agent.validation.models import CredentialValidationResult

_LOGGER = logging.getLogger("weapon_detection_agent.validation.monitor")

# An awaitable wait of ``delay`` seconds. Defaults to ``asyncio.sleep`` (monotonic, cancellable);
# tests inject a deterministic waiter so no real interval elapses. It is the wait between attempts —
# never the HTTP timeout, which stays the T-57 client's ``WDA_HTTP_TIMEOUT_SECONDS``.
Sleeper = Callable[[float], Awaitable[None]]


class CredentialValidationMonitor:
    """Periodically validate the stored credentials; lock and stop on a confirmed rejection.

    Construct with the T-58 identity repository, the T-57 validation client (caller-owned — not
    closed here), and the T-56 interval in seconds. ``sleeper`` is the injectable wait seam; leave
    it at the default ``asyncio.sleep`` in production.
    """

    def __init__(
        self,
        *,
        identity_repository: DeviceIdentityRepository,
        validation_client: CredentialValidationClient,
        interval_seconds: float,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._identity_repository = identity_repository
        self._validation_client = validation_client
        self._interval_seconds = interval_seconds
        self._sleep = sleeper

    async def run(self) -> CredentialValidationLoopResult:
        """Run the validation loop until it reaches a terminal stop, then return its typed result.

        Validates immediately, then once per configured interval, with never more than one request
        in flight. Returns a ``CredentialValidationLoopResult`` (``REACTIVATION_REQUIRED``) when
        a confirmed rejection is locked or the identity is already locked. Raises
        :class:`DeviceIdentityUnavailableError` when there is no usable identity, and
        :class:`CredentialLockPersistenceError` when a confirmed rejection cannot be persisted.
        ``asyncio.CancelledError`` propagates unchanged for a clean shutdown.
        """
        _LOGGER.info(
            "credential_validation_monitor_started",
            extra={"interval_seconds": self._interval_seconds},
        )
        while True:
            terminal = await self._attempt_once()
            if terminal is not None:
                return terminal
            # Non-terminal (Valid or Indeterminate): wait exactly one configured interval before the
            # next attempt. No immediate retry; the wait is monotonic and promptly cancellable.
            await self._sleep(self._interval_seconds)

    async def _attempt_once(self) -> CredentialValidationLoopResult | None:
        """Perform one loading + validation step; return a terminal result or ``None`` to continue.

        Returns a terminal result when the identity is already locked, or a confirmed rejection has
        just been locked. Returns ``None`` when the outcome (Valid or Indeterminate) means "wait and
        try again". Raises a lifecycle error when there is no usable identity or the lock cannot be
        persisted. Loads the identity fresh every time so a local state change is observed before
        any request is made — a locked or missing identity never produces an HTTP call.
        """
        identity = self._load_authenticatable_identity()
        if identity is None:
            # Already locked locally: never contact the Backend, never validate a NULL secret — just
            # report the terminal reactivation-required stop.
            _LOGGER.info("credential_validation_monitor_locked_local")
            return CredentialValidationLoopResult.reactivation_required()

        assert identity.shared_secret is not None  # guaranteed by can_authenticate  # noqa: S101
        result = await self._validation_client.validate(identity.device_id, identity.shared_secret)
        return self._handle_result(result)

    def _load_authenticatable_identity(self) -> DeviceIdentity | None:
        """Load the identity and decide whether it is validatable, without fabricating credentials.

        Returns the identity when it is Operational with a usable secret. Returns ``None`` when it
        is locally locked (``ReactivationRequired``) — the caller turns that into the terminal stop.
        Raises :class:`DeviceIdentityUnavailableError` when no identity exists or one exists that
        cannot authenticate for any other reason — a distinct local-state failure, never treated
        as a Backend-confirmed rejection. A structurally invalid stored identity surfaces as the
        repository's own safe error.
        """
        identity = self._identity_repository.load()
        if identity is None:
            _LOGGER.error("credential_validation_monitor_no_identity")
            raise DeviceIdentityUnavailableError(
                "no local device identity is stored; the credential-validation loop has nothing to "
                "validate and does not fabricate credentials"
            )
        if identity.operational_state is OperationalState.REACTIVATION_REQUIRED:
            return None
        if not identity.can_authenticate:
            # Operational-but-unusable should be impossible under the T-58 model invariant; fail
            # safely rather than send a request without a secret.
            _LOGGER.error("credential_validation_monitor_unauthenticatable_identity")
            raise DeviceIdentityUnavailableError(
                "the stored device identity cannot authenticate; the credential-validation loop "
                "will not send a request without a usable secret"
            )
        return identity

    def _handle_result(
        self, result: CredentialValidationResult
    ) -> CredentialValidationLoopResult | None:
        """Act on the T-57 classification; return a terminal result or ``None`` to keep polling."""
        if result.is_confirmed_rejected:
            return self._lock_on_confirmed_rejection()

        if result.is_valid:
            # Nothing persisted; identity, secret, state, and timestamps all preserved. Debug only,
            # to avoid noisy info logging on every successful validation.
            _LOGGER.debug(
                "credential_validation_monitor_valid", extra={"status": result.status_code}
            )
            return None

        # Indeterminate: never locks and never clears the secret. Log only safe metadata (operation,
        # coarse reason, and status when present) — never the Device ID, secret, body, or headers.
        reason = result.indeterminate_reason.value if result.indeterminate_reason else None
        _LOGGER.debug(
            "credential_validation_monitor_indeterminate",
            extra={"reason": reason, "status": result.status_code},
        )
        return None

    def _lock_on_confirmed_rejection(self) -> CredentialValidationLoopResult:
        """Persist the local lock for a confirmed rejection, then return the terminal result.

        Calls the single T-58 atomic operation (clear the secret + set ``ReactivationRequired``,
        preserving Device ID and both timestamps). Only on its success does the loop return the
        reactivation-required result and stop — no further request, no second lock attempt, no
        activation. If persistence fails, the successful result is **not** returned: the failure is
        wrapped in a credential-safe :class:`CredentialLockPersistenceError` and raised.
        """
        try:
            self._identity_repository.mark_reactivation_required()
        except (RepositoryError, sqlite3.Error) as exc:
            # A confirmed rejection that could not be locked is security-critical: never fall back
            # to "maybe valid", never return the success result. The message and chained cause hold
            # no credential material.
            _LOGGER.error("credential_validation_monitor_lock_persistence_failed")
            raise CredentialLockPersistenceError(
                "a confirmed credential rejection was received but persisting the local "
                "reactivation-required lock failed; the validation loop stopped without confirming "
                "the lock"
            ) from exc

        _LOGGER.info("credential_validation_monitor_reactivation_locked")
        return CredentialValidationLoopResult.reactivation_required()
