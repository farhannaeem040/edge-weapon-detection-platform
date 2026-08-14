"""Unit tests for the Device configuration lifecycle coordinator (FS-11 §5/§9, IP-13 T-233).

Mirrors ``test_detection_event_sync_worker.py``'s style: fakes for every dependency (client, cache
repository, identity repository, DeepStream process manager), internal methods (``_poll_once``,
``_load_and_apply_cache``) exercised directly via ``asyncio.run`` for the same reason that file
does — deterministic, no real asyncio.sleep, no real subprocess, no real network/SQLite.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from weapon_detection_agent.configuration.coordinator import DeviceConfigurationCoordinator
from weapon_detection_agent.configuration.models import (
    ConfigurationFailureReason,
    ConfigurationFetchFailure,
    DeviceCameraConfig,
    DeviceConfiguration,
)
from weapon_detection_agent.configuration.serialization import serialize_configuration
from weapon_detection_agent.persistence.models import (
    CachedConfiguration,
    DeviceIdentity,
    OperationalState,
)

DEVICE_ID = UUID("965032b6-26af-4506-81f9-2e7307290fa1")
BRANCH_ID = uuid4()
CAMERA_A = uuid4()
CAMERA_B = uuid4()
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
GENERATED_AT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
TEMPLATE_PATH = "/opt/weapon-detection/config/deepstream/deepstream-app.txt"
RUNTIME_DIR = "/opt/weapon-detection/runtime"


def _configuration(version: str, cameras: tuple[DeviceCameraConfig, ...]) -> DeviceConfiguration:
    return DeviceConfiguration(
        schema_version=1,
        configuration_version=version,
        device_id=DEVICE_ID,
        branch_id=BRANCH_ID,
        generated_at_utc=GENERATED_AT,
        cameras=cameras,
    )


def _camera(camera_id: UUID, source_order: int) -> DeviceCameraConfig:
    return DeviceCameraConfig(
        camera_id=camera_id,
        camera_key=str(camera_id),
        name=f"Camera {source_order}",
        stream_url=f"rtsp://camera.example.invalid:554/s{source_order}",
        enabled=True,
        source_order=source_order,
        output_path=f"cameras/{camera_id}",
    )


class _FakeIdentityRepository:
    def __init__(self, identity: DeviceIdentity | None) -> None:
        self.identity = identity

    def load(self) -> DeviceIdentity | None:
        return self.identity


class _FakeCacheRepository:
    def __init__(self, cached: CachedConfiguration | None = None) -> None:
        self._cached = cached
        self.save_calls: list[tuple[str, datetime]] = []

    def load(self) -> CachedConfiguration | None:
        return self._cached

    def save(self, config_json: str, *, updated_at: datetime) -> None:
        self.save_calls.append((config_json, updated_at))
        self._cached = CachedConfiguration(config_json=config_json, updated_at=updated_at)


class _FakeClient:
    def __init__(self, results: list[DeviceConfiguration | ConfigurationFetchFailure]) -> None:
        self._results = list(results)
        self.fetch_calls = 0
        self.closed = False

    async def fetch(
        self, device_id: str, shared_secret: SecretStr
    ) -> DeviceConfiguration | ConfigurationFetchFailure:
        self.fetch_calls += 1
        if not self._results:
            raise AssertionError("fetch called more times than scripted")
        return self._results.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _FakeProcessManager:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.running = False

    async def start(self) -> None:
        self.start_calls += 1
        self.running = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self.running = False


def _identity(operational: bool = True) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=str(DEVICE_ID),
        shared_secret=SecretStr("fake-secret-ZZZ") if operational else None,
        activated_at=ACTIVATED_AT,
        last_activated_at=ACTIVATED_AT,
        operational_state=(
            OperationalState.OPERATIONAL if operational else OperationalState.REACTIVATION_REQUIRED
        ),
    )


def _coordinator(
    *,
    client: _FakeClient,
    cache_repository: _FakeCacheRepository,
    identity_repository: _FakeIdentityRepository,
    process_manager: _FakeProcessManager,
    max_cameras: int = 8,
) -> DeviceConfigurationCoordinator:
    return DeviceConfigurationCoordinator(
        client=client,  # type: ignore[arg-type]
        cache_repository=cache_repository,  # type: ignore[arg-type]
        identity_repository=identity_repository,  # type: ignore[arg-type]
        process_manager=process_manager,  # type: ignore[arg-type]
        template_config_path=TEMPLATE_PATH,  # type: ignore[arg-type]
        runtime_dir=RUNTIME_DIR,  # type: ignore[arg-type]
        max_cameras=max_cameras,
        refresh_interval_seconds=1.0,
    )


# --- _poll_once: identity not operational --------------------------------------------------------


def test_poll_once_not_operational_idles_without_fetching() -> None:
    client = _FakeClient([])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity(operational=False)),
        process_manager=_FakeProcessManager(),
    )

    idle = asyncio.run(coordinator._poll_once())

    assert idle is True
    assert client.fetch_calls == 0


# --- _poll_once: first successful fetch applies and starts the Bridge ---------------------------


def test_poll_once_first_valid_configuration_applies_and_starts_bridge(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    process_manager = _FakeProcessManager()
    config = _configuration("v1", (_camera(CAMERA_A, 0),))
    client = _FakeClient([config])
    cache = _FakeCacheRepository()
    coordinator = _coordinator(
        client=client,
        cache_repository=cache,
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    idle = asyncio.run(coordinator._poll_once())

    assert idle is True
    assert process_manager.start_calls == 1
    assert process_manager.stop_calls == 0
    assert len(cache.save_calls) == 1
    assert coordinator.resolve_camera_id(0) == CAMERA_A


# --- _poll_once: unchanged version is a no-op ----------------------------------------------------


def test_poll_once_unchanged_version_does_not_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    config = _configuration("same-version", (_camera(CAMERA_A, 0),))
    process_manager = _FakeProcessManager()
    client = _FakeClient([config, config])
    cache = _FakeCacheRepository()
    coordinator = _coordinator(
        client=client,
        cache_repository=cache,
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    asyncio.run(coordinator._poll_once())
    asyncio.run(coordinator._poll_once())

    assert process_manager.start_calls == 1  # only the first apply started it
    assert process_manager.stop_calls == 0
    assert len(cache.save_calls) == 1  # second, unchanged fetch never re-saved


# --- _poll_once: changed version restarts exactly once -------------------------------------------


def test_poll_once_changed_version_restarts_bridge_exactly_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    first = _configuration("v1", (_camera(CAMERA_A, 0),))
    second = _configuration("v2", (_camera(CAMERA_B, 0),))
    process_manager = _FakeProcessManager()
    client = _FakeClient([first, second])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    asyncio.run(coordinator._poll_once())
    asyncio.run(coordinator._poll_once())

    assert process_manager.start_calls == 2
    assert process_manager.stop_calls == 1  # stopped exactly once, before the second start
    # FS-11 §9: the new generation maps source 0 to the NEW camera, never a stale one.
    assert coordinator.resolve_camera_id(0) == CAMERA_B


# --- _poll_once: name-only change (same version) never restarts ---------------------------------


def test_poll_once_name_only_change_keeps_same_version_no_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    # Same configuration_version (as the Backend would compute — Name excluded from the hash),
    # just a different Name string on the same camera.
    original = _configuration("v1", (_camera(CAMERA_A, 0),))
    renamed_camera = DeviceCameraConfig(
        camera_id=CAMERA_A,
        camera_key=str(CAMERA_A),
        name="Renamed",
        stream_url=original.cameras[0].stream_url,
        enabled=True,
        source_order=0,
        # The output mount is an identity, not a label — a rename never moves it (FS-11 §11).
        output_path=original.cameras[0].output_path,
    )
    renamed = _configuration("v1", (renamed_camera,))
    process_manager = _FakeProcessManager()
    client = _FakeClient([original, renamed])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    asyncio.run(coordinator._poll_once())
    asyncio.run(coordinator._poll_once())

    assert process_manager.start_calls == 1
    assert process_manager.stop_calls == 0


# --- _poll_once: invalid new configuration keeps the previous one running -----------------------


def test_poll_once_invalid_configuration_keeps_previous_running(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    valid = _configuration("v1", (_camera(CAMERA_A, 0),))
    # Invalid: DeviceId does not match the Agent's own identity.
    invalid = DeviceConfiguration(
        schema_version=1,
        configuration_version="v2",
        device_id=uuid4(),
        branch_id=BRANCH_ID,
        generated_at_utc=GENERATED_AT,
        cameras=(_camera(CAMERA_B, 0),),
    )
    process_manager = _FakeProcessManager()
    client = _FakeClient([valid, invalid])
    cache = _FakeCacheRepository()
    coordinator = _coordinator(
        client=client,
        cache_repository=cache,
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    asyncio.run(coordinator._poll_once())
    idle = asyncio.run(coordinator._poll_once())

    assert idle is False  # backoff, not idle-sleep
    assert process_manager.start_calls == 1
    assert process_manager.stop_calls == 0
    assert len(cache.save_calls) == 1  # the invalid one was never persisted
    assert coordinator.resolve_camera_id(0) == CAMERA_A  # still the original mapping


# --- _poll_once: fetch failure keeps the previous configuration running -------------------------


def test_poll_once_fetch_failure_keeps_previous_running(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    valid = _configuration("v1", (_camera(CAMERA_A, 0),))
    failure = ConfigurationFetchFailure(ConfigurationFailureReason.UNAVAILABLE, 503)
    process_manager = _FakeProcessManager()
    client = _FakeClient([valid, failure])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    asyncio.run(coordinator._poll_once())
    idle = asyncio.run(coordinator._poll_once())

    assert idle is False
    assert process_manager.start_calls == 1
    assert process_manager.stop_calls == 0


def test_poll_once_401_never_touches_identity_repository(monkeypatch, tmp_path) -> None:
    # The identity repository fake exposes no write method at all — if the coordinator ever tried
    # to rotate/clear credentials on a 401, this test would fail with an AttributeError.
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    failure = ConfigurationFetchFailure(ConfigurationFailureReason.UNAUTHORIZED, 401)
    client = _FakeClient([failure])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=_FakeProcessManager(),
    )

    idle = asyncio.run(coordinator._poll_once())

    assert idle is False


# --- resolve_camera_id: unknown source_id --------------------------------------------------------


def test_resolve_camera_id_before_any_apply_returns_none() -> None:
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=_FakeProcessManager(),
    )

    assert coordinator.resolve_camera_id(0) is None


def test_resolve_camera_id_unknown_source_id_returns_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    config = _configuration("v1", (_camera(CAMERA_A, 0),))
    client = _FakeClient([config])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=_FakeProcessManager(),
    )

    asyncio.run(coordinator._poll_once())

    assert coordinator.resolve_camera_id(99) is None


# --- _load_and_apply_cache: startup cache application (FS-11 §5 step 1/2) -----------------------


def test_load_and_apply_cache_applies_a_valid_cached_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )
    config = _configuration("v1", (_camera(CAMERA_A, 0),))
    cached = CachedConfiguration(
        config_json=serialize_configuration(config), updated_at=GENERATED_AT
    )
    process_manager = _FakeProcessManager()
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(cached),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    applied = asyncio.run(coordinator._load_and_apply_cache(DEVICE_ID))

    assert applied is True
    assert process_manager.start_calls == 1
    assert coordinator.resolve_camera_id(0) == CAMERA_A


def test_load_and_apply_cache_no_cache_returns_false_starts_nothing() -> None:
    process_manager = _FakeProcessManager()
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(None),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    applied = asyncio.run(coordinator._load_and_apply_cache(DEVICE_ID))

    assert applied is False
    assert process_manager.start_calls == 0


def test_load_and_apply_cache_device_id_mismatch_is_rejected_not_applied() -> None:
    config = _configuration("v1", (_camera(CAMERA_A, 0),))
    cached = CachedConfiguration(
        config_json=serialize_configuration(config), updated_at=GENERATED_AT
    )
    process_manager = _FakeProcessManager()
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(cached),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    # a different Device's identity
    applied = asyncio.run(coordinator._load_and_apply_cache(uuid4()))

    assert applied is False
    assert process_manager.start_calls == 0


def test_load_and_apply_cache_malformed_json_is_rejected_not_applied() -> None:
    cached = CachedConfiguration(config_json="not json", updated_at=GENERATED_AT)
    process_manager = _FakeProcessManager()
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(cached),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )

    applied = asyncio.run(coordinator._load_and_apply_cache(DEVICE_ID))

    assert applied is False
    assert process_manager.start_calls == 0


# --- start()/stop(): lifecycle ---------------------------------------------------------------


def test_start_with_no_cache_and_unreachable_backend_never_starts_bridge(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "weapon_detection_agent.configuration.coordinator.generate_runtime_config",
        lambda **kwargs: tmp_path / "deepstream.generated.conf",
    )

    async def _immediate_return(_delay: float) -> None:
        raise asyncio.CancelledError

    process_manager = _FakeProcessManager()
    failure = ConfigurationFetchFailure(ConfigurationFailureReason.TRANSPORT_FAILURE)
    client = _FakeClient([failure] * 5)
    coordinator = DeviceConfigurationCoordinator(
        client=client,  # type: ignore[arg-type]
        cache_repository=_FakeCacheRepository(None),  # type: ignore[arg-type]
        identity_repository=_FakeIdentityRepository(_identity()),  # type: ignore[arg-type]
        process_manager=process_manager,  # type: ignore[arg-type]
        template_config_path=TEMPLATE_PATH,  # type: ignore[arg-type]
        runtime_dir=RUNTIME_DIR,  # type: ignore[arg-type]
        max_cameras=8,
        refresh_interval_seconds=1.0,
        sleeper=_immediate_return,
    )

    asyncio.run(_start_then_stop_soon(coordinator))

    assert process_manager.start_calls == 0


async def _start_then_stop_soon(coordinator: DeviceConfigurationCoordinator) -> None:
    await coordinator.start()
    await asyncio.sleep(0.01)
    await coordinator.stop()


def test_stop_is_idempotent_and_closes_the_client() -> None:
    client = _FakeClient([])
    coordinator = _coordinator(
        client=client,
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=_FakeProcessManager(),
    )

    asyncio.run(coordinator.stop())
    asyncio.run(coordinator.stop())  # second call must not raise

    assert client.closed is True


def test_stop_stops_a_running_bridge() -> None:
    process_manager = _FakeProcessManager()
    coordinator = _coordinator(
        client=_FakeClient([]),
        cache_repository=_FakeCacheRepository(),
        identity_repository=_FakeIdentityRepository(_identity()),
        process_manager=process_manager,
    )
    coordinator._bridge_running = True  # simulate an already-applied configuration

    asyncio.run(coordinator.stop())

    assert process_manager.stop_calls == 1


# --- constructor validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refresh_interval_seconds": 0},
        {"refresh_interval_seconds": -1},
        {"initial_backoff_seconds": 0},
        {"max_backoff_seconds": 0.5, "initial_backoff_seconds": 1.0},
    ],
)
def test_constructor_rejects_invalid_tuning_values(kwargs: dict[str, object]) -> None:
    base = {
        "client": _FakeClient([]),
        "cache_repository": _FakeCacheRepository(),
        "identity_repository": _FakeIdentityRepository(_identity()),
        "process_manager": _FakeProcessManager(),
        "template_config_path": TEMPLATE_PATH,
        "runtime_dir": RUNTIME_DIR,
        "max_cameras": 8,
        "refresh_interval_seconds": 1.0,
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        DeviceConfigurationCoordinator(**base)  # type: ignore[arg-type]
