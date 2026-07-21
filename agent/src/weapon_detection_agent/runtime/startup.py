"""FastAPI lifespan startup/shutdown for the Jetson Agent (IP-02 T-39, §5, §12).

This module composes the foundations built by T-32–T-38 into the §12.1 startup decision, run inside
the FastAPI **lifespan** — never at import time. The order is fixed (§12.1, with logging configured
after the layout exists so the log file's directory is present):

    load settings → resolve paths → provision layout → configure logging → initialize SQLite schema
    → construct repositories → construct the Backend client → construct the resolver + service
    → run activation (first / reactivate / no-op) → load ConfigCache (may be empty, OI-2)
    → publish app.state.runtime → serve

The runtime state is published **only** after every required step succeeds; on any failure the
application does not serve, an owned Backend client already created is closed, and no runtime state
is left half-published. The activation call happens exactly once and is never retried (IP-02 §14).

Shutdown closes the owned HTTP client and clears the runtime reference. It never contacts the
Backend, and never deletes the Device Identity, the ConfigCache, the database, the key file, logs,
or any directory (§12.4).

Dependencies are injected through :func:`create_lifespan` (a settings loader, a clock, and a Backend
client factory) so tests can substitute fakes without a real Backend, network, or ``/opt``. The
defaults are the real T-32/T-37 components. This module writes **no** ConfigCache, starts no
DeepStream/heartbeat/background task, and adds no HTTP route.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
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

_LOGGER = logging.getLogger("weapon_detection_agent.runtime.startup")

SettingsLoader = Callable[[], AgentSettings]
Clock = Callable[[], datetime]
BackendClientFactory = Callable[[AgentSettings], BackendActivationClient]


def default_clock() -> datetime:
    """The default activation clock — the current time, timezone-aware in UTC."""
    return datetime.now(timezone.utc)


def default_backend_client_factory(settings: AgentSettings) -> BackendActivationClient:
    """Build the real Backend activation client from the settings (owned by the runtime)."""
    return BackendActivationClient(
        settings.backend_base_url, timeout_seconds=settings.http_timeout_seconds
    )


def create_lifespan(
    *,
    settings_loader: SettingsLoader = load_settings,
    clock: Clock = default_clock,
    backend_client_factory: BackendClientFactory = default_backend_client_factory,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build the FastAPI lifespan context manager with injectable dependencies.

    The defaults are the real components; tests pass a settings loader, a fixed clock, and a fake
    Backend client factory to exercise startup without a real Backend, network, or ``/opt`` root.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = await _start(
            app,
            settings_loader=settings_loader,
            clock=clock,
            backend_client_factory=backend_client_factory,
        )
        try:
            yield
        finally:
            await _shutdown(app, client)

    return lifespan


async def _start(
    app: FastAPI,
    *,
    settings_loader: SettingsLoader,
    clock: Clock,
    backend_client_factory: BackendClientFactory,
) -> BackendActivationClient:
    """Run the §12.1 startup steps and publish the runtime; return the owned Backend client.

    Steps before the Backend client is created (settings, provisioning, logging, schema) fail by
    propagating their own clear, safe errors — no client exists to close. Once the client exists, an
    activation or persistence failure closes it before re-raising, so no owned resource leaks.
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

    client = backend_client_factory(settings)
    try:
        resolver = ActivationKeyResolver(
            environment_key=settings.activation_key,
            key_file_path=paths.activation_key_file,
        )
        service = ActivationService(
            backend_client=client,
            identity_repository=identity_repository,
            key_resolver=resolver,
            clock=clock,
        )
        activation = await service.activate()

        # §12.1: load the cached configuration (normally empty this milestone, OI-2) — the offline
        # startup structural guarantee. Nothing consumes it yet; it must not error when absent.
        cached = config_cache_repository.load()
        _LOGGER.info(
            "agent_config_cache_loaded" if cached is not None else "agent_config_cache_absent"
        )

        runtime = AgentRuntime(
            settings=settings,
            paths=paths,
            identity_repository=identity_repository,
            config_cache_repository=config_cache_repository,
            activation=activation,
        )
    except BaseException:
        _LOGGER.error("agent_startup_failed")
        await client.aclose()
        raise

    setattr(app.state, RUNTIME_STATE_ATTR, runtime)
    _LOGGER.info(
        "agent_startup_complete",
        extra={"outcome": activation.outcome.value, "device_id": activation.device_id},
    )
    return client


async def _shutdown(app: FastAPI, client: BackendActivationClient) -> None:
    """Close the owned Backend client and clear the runtime reference (§12.4).

    Idempotent and never raises: closing an already-closed client is a no-op, and a cleanup error is
    logged safely rather than propagated. Contacts no Backend and deletes no local state.
    """
    try:
        await client.aclose()
    except Exception:
        _LOGGER.warning("agent_shutdown_client_close_failed")
    setattr(app.state, RUNTIME_STATE_ATTR, None)
    _LOGGER.info("agent_shutdown_complete")


def _sensitive_values(settings: AgentSettings) -> list[SecretStr]:
    """The secret literals to scrub from logs — the environment Activation Key when present."""
    if settings.activation_key is not None:
        return [settings.activation_key]
    return []
