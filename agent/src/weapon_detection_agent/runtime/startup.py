"""FastAPI lifespan startup/shutdown for the Jetson Agent (IP-02 T-39; IP-05 T-61, §5, §12).

This module composes the T-32–T-38 foundations and then hands the startup *decision* to the T-61
:class:`AgentRuntimeSupervisor`, which owns the approved decision tree (first activation / manual
reactivation / locked / validate-then-operate) and the running validation-monitor task. The order of
the foundation steps is fixed (§12.1, logging after the layout exists so the log directory is
present):

    load settings → resolve paths → provision layout → configure logging → initialize SQLite schema
    → construct repositories → construct the activation Backend client + validation client
    → construct the resolver + activation service + supervisor → run supervisor startup
    → load ConfigCache (may be empty, OI-2) → publish app.state.runtime → serve

The runtime is published **only** after the supervisor's startup succeeds. On any startup failure
the application does not serve: the supervisor is shut down (cancelling any monitor task, closing
the validation client it owns) and the owned activation Backend client is closed, so no resource
leaks and no half-published runtime is left. The FastAPI process is able to stay alive in the locked
branches (C / D3) — a locked startup is a normal, non-failing outcome, not an exception.

Shutdown drives the supervisor's shutdown (cancel + await the monitor task, stop operational
components, close the owned validation client), then closes the owned Backend client and clears the
runtime reference. It contacts no Backend and deletes no local state (§12.4).

Dependencies are injected through :func:`create_lifespan` (a settings loader, a clock, a Backend
client factory, and a validation client factory) so tests substitute fakes without a real Backend,
network, or ``/opt``. This module adds no HTTP route and starts no DeepStream/detection component.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import SecretStr

from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.service import ActivationService
from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.config.settings import AgentSettings, load_settings
from weapon_detection_agent.logging.configuration import configure_logging
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.schema import initialize_database
from weapon_detection_agent.runtime.state import RUNTIME_STATE_ATTR, AgentRuntime
from weapon_detection_agent.runtime.supervisor import AgentRuntimeSupervisor
from weapon_detection_agent.validation.client import CredentialValidationClient

_LOGGER = logging.getLogger("weapon_detection_agent.runtime.startup")

SettingsLoader = Callable[[], AgentSettings]
Clock = Callable[[], datetime]
BackendClientFactory = Callable[[AgentSettings], BackendActivationClient]
ValidationClientFactory = Callable[[AgentSettings], CredentialValidationClient]


def default_clock() -> datetime:
    """The default activation clock — the current time, timezone-aware in UTC."""
    return datetime.now(timezone.utc)


def default_backend_client_factory(settings: AgentSettings) -> BackendActivationClient:
    """Build the real Backend activation client from the settings (owned by the lifespan)."""
    return BackendActivationClient(
        settings.backend_base_url, timeout_seconds=settings.http_timeout_seconds
    )


def default_validation_client_factory(settings: AgentSettings) -> CredentialValidationClient:
    """Build the real credential-validation client (T-57) — ownership passes to the supervisor.

    It uses ``http_timeout_seconds`` as the per-request timeout (never the validation interval); the
    monitor's cadence uses ``credential_validation_interval_seconds`` instead.
    """
    return CredentialValidationClient(
        settings.backend_base_url, timeout_seconds=settings.http_timeout_seconds
    )


@dataclass
class _StartedRuntime:
    """The owned resources returned by :func:`_start` so :func:`_shutdown` can dispose of them."""

    backend_client: BackendActivationClient
    supervisor: AgentRuntimeSupervisor


def create_lifespan(
    *,
    settings_loader: SettingsLoader = load_settings,
    clock: Clock = default_clock,
    backend_client_factory: BackendClientFactory = default_backend_client_factory,
    validation_client_factory: ValidationClientFactory = default_validation_client_factory,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build the FastAPI lifespan context manager with injectable dependencies.

    The defaults are the real components; tests pass a settings loader, a fixed clock, a fake
    Backend client factory, and a fake validation client factory to exercise every branch without a
    real Backend, network, or ``/opt`` root.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        started = await _start(
            app,
            settings_loader=settings_loader,
            clock=clock,
            backend_client_factory=backend_client_factory,
            validation_client_factory=validation_client_factory,
        )
        try:
            yield
        finally:
            await _shutdown(app, started)

    return lifespan


async def _start(
    app: FastAPI,
    *,
    settings_loader: SettingsLoader,
    clock: Clock,
    backend_client_factory: BackendClientFactory,
    validation_client_factory: ValidationClientFactory,
) -> _StartedRuntime:
    """Run the foundation steps, drive the supervisor's startup, and publish the runtime.

    Foundation failures (settings, provisioning, logging, schema) propagate their own safe errors —
    no client exists yet to close. Once the clients and supervisor exist, any startup failure shuts
    the supervisor down (closing the validation client it owns) and closes the Backend client before
    re-raising, so no owned resource leaks.
    """
    settings = settings_loader()

    paths = resolve_paths(settings.root_path)
    paths.provision()

    configure_logging(
        log_level=settings.log_level,
        log_file=paths.log_file,
        sensitive_values=_sensitive_values(settings),
    )
    _LOGGER.info("agent_startup_begin")

    initialize_database(paths.database_file)
    identity_repository = DeviceIdentityRepository(paths.database_file)
    config_cache_repository = ConfigCacheRepository(paths.database_file)

    backend_client = backend_client_factory(settings)
    supervisor: AgentRuntimeSupervisor | None = None
    validation_client: CredentialValidationClient | None = None
    try:
        validation_client = validation_client_factory(settings)
        resolver = ActivationKeyResolver(
            environment_key=settings.activation_key,
            key_file_path=paths.activation_key_file,
        )
        service = ActivationService(
            backend_client=backend_client,
            identity_repository=identity_repository,
            key_resolver=resolver,
            clock=clock,
        )
        supervisor = AgentRuntimeSupervisor(
            settings=settings,
            identity_repository=identity_repository,
            key_resolver=resolver,
            activation_service=service,
            validation_client=validation_client,
            components=(),  # no operational components exist yet (T-62+)
        )
        await supervisor.startup()

        # §12.1: load the cached configuration (normally empty this milestone, OI-2).
        cached = config_cache_repository.load()
        _LOGGER.info(
            "agent_config_cache_loaded" if cached is not None else "agent_config_cache_absent"
        )

        runtime = AgentRuntime(
            settings=settings,
            paths=paths,
            identity_repository=identity_repository,
            config_cache_repository=config_cache_repository,
            activation=supervisor.activation_result,
            supervisor=supervisor,
        )
    except BaseException:
        _LOGGER.error("agent_startup_failed")
        if supervisor is not None:
            await supervisor.shutdown()
        elif validation_client is not None:
            await validation_client.aclose()
        await backend_client.aclose()
        raise

    setattr(app.state, RUNTIME_STATE_ATTR, runtime)
    _LOGGER.info(
        "agent_startup_complete",
        extra={"branch": supervisor.startup_branch.value if supervisor.startup_branch else None},
    )
    return _StartedRuntime(backend_client=backend_client, supervisor=supervisor)


async def _shutdown(app: FastAPI, started: _StartedRuntime) -> None:
    """Shut the supervisor down, close the owned Backend client, and clear the runtime (§12.4).

    Idempotent and never raises: the supervisor's shutdown is idempotent, closing an already-closed
    client is a no-op, and a cleanup error is logged safely rather than propagated. Contacts no
    Backend and deletes no local state.
    """
    try:
        await started.supervisor.shutdown()
    except Exception:
        _LOGGER.warning("agent_shutdown_supervisor_failed")
    try:
        await started.backend_client.aclose()
    except Exception:
        _LOGGER.warning("agent_shutdown_client_close_failed")
    setattr(app.state, RUNTIME_STATE_ATTR, None)
    _LOGGER.info("agent_shutdown_complete")


def _sensitive_values(settings: AgentSettings) -> list[SecretStr]:
    """The secret literals to scrub from logs — the environment Activation Key when present."""
    if settings.activation_key is not None:
        return [settings.activation_key]
    return []
