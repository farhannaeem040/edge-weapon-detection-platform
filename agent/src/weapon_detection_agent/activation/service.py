"""Activation and reactivation orchestration (IP-02 T-38, §9, §10, §12, §14).

``ActivationService.activate()`` coordinates the pieces built by earlier tasks — the Activation Key
resolver (T-38 §6.1), the Backend HTTP client (T-37), and the Device Identity repository (T-36) —
into the three startup outcomes IP-02 §12.1 defines:

* **First activation** (no stored identity + a key): call the Backend once, persist the returned
  identity, then delete a file-sourced key.
* **Reactivation** (a stored identity + a key): call the Backend once, verify the returned Device ID
  matches the stored one (fail loudly on a mismatch, OI-4), atomically replace the shared secret
  while retaining the Device ID and original ``ActivatedAt``, then delete a file-sourced key.
* **Already activated / no key** (a stored identity + no key): a no-op — no Backend call, no change.
  This is the ordinary restart path (§12.2).

It performs **exactly one** Backend request per activation and **never retries** (the one-time key
is not idempotent; IP-02 §14, amended). A timeout is surfaced as an *ambiguous* outcome, since the
Backend may have committed the activation before the response was lost.

Boundaries (T-38 owns none of these): no FastAPI lifespan or startup wiring (T-39), no schema
initialization or directory provisioning, no ``ConfigCache`` write, no DeepStream/heartbeat/command
handling. Its dependencies are injected — it constructs no client, repository, or clock globally,
reads no settings or environment, and configures no logging. Importing this module performs no I/O.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.errors import (
    ActivationKeyMissingError,
    ActivationOutcomeAmbiguousError,
    ActivationPersistenceError,
    ActivationTimeoutError,
    DeviceIdentityMismatchError,
)
from weapon_detection_agent.activation.key_resolver import (
    ActivationKeyResolver,
    KeySource,
    ResolvedActivationKey,
)
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity

_LOGGER = logging.getLogger("weapon_detection_agent.activation.service")

# The clock returns the "now" used for activation timestamps. Injectable for deterministic tests.
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ActivationOutcome(Enum):
    """Which of the three §12.1 branches an ``activate()`` call took."""

    FIRST_ACTIVATION = "first_activation"
    REACTIVATION = "reactivation"
    ALREADY_ACTIVATED = "already_activated"


@dataclass(frozen=True)
class ActivationServiceResult:
    """The safe, immutable outcome of an ``activate()`` call.

    Carries only non-sensitive information (IP-02 §13): the outcome, the public ``device_id``, the
    ``branch_id`` when the Backend returned one (``None`` for the no-op path — the stored identity
    holds no branch id), and the activation timestamps. It deliberately does **not** carry the
    shared secret, so it can never leak one through a ``repr`` or a serialization.
    """

    outcome: ActivationOutcome
    device_id: str
    activated_at: datetime
    last_activated_at: datetime
    branch_id: str | None = None


class ActivationService:
    """Coordinates key resolution, the Backend call, and local persistence (IP-02 §9)."""

    def __init__(
        self,
        *,
        backend_client: BackendActivationClient,
        identity_repository: DeviceIdentityRepository,
        key_resolver: ActivationKeyResolver,
        clock: Clock = _utc_now,
    ) -> None:
        self._backend_client = backend_client
        self._identity_repository = identity_repository
        self._key_resolver = key_resolver
        self._clock = clock

    async def activate(self) -> ActivationServiceResult:
        """Run the §12.1 startup decision once and return its typed outcome.

        Raises :class:`ActivationKeyMissingError` when a fresh Agent has no key; propagates the
        typed activation errors for every failure (a timeout becomes an
        :class:`ActivationOutcomeAmbiguousError`). Performs exactly one Backend request in the two
        activating branches, and none in the no-op branch.
        """
        existing = self._identity_repository.load()
        resolved = self._key_resolver.resolve()

        if existing is None:
            if resolved is None:
                raise ActivationKeyMissingError(
                    "no Activation Key is configured (set WDA_ACTIVATION_KEY or provision the "
                    "activation-key file); a fresh Agent cannot activate without one"
                )
            return await self._first_activation(resolved)

        if resolved is None:
            _LOGGER.info(
                "activation_not_required",
                extra={"outcome": ActivationOutcome.ALREADY_ACTIVATED.value},
            )
            return ActivationServiceResult(
                outcome=ActivationOutcome.ALREADY_ACTIVATED,
                device_id=existing.device_id,
                activated_at=existing.activated_at,
                last_activated_at=existing.last_activated_at,
            )

        return await self._reactivation(existing, resolved)

    async def _first_activation(self, resolved: ResolvedActivationKey) -> ActivationServiceResult:
        _LOGGER.info("first_activation_started", extra={"key_source": resolved.source.value})

        result = await self._call_backend(resolved)
        now = self._now_utc()

        identity = DeviceIdentity(
            device_id=result.device_id,
            shared_secret=result.shared_secret,
            activated_at=now,
            last_activated_at=now,
        )
        try:
            self._identity_repository.store(identity)
        except (RepositoryError, sqlite3.Error) as exc:
            _LOGGER.error("activation_persistence_failed", extra={"phase": "first_activation"})
            raise ActivationPersistenceError(
                "the Backend activation succeeded but storing the device identity locally failed; "
                "the request must not be repeated with the same key"
            ) from exc

        self._delete_file_key_if_needed(resolved)

        _LOGGER.info(
            "first_activation_completed",
            extra={"device_id": result.device_id, "branch_id": result.branch_id},
        )
        return ActivationServiceResult(
            outcome=ActivationOutcome.FIRST_ACTIVATION,
            device_id=result.device_id,
            activated_at=now,
            last_activated_at=now,
            branch_id=result.branch_id,
        )

    async def _reactivation(
        self, existing: DeviceIdentity, resolved: ResolvedActivationKey
    ) -> ActivationServiceResult:
        _LOGGER.info("reactivation_started", extra={"key_source": resolved.source.value})

        result = await self._call_backend(resolved)

        if result.device_id != existing.device_id:
            # Never adopt a new identity: the Device ID is permanent (FR-BRN-007, OI-4). No update,
            # no key-file deletion. The message names neither identifier.
            _LOGGER.error("activation_device_mismatch", extra={"device_id": existing.device_id})
            raise DeviceIdentityMismatchError(
                "the Backend returned a different device identity than the one already stored; "
                "the stored identity was left unchanged"
            )

        now = self._now_utc()
        try:
            self._identity_repository.replace_shared_secret(
                shared_secret=result.shared_secret, last_activated_at=now
            )
        except (RepositoryError, sqlite3.Error) as exc:
            _LOGGER.error("activation_persistence_failed", extra={"phase": "reactivation"})
            raise ActivationPersistenceError(
                "the Backend reactivation succeeded but replacing the stored shared secret failed; "
                "the request must not be repeated with the same key"
            ) from exc

        self._delete_file_key_if_needed(resolved)

        _LOGGER.info(
            "reactivation_completed",
            extra={"device_id": existing.device_id, "branch_id": result.branch_id},
        )
        return ActivationServiceResult(
            outcome=ActivationOutcome.REACTIVATION,
            device_id=existing.device_id,
            activated_at=existing.activated_at,  # original activation time is preserved
            last_activated_at=now,
            branch_id=result.branch_id,
        )

    async def _call_backend(self, resolved: ResolvedActivationKey) -> ActivationResult:
        """Make the single Backend request, mapping a timeout to the ambiguous-outcome error.

        Every other client error (transport, `401`, `5xx`/unexpected, malformed) propagates
        unchanged — it is already typed and safe, and leaves the local state untouched (no
        persistence has happened yet). No retry occurs (IP-02 §14, amended).
        """
        try:
            return await self._backend_client.activate(resolved.key)
        except ActivationTimeoutError as exc:
            _LOGGER.warning("activation_outcome_ambiguous")
            raise ActivationOutcomeAmbiguousError(
                "the activation request timed out; the Backend may have activated this device, but "
                "no reliable result was received. The stored identity was not changed, the request "
                "was not retried, and operator recovery or a new Activation Key is required"
            ) from exc

    def _delete_file_key_if_needed(self, resolved: ResolvedActivationKey) -> None:
        """Delete the key file after a fully successful activation, only when the key came from it.

        An environment key never causes a deletion (IP-02 §6.1). A cleanup failure is surfaced as
        :class:`ActivationKeyCleanupError` (raised by the resolver) — the already-committed identity
        is **not** rolled back; only the leftover file needs operator attention (§12).
        """
        if resolved.source is not KeySource.FILE:
            return
        try:
            self._key_resolver.delete_key_file()
        except Exception:
            _LOGGER.error("activation_key_cleanup_failed")
            raise
        _LOGGER.info("activation_key_file_removed")

    def _now_utc(self) -> datetime:
        """Return the injected clock's value, validated as timezone-aware and normalized to UTC."""
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the activation clock returned a naive (timezone-unaware) datetime")
        return now.astimezone(timezone.utc)
