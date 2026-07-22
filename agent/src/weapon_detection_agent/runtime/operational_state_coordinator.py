"""The Agent operational-state coordinator and its credential-lock enforcement (IP-05 T-60, §7).

The durable source of truth for whether the Agent may operate is the SQLite ``DeviceIdentity`` row's
``OperationalState`` (T-58). This coordinator holds only the **runtime** enforcement of that fact:
it decides whether the operational components (T-60 contract — DeepStream supervision, detection,
alert sync, streaming, commands, siren, none of which exist yet) are allowed to run, starts them
only while ``Operational``, and stops/prevents them the instant the Agent is locked into
``ReactivationRequired``.

It reuses the T-58 :class:`OperationalState` enum — there is no second state type. It never invents
or overwrites persistent credential state: locking here is pure in-memory enforcement (T-59 already
persisted the cleared secret and the lock), and unlocking is permitted only after re-reading the
repository and confirming durable state is genuinely ``Operational`` with a usable secret. It makes
no Backend call, performs no activation, reads/deletes no Activation Key, and starts no polling.

Concurrency: a single :class:`asyncio.Lock` serializes every transition — starting components,
entering the lock, and unlocking — so a start and a lock requested concurrently cannot interleave.
The lock always wins the end state: whichever runs first runs to completion, and if a start ran
first its components are stopped by the subsequent lock; if the lock ran first a later start fails.
Every transition is directly awaitable; nothing is fired and forgotten.

Boundaries (T-61 owns these): this module wires nothing into the FastAPI lifespan or the startup
sequence, and does not decide *when* the coordinator is constructed or with which initial state. It
implements no operational feature — only the start/stop orchestration and the lock.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Sequence

from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.operational_components import (
    OperationalComponent,
    safe_component_name,
)
from weapon_detection_agent.validation.loop import (
    CredentialValidationLoopExitReason,
    CredentialValidationLoopResult,
)

_LOGGER = logging.getLogger("weapon_detection_agent.runtime.operational_state")


class OperationalStateError(RuntimeError):
    """Base class for an operational-state coordinator failure whose message is always safe."""


class OperationalComponentsLockedError(OperationalStateError):
    """A start was requested while the Agent is locked into ``ReactivationRequired``.

    Operational components must not run without valid credentials, so the start is refused rather
    than silently pretending it succeeded. The message names no Device ID, secret, or key.
    """


class OperationalComponentStartError(OperationalStateError):
    """Starting the operational components failed partway through.

    Components started during the failed attempt have been stopped again (best-effort, reverse
    order), so no partially running operational system remains. The coordinator's state is unchanged
    — a start failure never locks or unlocks the Agent. The failing component's raw error is chained
    as ``__cause__``; the message carries only the safe component name.
    """


class OperationalComponentShutdownError(OperationalStateError):
    """One or more operational components failed to stop while the Agent was locking.

    The Agent stays locked (``ReactivationRequired``) regardless — a stop failure never unlocks it —
    and every other component was still asked to stop. Only the safe static component names are
    reported; no raw component exception text (which might carry sensitive data) is included.
    """

    def __init__(self, component_names: Sequence[str]) -> None:
        self.component_names: tuple[str, ...] = tuple(component_names)
        joined = ", ".join(self.component_names) if self.component_names else "unknown"
        super().__init__(
            "one or more operational components failed to stop while locking the Agent "
            f"({joined}); the Agent remains locked"
        )


class ReactivationNotPersistedError(OperationalStateError):
    """Unlocking was requested but durable local state does not prove reactivation succeeded.

    The coordinator only mirrors persistence — it never activates — so it refuses to go
    ``Operational`` unless the stored identity is present, ``Operational``, and holds a usable
    secret. The Agent stays locked. The message names no Device ID, secret, or key.
    """


class UnknownValidationLoopResultError(OperationalStateError):
    """The validation loop returned a result the coordinator does not know how to handle safely.

    It is neither treated as ``Operational`` nor allowed to leak any detail; the coordinator fails
    clearly so a future exit reason cannot silently keep operational components running.
    """


class OperationalStateCoordinator:
    """Enforce, at runtime, whether operational components may run (IP-05 T-60).

    Construct with the T-58 identity repository (used only to *read* durable state when unlocking),
    the registered operational components (may be empty — real ones do not exist yet), and the
    initial :class:`OperationalState` a higher-level caller derived from the loaded identity (T-61
    owns that wiring). The coordinator never writes persistent credential state.
    """

    def __init__(
        self,
        *,
        identity_repository: DeviceIdentityRepository,
        components: Sequence[OperationalComponent] = (),
        initial_state: OperationalState,
    ) -> None:
        self._identity_repository = identity_repository
        self._components: tuple[OperationalComponent, ...] = tuple(components)
        self._state = initial_state
        # Components confirmed started and not yet confirmed stopped, in start order.
        self._running: list[OperationalComponent] = []
        self._lock = asyncio.Lock()

    @classmethod
    def from_identity(
        cls,
        identity: DeviceIdentity | None,
        *,
        identity_repository: DeviceIdentityRepository,
        components: Sequence[OperationalComponent] = (),
    ) -> OperationalStateCoordinator:
        """Build a coordinator whose initial state mirrors a loaded identity's ``OperationalState``.

        A missing identity is not a valid coordinator state (there is nothing to operate) — T-61
        handles the no-identity startup path — so this raises rather than guessing ``Operational``.
        """
        if identity is None:
            raise ValueError("cannot build an operational coordinator without a stored identity")
        return cls(
            identity_repository=identity_repository,
            components=components,
            initial_state=identity.operational_state,
        )

    @property
    def state(self) -> OperationalState:
        """The current runtime enforcement state (``Operational`` or ``ReactivationRequired``)."""
        return self._state

    @property
    def can_run_operational_components(self) -> bool:
        """True only while ``Operational`` — the single gate every start honours."""
        return self._state is OperationalState.OPERATIONAL

    async def start_operational_components(self) -> None:
        """Start every registered, not-yet-running component in order — only while ``Operational``.

        Refuses with :class:`OperationalComponentsLockedError` while locked (never a silent no-op
        that pretends success). Skips components already running, so a duplicate call never starts a
        component twice. If a component fails to start, the ones started *in this attempt* are
        stopped again in reverse order (best-effort) so no partially running system is left, and a
        :class:`OperationalComponentStartError` is raised; the coordinator's state is unchanged.
        """
        async with self._lock:
            if self._state is not OperationalState.OPERATIONAL:
                raise OperationalComponentsLockedError(
                    "operational components cannot start while the Agent is locked "
                    "(ReactivationRequired); manual reactivation is required first"
                )

            pending = [c for c in self._components if c not in self._running]
            if not pending:
                return

            _LOGGER.info("operational_components_starting", extra={"count": len(pending)})
            started_now: list[OperationalComponent] = []
            try:
                for component in pending:
                    await component.start()
                    self._running.append(component)
                    started_now.append(component)
            except Exception as exc:
                failing_name = safe_component_name(component)
                await self._rollback_started(started_now)
                raise OperationalComponentStartError(
                    f"operational component '{failing_name}' failed to start; components started "
                    "during this attempt were stopped and no operational component is running"
                ) from exc

            _LOGGER.info("operational_components_started", extra={"count": len(started_now)})

    async def enter_reactivation_required(self) -> None:
        """Lock the Agent: forbid starts and stop every running component (runtime enforcement).

        Sets the in-memory state to ``ReactivationRequired`` *before* any shutdown, so
        :attr:`can_run_operational_components` is false at once and any queued start is rejected.
        Idempotent: a second call stays locked and starts nothing, and only re-attempts a component
        a previous lock could not confirm stopped. It performs **no** persistence — T-59 already
        stored the cleared secret and the lock. If any component fails to stop, the Agent stays
        locked and :class:`OperationalComponentShutdownError` is raised after every stop is tried.
        """
        async with self._lock:
            was_operational = self._state is OperationalState.OPERATIONAL
            self._state = OperationalState.REACTIVATION_REQUIRED
            if was_operational:
                _LOGGER.info("operational_lock_entered")
            if self._running:
                await self._stop_running_locked()

    async def mark_operational_after_reactivation(self) -> None:
        """Unlock the runtime **after** T-58 has already persisted a successful reactivation.

        This never activates and never accepts a bare "it worked" flag: it re-reads the repository
        and unlocks only when the stored identity exists, is ``Operational``, and can authenticate
        (a usable secret is present). Otherwise the Agent stays locked and
        :class:`ReactivationNotPersistedError` is raised. It does **not** start components — the
        caller starts them explicitly afterwards, keeping persistence, unlocking, and startup
        observable as separate steps.
        """
        async with self._lock:
            try:
                identity = self._identity_repository.load()
            except (RepositoryError, sqlite3.Error) as exc:
                raise ReactivationNotPersistedError(
                    "could not read the local device identity to confirm reactivation; the Agent "
                    "remains locked"
                ) from exc

            if identity is None:
                raise ReactivationNotPersistedError(
                    "no stored device identity; reactivation is not persisted and the Agent "
                    "remains locked"
                )
            if identity.operational_state is not OperationalState.OPERATIONAL:
                raise ReactivationNotPersistedError(
                    "durable state is still ReactivationRequired; the Agent remains locked until "
                    "reactivation is persisted"
                )
            if not identity.can_authenticate:
                raise ReactivationNotPersistedError(
                    "the stored identity cannot authenticate (no usable secret); the Agent remains "
                    "locked"
                )

            self._state = OperationalState.OPERATIONAL
            _LOGGER.info("operational_state_restored_after_reactivation")

    async def handle_validation_loop_result(self, result: CredentialValidationLoopResult) -> None:
        """Consume the terminal T-59 result and enforce the runtime lock for a confirmed revocation.

        A ``REACTIVATION_REQUIRED`` exit locks the runtime and stops components (via
        :meth:`enter_reactivation_required`) — with no repeated persistence, no secret clearing, no
        restarted polling, and no activation. Any other (future/unknown) exit reason raises
        :class:`UnknownValidationLoopResultError` rather than silently assuming ``Operational``.
        Cancellation is never a result — T-59 propagates it — so it is not handled here.
        """
        if result.exit_reason is CredentialValidationLoopExitReason.REACTIVATION_REQUIRED:
            await self.enter_reactivation_required()
            return
        raise UnknownValidationLoopResultError(
            "the credential-validation loop returned an unrecognized exit reason; the Agent is not "
            "assumed operational"
        )

    async def _rollback_started(self, started_now: Sequence[OperationalComponent]) -> None:
        """Best-effort stop of components started during a failed start attempt, in reverse order.

        Each is removed from the running set once its stop has been attempted, so nothing from the
        failed attempt is left tracked as running. A stop that itself fails is logged safely (by
        name only) and does not mask the original start failure that is being raised.
        """
        for component in reversed(list(started_now)):
            name = safe_component_name(component)
            try:
                await component.stop()
            except Exception:
                _LOGGER.error("operational_shutdown_failed", extra={"component": name})
            if component in self._running:
                self._running.remove(component)

    async def _stop_running_locked(self) -> None:
        """Stop all currently running components in reverse start order while holding the lock.

        A component that stops cleanly is dropped from the running set; one that fails to stop is
        kept (so a later lock call can retry it) and its safe name is collected. After every
        component has been attempted, a non-empty failure set raises
        :class:`OperationalComponentShutdownError`. The state was already set to
        ``ReactivationRequired`` by the caller, so the lock is visible even when this raises.
        """
        failures: list[str] = []
        for component in reversed(list(self._running)):
            name = safe_component_name(component)
            try:
                await component.stop()
            except Exception:
                _LOGGER.error("operational_shutdown_failed", extra={"component": name})
                failures.append(name)
            else:
                self._running.remove(component)
                _LOGGER.info("operational_component_stopped", extra={"component": name})

        if failures:
            raise OperationalComponentShutdownError(failures)
