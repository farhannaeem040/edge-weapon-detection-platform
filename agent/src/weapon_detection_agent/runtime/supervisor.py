"""The Agent runtime supervisor: the approved startup decision tree and lifecycle (IP-05 T-61, §12).

This is the single orchestration that decides, at startup, whether the Agent activates, validates,
operates, or stays locked — wiring together the pieces the earlier tasks built:

* the Activation Key resolver and :class:`ActivationService` (first activation / reactivation);
* the T-57 :class:`CredentialValidationClient` (the startup validation, and the monitor's);
* the T-58 :class:`DeviceIdentityRepository` and persistent ``OperationalState`` (source of truth);
* the T-59 :class:`CredentialValidationMonitor` (the running validation loop);
* the T-60 :class:`OperationalStateCoordinator` (runtime start/stop + the credential lock).

Startup inspects four facts — does an identity exist, is an Activation Key present, what is the
persisted ``OperationalState``, and is a usable secret stored — and takes exactly one branch:

* **A — no identity, key present:** first activation (one ``POST /activate``), persist, delete the
  file key, then go operational. No extra startup validation — activation just proved the secret.
* **B — identity present, key present:** manual reactivation (one ``POST /activate``, same DeviceId
  required), persist the replacement secret, delete the file key, then go operational.
* **C — identity present, no key, ReactivationRequired:** stay locked; no Backend call, no NULL
  secret validated, no components, no monitor. The control process stays alive for manual recovery.
* **D — identity present, no key, Operational:** one startup validation. Valid or Indeterminate →
  operate (offline-start preserves NFR-REL-001); ConfirmedRejected → persist the lock and stay
  locked; a failure to persist that lock is fail-closed and fatal.

Fail-loud (no offline fallback): no identity and no key, a structurally invalid/unreadable local
identity, a Backend/DeviceId-mismatch/persistence failure in A/B. A local database failure is never
mistaken for an offline Backend. The Agent never obtains a replacement key automatically — a new key
arrives only via the operator's stop → ``set-activation-key.sh`` → start, which the next startup
turns into exactly one activation request.

Ownership: the supervisor owns the validation client it is given (closing it on shutdown) unless a
caller keeps ownership; it never owns the activation Backend client (the lifespan does). It runs
exactly one monitor task, tracked and cancelled cleanly on shutdown — never a fire-and-forget task.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Protocol

from weapon_detection_agent.activation.errors import ActivationKeyMissingError
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.service import ActivationService, ActivationServiceResult
from weapon_detection_agent.config.settings import AgentSettings
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.operational_components import OperationalComponent
from weapon_detection_agent.runtime.operational_state_coordinator import (
    OperationalComponentShutdownError,
    OperationalStateCoordinator,
)
from weapon_detection_agent.validation.client import CredentialValidationClient
from weapon_detection_agent.validation.loop import CredentialValidationLoopResult
from weapon_detection_agent.validation.monitor import CredentialValidationMonitor

_LOGGER = logging.getLogger("weapon_detection_agent.runtime.supervisor")


class AgentStartupError(RuntimeError):
    """A fatal, credential-safe startup failure that must prevent the Agent from operating.

    Raised for the fail-loud branches (structurally invalid local identity, a startup lock that
    could not be persisted). The message names only the failure category — never a Device ID, key,
    secret, response body, or row content; any underlying cause is chained but never logged raw.
    """


class AgentRuntimeFatalError(RuntimeError):
    """A recorded, credential-safe fatal failure of the running validation monitor.

    Set on the supervisor (not raised) when the monitor terminates fatally after startup, so the
    runtime is observably fail-closed. It carries no credential material and does not chain the raw
    cause, so neither its ``repr`` nor a log of it can leak anything.
    """


class StartupBranch(Enum):
    """Which approved startup branch a :meth:`AgentRuntimeSupervisor.startup` call took."""

    FIRST_ACTIVATION = "first_activation"
    REACTIVATION = "reactivation"
    LOCKED_REACTIVATION_REQUIRED = "locked_reactivation_required"
    OPERATIONAL_VALIDATED = "operational_validated"
    OPERATIONAL_OFFLINE = "operational_offline"
    STARTUP_CONFIRMED_REJECTED = "startup_confirmed_rejected"


class _MonitorRunnable(Protocol):
    """The one method the supervisor needs from a validation monitor — its awaitable loop."""

    async def run(self) -> CredentialValidationLoopResult: ...


MonitorFactory = Callable[[], _MonitorRunnable]


class AgentRuntimeSupervisor:
    """Own the Agent's startup decision tree, the validation-monitor task, and clean shutdown.

    Construct with the already-built foundation components. ``validation_client`` is used for the
    Branch-D startup validation and (via the default monitor factory) for the running monitor; the
    supervisor closes it on shutdown when ``owns_validation_client`` is true (the lifespan transfers
    ownership; tests injecting a shared client set it false). ``monitor_factory`` builds the monitor
    task's runnable — the default builds the real T-59 monitor bound to this client and the T-56
    interval; tests inject a controllable fake.
    """

    def __init__(
        self,
        *,
        settings: AgentSettings,
        identity_repository: DeviceIdentityRepository,
        key_resolver: ActivationKeyResolver,
        activation_service: ActivationService,
        validation_client: CredentialValidationClient,
        components: Sequence[OperationalComponent] = (),
        monitor_factory: MonitorFactory | None = None,
        owns_validation_client: bool = True,
    ) -> None:
        self._settings = settings
        self._identity_repository = identity_repository
        self._key_resolver = key_resolver
        self._activation_service = activation_service
        self._validation_client = validation_client
        self._components = tuple(components)
        self._monitor_factory = monitor_factory or self._default_monitor_factory
        self._owns_validation_client = owns_validation_client

        self._coordinator: OperationalStateCoordinator | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._started = False
        self._shutdown_done = False
        self._client_closed = False

        self.startup_branch: StartupBranch | None = None
        self.activation_result: ActivationServiceResult | None = None
        self.fatal_error: AgentRuntimeFatalError | None = None

    # --- Introspection (safe; no credential material) ------------------------------------------

    @property
    def coordinator(self) -> OperationalStateCoordinator | None:
        """The operational-state coordinator once startup has constructed it, else ``None``."""
        return self._coordinator

    @property
    def state(self) -> OperationalState | None:
        """The coordinator's runtime state, or ``None`` before the coordinator exists."""
        return self._coordinator.state if self._coordinator is not None else None

    @property
    def is_monitor_running(self) -> bool:
        """True while exactly one monitor task exists and has not finished."""
        return self._monitor_task is not None and not self._monitor_task.done()

    # --- Startup -------------------------------------------------------------------------------

    async def startup(self) -> None:
        """Run the approved startup decision tree exactly once.

        Raises :class:`AgentStartupError` (or an existing activation error) for a fail-loud branch;
        leaves the runtime locked for Branch C / D3; goes operational and starts one monitor task
        for Branch A / B / D1 / D2. A second call raises :class:`AgentStartupError` rather than
        re-running — startup is not idempotent by design (it would duplicate the monitor).
        """
        if self._started:
            raise AgentStartupError("startup has already been invoked")
        self._started = True

        identity = self._load_identity()
        resolved = self._key_resolver.resolve()

        if resolved is not None:
            await self._startup_with_key(identity)
        elif identity is None:
            _LOGGER.error("agent_startup_no_identity_no_key")
            raise ActivationKeyMissingError(
                "no device identity and no Activation Key are present; a fresh Agent cannot "
                "operate without a manually provisioned key"
            )
        elif identity.operational_state is OperationalState.REACTIVATION_REQUIRED:
            self._enter_locked(StartupBranch.LOCKED_REACTIVATION_REQUIRED)
        elif identity.can_authenticate:
            await self._startup_validate_operational(identity)
        else:
            # Operational-without-secret is impossible under the T-58 invariant; fail safely.
            _LOGGER.error("agent_startup_structurally_invalid_identity")
            raise AgentStartupError(
                "the stored device identity is structurally invalid (Operational without a usable "
                "secret); startup cannot proceed"
            )

    async def _startup_with_key(self, identity_before: DeviceIdentity | None) -> None:
        """Branch A / B: activate exactly once, then go operational (no extra validation)."""
        activation = await self._activation_service.activate()
        self.activation_result = activation
        self.startup_branch = (
            StartupBranch.FIRST_ACTIVATION
            if identity_before is None
            else StartupBranch.REACTIVATION
        )
        _LOGGER.info(
            "agent_startup_activation_complete", extra={"branch": self.startup_branch.value}
        )

        identity = self._identity_repository.load()
        if identity is None or not identity.can_authenticate:
            raise AgentStartupError(
                "activation reported success but the persisted identity is not usable; startup "
                "cannot proceed"
            )
        await self._enter_operational(identity)

    async def _startup_validate_operational(self, identity: DeviceIdentity) -> None:
        """Branch D: one startup validation, then operate (D1/D2) or lock (D3/D4)."""
        assert identity.shared_secret is not None  # can_authenticate guarantees this  # noqa: S101
        result = await self._validation_client.validate(identity.device_id, identity.shared_secret)

        if result.is_confirmed_rejected:
            await self._startup_confirmed_rejected()
            return

        self.startup_branch = (
            StartupBranch.OPERATIONAL_VALIDATED
            if result.is_valid
            else StartupBranch.OPERATIONAL_OFFLINE
        )
        _LOGGER.info(
            "agent_startup_validation_classified", extra={"branch": self.startup_branch.value}
        )
        await self._enter_operational(identity)

    async def _startup_confirmed_rejected(self) -> None:
        """Branch D3/D4: persist the lock and stay locked; fail-closed and fatal if it cannot."""
        self.startup_branch = StartupBranch.STARTUP_CONFIRMED_REJECTED
        try:
            self._identity_repository.mark_reactivation_required()
        except (RepositoryError, sqlite3.Error) as exc:
            # D4: the confirmed rejection could not be persisted. Never continue as Operational; set
            # a fail-closed in-memory lock and fail startup loudly. No secret/body in the message.
            self._coordinator = self._locked_coordinator()
            _LOGGER.error("agent_startup_lock_persistence_failed")
            raise AgentStartupError(
                "a confirmed credential rejection was received at startup but persisting the "
                "reactivation-required lock failed; the Agent will not operate"
            ) from exc

        # D3: lock persisted. Stay locked; no components, no monitor, no activation.
        self._coordinator = self._locked_coordinator()
        _LOGGER.warning("agent_startup_locked_after_confirmed_rejection")

    def _enter_locked(self, branch: StartupBranch) -> None:
        """Branch C: construct a locked coordinator and stay alive with nothing running."""
        self.startup_branch = branch
        self._coordinator = self._locked_coordinator()
        _LOGGER.warning("agent_startup_locked_reactivation_required")

    async def _enter_operational(self, identity: DeviceIdentity) -> None:
        """Approved Operational start order: coordinator → components → exactly one monitor task.

        Constructs the coordinator from the durable Operational state, starts the registered
        components (T-60 rolls back a partial failure and raises before the monitor starts), then
        starts the single validation-monitor task. Works with zero components.
        """
        self._coordinator = OperationalStateCoordinator.from_identity(
            identity,
            identity_repository=self._identity_repository,
            components=self._components,
        )
        await self._coordinator.start_operational_components()
        self._start_monitor()

    # --- Monitor task ---------------------------------------------------------------------------

    def _start_monitor(self) -> None:
        """Create exactly one owned monitor task (idempotent — never a duplicate)."""
        if self._monitor_task is not None:
            return
        monitor = self._monitor_factory()
        self._monitor_task = asyncio.create_task(
            self._run_monitor(monitor), name="credential-validation-monitor"
        )
        _LOGGER.info("agent_validation_monitor_started")

    async def _run_monitor(self, monitor: _MonitorRunnable) -> None:
        """Run the monitor loop, then react to its terminal result or a fatal error.

        A ``REACTIVATION_REQUIRED`` result locks the runtime and stops components through the
        coordinator (no repository re-write — T-59 already persisted). ``asyncio.CancelledError``
        (normal shutdown) propagates untouched. Any other exception is a fatal monitor failure,
        handled fail-closed. The monitor is never restarted.
        """
        try:
            result = await monitor.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._on_monitor_fatal(exc)
            return

        try:
            await self._coordinator_handle(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._on_monitor_fatal(exc)

    async def _coordinator_handle(self, result: CredentialValidationLoopResult) -> None:
        if self._coordinator is None:  # pragma: no cover - a monitor only runs once operational
            return
        _LOGGER.warning("agent_validation_monitor_reactivation_required")
        await self._coordinator.handle_validation_loop_result(result)

    async def _on_monitor_fatal(self, exc: BaseException) -> None:
        """Record a safe fatal error and drive the runtime fail-closed (lock + stop components).

        The runtime does not silently continue operational processing after the monitor fatally
        terminates. The raw cause is never logged or chained; only its type name (a safe identifier)
        is recorded. The monitor is not restarted and no activation is attempted.
        """
        self.fatal_error = AgentRuntimeFatalError(
            "the credential-validation monitor terminated fatally; the Agent is fail-closed"
        )
        _LOGGER.error("agent_validation_monitor_fatal", extra={"error_type": type(exc).__name__})
        if self._coordinator is not None:
            try:
                await self._coordinator.enter_reactivation_required()
            except asyncio.CancelledError:
                raise
            except OperationalComponentShutdownError:
                _LOGGER.error("agent_fail_closed_component_stop_incomplete")

    # --- Shutdown -------------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Cancel the monitor task, stop components, and close the owned validation client.

        Idempotent: a second call is a safe no-op. The monitor task's own cancellation is expected
        and suppressed; components are stopped through the coordinator (in-memory only — no
        persistence, so durable ``OperationalState`` is untouched and the next start re-evaluates
        the branches). The owned validation client is closed exactly once. Safe when already locked.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True

        task = self._monitor_task
        self._monitor_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected: our own shutdown cancellation of an owned task
            except Exception:
                # _run_monitor converts real failures to fail-closed and returns, so this is only a
                # defensive guard; record it safely rather than swallow it silently.
                _LOGGER.error("agent_shutdown_monitor_unexpected_error")

        if self._coordinator is not None:
            try:
                await self._coordinator.enter_reactivation_required()
            except asyncio.CancelledError:
                raise
            except OperationalComponentShutdownError:
                _LOGGER.warning("agent_shutdown_component_stop_incomplete")

        if self._owns_validation_client and not self._client_closed:
            self._client_closed = True
            await self._validation_client.aclose()

        _LOGGER.info("agent_supervisor_shutdown_complete")

    # --- Helpers --------------------------------------------------------------------------------

    def _load_identity(self) -> DeviceIdentity | None:
        try:
            return self._identity_repository.load()
        except (RepositoryError, sqlite3.Error) as exc:
            # A local persistence failure is fatal and is NOT treated as an offline Backend.
            _LOGGER.error("agent_startup_identity_load_failed")
            raise AgentStartupError(
                "the local device identity could not be loaded; startup cannot proceed"
            ) from exc

    def _locked_coordinator(self) -> OperationalStateCoordinator:
        return OperationalStateCoordinator(
            identity_repository=self._identity_repository,
            components=self._components,
            initial_state=OperationalState.REACTIVATION_REQUIRED,
        )

    def _default_monitor_factory(self) -> _MonitorRunnable:
        return CredentialValidationMonitor(
            identity_repository=self._identity_repository,
            validation_client=self._validation_client,
            interval_seconds=self._settings.credential_validation_interval_seconds,
        )
