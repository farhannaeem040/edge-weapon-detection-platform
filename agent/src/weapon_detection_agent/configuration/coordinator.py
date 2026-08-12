"""Device Camera configuration lifecycle coordinator (FS-11 §5/§9, IP-13 T-233).

``DeviceConfigurationCoordinator`` implements the existing ``OperationalComponent`` protocol (IP-05
T-60) unchanged, the same way
:class:`~weapon_detection_agent.sync.worker.DetectionEventSyncWorker` does. When
``settings.device_config_enabled`` is ``True`` this coordinator *replaces* the static
``DeepStreamProcessManager`` component in the Operational component list — it owns starting,
stopping, and restarting the Bridge itself, driven by the Backend's Camera configuration, rather
than DeepStream being started once at startup from a static file (FS-11 §11).

**Lifecycle (FS-11 §5).**

1. On :meth:`start`, load the last-known-good configuration from ``ConfigCache`` (if any) and, if it
   validates, apply it — generate the runtime config and start the Bridge — *before* the first
   Backend round trip, so a restart during a Backend outage recovers instantly.
2. Then poll the Backend every ``device_config_refresh_seconds``. A successful, valid, *changed*
   configuration is persisted and applied (Bridge restarted exactly once); an unchanged version is a
   no-op; any failure (unreachable, 401, 503, invalid) leaves whatever is currently applied running
   untouched and backs off exponentially (mirroring
   :class:`~weapon_detection_agent.sync.worker.DetectionEventSyncWorker`'s own backoff shape).
3. No cache and the first fetch fails: the Bridge is never started with an invented source — this
   coordinator logs a ``device_config_waiting`` state and keeps retrying.

**Race safety (FS-11 §9).** A restart always fully stops (and reaps) the previous Bridge process
before the new :class:`~weapon_detection_agent.configuration.source_mapping.SourceGeneration` is
installed and the new Bridge is started — so a detection is never interpreted against the wrong
generation's mapping, because only one Bridge process is ever alive at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from weapon_detection_agent.configuration.client import DeviceConfigurationClient
from weapon_detection_agent.configuration.models import (
    ConfigurationFetchFailure,
    DeviceConfiguration,
)
from weapon_detection_agent.configuration.serialization import (
    deserialize_configuration,
    serialize_configuration,
)
from weapon_detection_agent.configuration.source_mapping import SourceGenerationTracker
from weapon_detection_agent.configuration.validation import (
    ConfigurationValidationError,
    validate_configuration,
)
from weapon_detection_agent.deepstream.config_generator import generate_runtime_config
from weapon_detection_agent.persistence.models import OperationalState

if TYPE_CHECKING:
    from pathlib import Path

    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.deepstream.process_manager import DeepStreamProcessManager
    from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )

_LOGGER = logging.getLogger("weapon_detection_agent.configuration.coordinator")

Sleeper = Callable[[float], Awaitable[None]]
JitterSource = Callable[[], float]


class DeviceConfigurationCoordinator:
    """Fetches, validates, caches, and applies the authenticated Device's Camera configuration."""

    def __init__(
        self,
        *,
        client: DeviceConfigurationClient,
        cache_repository: ConfigCacheRepository,
        identity_repository: DeviceIdentityRepository,
        process_manager: DeepStreamProcessManager,
        template_config_path: Path,
        runtime_dir: Path,
        max_cameras: int,
        refresh_interval_seconds: float,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        sleeper: Sleeper = asyncio.sleep,
        jitter: JitterSource = random.random,
        serializer: Callable[[DeviceConfiguration], str] | None = None,
        deserializer: Callable[[str], DeviceConfiguration] | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        if initial_backoff_seconds <= 0:
            raise ValueError("initial_backoff_seconds must be positive")
        if max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= initial_backoff_seconds")

        self._client = client
        self._cache_repository = cache_repository
        self._identity_repository = identity_repository
        self._process_manager = process_manager
        self._template_config_path = template_config_path
        self._runtime_dir = runtime_dir
        self._max_cameras = max_cameras
        self._refresh_interval = refresh_interval_seconds
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleeper
        self._jitter = jitter
        self._serialize = serializer or serialize_configuration
        self._deserialize = deserializer or deserialize_configuration

        self._tracker = SourceGenerationTracker()
        self._applied: DeviceConfiguration | None = None
        self._bridge_running = False
        self._task: asyncio.Task[None] | None = None
        self._backoff_attempt = 0

    # --- OperationalComponent protocol (IP-05 T-60) ---------------------------------------------

    @property
    def name(self) -> str:
        return "device-configuration"

    async def start(self) -> None:
        """Apply any last-known-good cache immediately, then launch the poll loop."""
        if self._task is not None:
            return

        identity = self._identity_repository.load()
        if identity is not None and identity.operational_state is OperationalState.OPERATIONAL:
            cached = await self._load_and_apply_cache(UUID(identity.device_id))
            if cached:
                _LOGGER.info("device_config_applied_from_cache", extra={"component": self.name})
            else:
                _LOGGER.info("device_config_waiting", extra={"component": self.name})

        self._backoff_attempt = 0
        self._task = asyncio.create_task(self._run(), name="device-configuration")
        _LOGGER.info("device_configuration_coordinator_started", extra={"component": self.name})

    async def stop(self) -> None:
        """Cancel the poll loop, await it, stop the Bridge if running, close the owned client."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._bridge_running:
            await self._process_manager.stop()
            self._bridge_running = False
        await self._client.aclose()
        _LOGGER.info("device_configuration_coordinator_stopped", extra={"component": self.name})

    # --- Startup cache application (FS-11 §5 step 1/2) -------------------------------------------

    async def _load_and_apply_cache(self, expected_device_id: UUID) -> bool:
        cached = self._cache_repository.load()
        if cached is None:
            return False

        try:
            configuration = self._deserialize(cached.config_json)
            validate_configuration(
                configuration, expected_device_id=expected_device_id, max_cameras=self._max_cameras
            )
        except (ConfigurationValidationError, ValueError) as exc:
            reason = getattr(exc, "reason", "malformed_cache")
            _LOGGER.warning(
                "device_config_cache_invalid", extra={"component": self.name, "reason": reason}
            )
            return False

        self._apply(configuration)
        if configuration.cameras:
            await self._process_manager.start()
            self._bridge_running = True
        return True

    # --- Poll loop (FS-11 §5 steps 3-9) ----------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                changed_or_idle = await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "device_configuration_coordinator_iteration_failed",
                    extra={"component": self.name},
                )
                await self._sleep_backoff()
                continue

            if changed_or_idle:
                self._reset_backoff()
                await self._sleep(self._refresh_interval)
            else:
                await self._sleep_backoff()

    async def _poll_once(self) -> bool:
        """Run one fetch/validate/apply cycle. Returns ``True`` if the caller should idle-sleep."""
        identity = self._identity_repository.load()
        if identity is None or identity.operational_state is not OperationalState.OPERATIONAL:
            # Not Operational right now — nothing to fetch with. Idle; the
            # OperationalStateCoordinator will stop this component shortly if the Agent is
            # genuinely locked (same defense-in-depth as DetectionEventSyncWorker).
            return True

        assert identity.shared_secret is not None  # noqa: S101 — OPERATIONAL implies a usable secret
        result = await self._client.fetch(identity.device_id, identity.shared_secret)

        if isinstance(result, ConfigurationFetchFailure):
            _LOGGER.warning(
                "device_config_fetch_unavailable",
                extra={
                    "component": self.name,
                    "reason": result.reason.value,
                    "status": result.status_code,
                },
            )
            return False

        try:
            validate_configuration(
                result,
                expected_device_id=UUID(identity.device_id),
                max_cameras=self._max_cameras,
            )
        except ConfigurationValidationError as exc:
            # FS-11 §5 step 7: invalid new configuration keeps the previous last-known-good running.
            _LOGGER.warning(
                "device_config_validation_failed",
                extra={"component": self.name, "reason": exc.reason},
            )
            return False

        unchanged = (
            self._applied is not None
            and self._applied.configuration_version == result.configuration_version
        )
        if unchanged:
            return True  # no-op, no restart

        self._cache_repository.save(self._serialize(result), updated_at=result.generated_at_utc)
        await self._apply_async(result)
        return True

    # --- Apply (FS-11 §7/§8/§9) ------------------------------------------------------------------

    def _apply(self, configuration: DeviceConfiguration) -> None:
        """Synchronous half of apply — generates the runtime config and installs the new mapping.

        Used only from :meth:`start` (before the event loop's first await, so no Bridge is running
        yet to stop). The poll loop uses :meth:`_apply_async`, which stops the running Bridge first.
        """
        generate_runtime_config(
            template_path=self._template_config_path,
            runtime_dir=self._runtime_dir,
            cameras=configuration.cameras,
        )
        self._tracker.install(configuration)
        self._applied = configuration

    async def _apply_async(self, configuration: DeviceConfiguration) -> None:
        # FS-11 §9: stop old Bridge -> wait for exit -> atomically install new config/mapping ->
        # start new Bridge -> accept detections for the new generation.
        if self._bridge_running:
            await self._process_manager.stop()
            self._bridge_running = False

        self._apply(configuration)

        if configuration.cameras:
            await self._process_manager.start()
            self._bridge_running = True
            _LOGGER.info(
                "device_config_applied",
                extra={
                    "component": self.name,
                    "camera_count": len(configuration.cameras),
                    "configuration_version": configuration.configuration_version,
                },
            )
        else:
            # Zero enabled Cameras is a valid configuration (FS-11 §16 item 13) — there is simply
            # nothing to build a pipeline from, so the Bridge stays stopped.
            _LOGGER.info(
                "device_config_applied_with_no_enabled_cameras", extra={"component": self.name}
            )

    # --- Backoff (mirrors DetectionEventSyncWorker) ----------------------------------------------

    def _reset_backoff(self) -> None:
        self._backoff_attempt = 0

    async def _sleep_backoff(self) -> None:
        delay = min(self._initial_backoff * (2**self._backoff_attempt), self._max_backoff)
        self._backoff_attempt += 1
        jittered = delay * (0.5 + self._jitter() * 0.5)
        await self._sleep(jittered)

    # --- source_id -> CameraId resolution (FS-11 §9, used by DetectionIngestHandler) -------------

    def resolve_camera_id(self, source_id: int) -> UUID | None:
        """Resolve ``source_id`` to an immutable ``Camera.CameraId`` in the current generation."""
        generation = self._tracker.current
        if generation is None:
            return None
        return generation.resolve(source_id)


def default_device_configuration_components_factory(
    settings: AgentSettings,
    paths: AgentPaths,
    identity_repository: DeviceIdentityRepository,
) -> tuple[DeviceConfigurationCoordinator, ...]:
    """Build the real ``DeviceConfigurationCoordinator`` from settings, if enabled (FS-11 §11).

    Returns an empty tuple when ``settings.device_config_enabled`` is ``False`` (the default) — no
    coordinator, client, or DeepStream process manager is constructed, and the pre-FS-11 static
    ``deepstream-app.txt``/``WDA_DETECTION_CAMERA_ID`` pipeline is left completely untouched.

    When enabled, this factory's ``DeepStreamProcessManager`` points at the *generated* runtime
    config path (``paths.runtime_dir / deepstream.generated.conf``), never the static template —
    the coordinator itself regenerates that file from the template before every start/restart.
    """
    if not settings.device_config_enabled:
        return ()

    from weapon_detection_agent.deepstream.config_generator import GENERATED_CONFIG_FILENAME
    from weapon_detection_agent.deepstream.process_manager import DeepStreamProcessManager
    from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository

    client = DeviceConfigurationClient(
        settings.backend_base_url,
        timeout_seconds=settings.http_timeout_seconds,
        max_cameras=settings.device_config_max_cameras,
    )
    process_manager = DeepStreamProcessManager(
        executable_path=settings.deepstream_executable_path,
        config_path=paths.runtime_dir / GENERATED_CONFIG_FILENAME,
        working_directory=settings.deepstream_working_directory,
        stop_timeout_seconds=settings.deepstream_stop_timeout_seconds,
        restart_policy=settings.deepstream_restart_policy,
        log_path=settings.deepstream_log_path,
    )

    return (
        DeviceConfigurationCoordinator(
            client=client,
            cache_repository=ConfigCacheRepository(paths.database_file),
            identity_repository=identity_repository,
            process_manager=process_manager,
            template_config_path=settings.deepstream_config_path,
            runtime_dir=paths.runtime_dir,
            max_cameras=settings.device_config_max_cameras,
            refresh_interval_seconds=settings.device_config_refresh_seconds,
        ),
    )
