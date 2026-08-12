"""Tests for wiring ``DetectionIngestHandler``/``DeepStreamProcessManager`` into the Agent lifecycle
(IP-07 T-87, FS-05).

Three layers, matching the task's own breakdown:

* :func:`~weapon_detection_agent.detection.ingest_handler.default_detection_components_factory` in
  isolation — the feature-gate matrix, dependency wiring, the missing-identity failure, and the
  class-label file being read exactly once.
* :mod:`weapon_detection_agent.detection.class_labels` — the profile labelfile reader in isolation.
* ``main.py``'s own composed factory (:func:`weapon_detection_agent.main._components_factory`) — the
  ordering matrix, and (via a real :class:`OperationalStateCoordinator` with two fake components
  standing in for the ingest handler and DeepStream) the actual start/stop ordering, partial-startup
  rollback, and feature isolation this task requires.

No real DeepStream/pyds/GStreamer is used anywhere in this file — DeepStream is represented either
by its real, fake-subprocess-backed ``DeepStreamProcessManager`` (dependency-wiring assertions only)
or by a plain fake component (ordering/lifecycle assertions), exactly like the existing
``test_deepstream_process_manager.py``/``test_agent_runtime_supervisor.py`` suites already do.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.config.settings import ConfigurationError, load_settings
from weapon_detection_agent.deepstream.process_manager import DeepStreamProcessManager
from weapon_detection_agent.detection.class_labels import (
    load_class_names,
    resolve_class_labels_path,
)
from weapon_detection_agent.detection.cooldown import DetectionCooldownTracker
from weapon_detection_agent.detection.errors import (
    DetectionClassLabelsUnavailableError,
    DetectionDeviceIdentityUnavailableError,
)
from weapon_detection_agent.detection.ingest_handler import (
    DetectionIngestHandler,
    default_detection_components_factory,
    default_device_identity_provider,
)
from weapon_detection_agent.main import _components_factory
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.operational_state_coordinator import (
    OperationalComponentStartError,
    OperationalStateCoordinator,
)
from weapon_detection_agent.sync.worker import DetectionEventSyncWorker

# IP-07 T-90's incompatible-configuration preflight rejects detection_events_enabled=True
# combined with the default deepstream_executable_path — every fixture below that enables both
# switches must repoint the executable away from that default, matching a real Bridge deployment.
BRIDGE_RUN_SH_PATH = "/opt/weapon-detection/deepstream-bridge/run.sh"

DEVICE_ID = "device-wiring-11111111"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

requires_unix_sockets = pytest.mark.skipif(
    not hasattr(asyncio, "start_unix_server"),
    reason="asyncio Unix domain sockets are unavailable on this platform",
)


class FakeComponent:
    """A deterministic operational component recording start/stop order into a shared list."""

    def __init__(self, name: str, *, log: list[str], fail_start: bool = False) -> None:
        self.name = name
        self._log = log
        self._fail_start = fail_start
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> None:
        self.start_count += 1
        if self._fail_start:
            raise RuntimeError(f"{self.name} failed to start")
        self._log.append(f"start:{self.name}")

    async def stop(self) -> None:
        self.stop_count += 1
        self._log.append(f"stop:{self.name}")


def _ready_paths(tmp_path: Path):
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths


def _store_identity(repo: DeviceIdentityRepository, *, device_id: str = DEVICE_ID) -> None:
    repo.store(
        DeviceIdentity(
            device_id=device_id,
            shared_secret=SecretStr("placeholder-secret-never-real"),  # noqa: S106
            activated_at=T0,
            last_activated_at=T0,
        )
    )


# ==================================================================================================
# class_labels
# ==================================================================================================


def test_resolve_class_labels_path_is_pure_path_arithmetic(tmp_path: Path) -> None:
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=str(tmp_path / "weapon-detection"),
        deepstream_model_profile="yolov4-fp16",
    )

    path = resolve_class_labels_path(settings)

    expected = tmp_path / "weapon-detection" / "config" / "deepstream" / "profiles"
    expected = expected / "yolov4-fp16" / "labels.txt"
    assert path == expected
    assert not path.exists()  # resolving the path performs no I/O


def test_load_class_names_indexes_by_line_position(tmp_path: Path) -> None:
    labels_path = tmp_path / "labels.txt"
    labels_path.write_text("gun\nknife\n", encoding="utf-8")

    assert load_class_names(labels_path) == {0: "gun", 1: "knife"}


def test_load_class_names_ignores_only_trailing_blank_lines(tmp_path: Path) -> None:
    labels_path = tmp_path / "labels.txt"
    labels_path.write_text("gun\nknife\n\n\n", encoding="utf-8")

    assert load_class_names(labels_path) == {0: "gun", 1: "knife"}


def test_load_class_names_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DetectionClassLabelsUnavailableError):
        load_class_names(tmp_path / "does-not-exist" / "labels.txt")


def test_load_class_names_empty_file_raises(tmp_path: Path) -> None:
    labels_path = tmp_path / "labels.txt"
    labels_path.write_text("", encoding="utf-8")

    with pytest.raises(DetectionClassLabelsUnavailableError):
        load_class_names(labels_path)


# ==================================================================================================
# default_detection_components_factory — feature-gate matrix
# ==================================================================================================


def _settings_for(tmp_path: Path, **overrides: object):
    # detection_socket_path does NOT auto-follow root_path (IP-07 T-90: it is now the actual bind
    # path DetectionIngestHandler uses, wired independently per FS-05 §9) — default it to the
    # tmp_path-isolated runtime/detection.sock here so tests never accidentally target the real
    # production socket path, unless a test explicitly overrides it itself.
    defaults: dict[str, object] = {
        "backend_base_url": "http://backend.local:5230",
        "root_path": str(tmp_path / "weapon-detection"),
    }
    if "detection_socket_path" not in overrides:
        defaults["detection_socket_path"] = str(
            resolve_paths(tmp_path / "weapon-detection").detection_socket_file
        )
    defaults.update(overrides)
    return load_settings(**defaults)


def test_detection_factory_both_disabled_by_default_registers_nothing(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    assert settings.deepstream_enabled is False
    assert settings.detection_events_enabled is False
    assert default_detection_components_factory(settings, paths, identity_repository) == ()


def test_detection_factory_deepstream_only_registers_nothing(tmp_path: Path) -> None:
    """DeepStream enabled, detection events not — existing IP-06 behaviour only (FS-05 §4)."""
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path, deepstream_enabled=True)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    assert default_detection_components_factory(settings, paths, identity_repository) == ()


def test_detection_only_is_rejected_at_settings_construction(tmp_path: Path) -> None:
    """Detection events enabled, DeepStream not — FS-05 §4 requires *both*. IP-07 T-90's
    incompatible-configuration preflight now makes this combination impossible by construction
    (``ConfigurationError`` at settings load) rather than merely "constructs but registers
    nothing" — a stronger guarantee than the pre-T-90 behaviour this test used to check."""
    with pytest.raises(ConfigurationError):
        _settings_for(tmp_path, detection_events_enabled=True)


def test_detection_factory_both_enabled_builds_one_handler_with_correct_dependencies(
    tmp_path: Path,
) -> None:
    paths = _ready_paths(tmp_path)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16" / "labels.txt").write_text(
        "gun\nknife\n", encoding="utf-8"
    )
    settings = _settings_for(
        tmp_path,
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH_PATH,
        deepstream_model_profile="yolov4-fp16",
        detection_camera_id="camera-7",
        detection_min_confidence=0.75,
        detection_cooldown_seconds=12.0,
        detection_queue_capacity=42,
    )
    identity_repository = DeviceIdentityRepository(paths.database_file)
    _store_identity(identity_repository)

    components = default_detection_components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    handler = components[0]
    assert isinstance(handler, DetectionIngestHandler)
    assert handler.name == "detection-ingest"
    # Identity is never read at construction (IP-07 T-87 correction) — the cache is still empty.
    assert handler._device_id is None  # noqa: SLF001
    # Dependency wiring (IP-07 T-87 item 5/10) — private attributes are the most direct way to
    # assert exactly what the constructor received without re-deriving it independently.
    assert handler._socket_path == paths.detection_socket_file  # noqa: SLF001
    assert handler._queue_capacity == 42  # noqa: SLF001
    assert handler._device_id_provider() == DEVICE_ID  # noqa: SLF001
    assert handler._camera_id == "camera-7"  # noqa: SLF001
    assert handler._class_names == {0: "gun", 1: "knife"}  # noqa: SLF001
    assert handler._min_confidence == 0.75  # noqa: SLF001
    assert isinstance(handler._repository, DetectionEventRepository)  # noqa: SLF001


def test_detection_factory_construction_succeeds_with_no_identity_yet(tmp_path: Path) -> None:
    """IP-07 T-87 correction: a brand-new, unactivated device must still be able to construct the
    ingest handler — construction must never require a persisted identity to exist yet, so a fresh
    device gets the chance to activate at all."""
    paths = _ready_paths(tmp_path)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16" / "labels.txt").write_text(
        "gun\n", encoding="utf-8"
    )
    settings = _settings_for(
        tmp_path,
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH_PATH,
        deepstream_model_profile="yolov4-fp16",
    )
    identity_repository = DeviceIdentityRepository(paths.database_file)  # nothing stored yet

    components = default_detection_components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    assert isinstance(components[0], DetectionIngestHandler)


def test_device_identity_provider_raises_only_when_actually_called(tmp_path: Path) -> None:
    """The provider itself is a plain lazy callable: building it does no I/O, and it raises only
    when invoked with nothing persisted — never generating or fabricating a value instead."""
    paths = _ready_paths(tmp_path)
    identity_repository = DeviceIdentityRepository(paths.database_file)  # nothing stored

    provider = default_device_identity_provider(identity_repository)  # no I/O yet

    with pytest.raises(DetectionDeviceIdentityUnavailableError):
        provider()


@requires_unix_sockets
def test_ingest_handler_start_fails_when_identity_still_unavailable(tmp_path: Path) -> None:
    """IP-07 T-87 item 7: if start() (the real operational-start boundary) is reached and identity
    is still unavailable, it must fail loudly and bind nothing."""

    async def _scenario() -> None:
        paths = _ready_paths(tmp_path)
        identity_repository = DeviceIdentityRepository(paths.database_file)  # nothing stored
        handler = DetectionIngestHandler(
            socket_path=paths.detection_socket_file,
            queue_capacity=10,
            device_id_provider=default_device_identity_provider(identity_repository),
            camera_id="camera1",
            class_names={0: "gun"},
            min_confidence=0.5,
            cooldown_tracker=DetectionCooldownTracker(cooldown_seconds=5.0),
            repository=DetectionEventRepository(paths.database_file),
        )

        with pytest.raises(DetectionDeviceIdentityUnavailableError):
            await handler.start()

        assert not paths.detection_socket_file.exists()  # nothing was bound

    asyncio.run(_scenario())


@requires_unix_sockets
def test_ingest_handler_starts_using_identity_persisted_after_construction(tmp_path: Path) -> None:
    """The exact fixed scenario: construct while unactivated, activation persists identity
    afterwards, then start() must resolve and use that newly-persisted identity."""

    async def _scenario() -> None:
        paths = _ready_paths(tmp_path)
        identity_repository = DeviceIdentityRepository(paths.database_file)
        handler = DetectionIngestHandler(
            socket_path=paths.detection_socket_file,
            queue_capacity=10,
            device_id_provider=default_device_identity_provider(identity_repository),
            camera_id="camera1",
            class_names={0: "gun"},
            min_confidence=0.5,
            cooldown_tracker=DetectionCooldownTracker(cooldown_seconds=5.0),
            repository=DetectionEventRepository(paths.database_file),
        )

        # Simulates activation completing strictly after construction, before start().
        _store_identity(identity_repository)

        await handler.start()
        try:
            assert handler._device_id == DEVICE_ID  # noqa: SLF001
        finally:
            await handler.stop()

    asyncio.run(_scenario())


def test_identity_provider_is_called_at_most_once_per_handler(tmp_path: Path) -> None:
    """IP-07 T-87 item 12: identity is loaded once at the appropriate lifecycle point, never
    per-detection-event and never repeatedly."""
    paths = _ready_paths(tmp_path)
    call_count = 0

    def _counting_provider() -> str:
        nonlocal call_count
        call_count += 1
        return DEVICE_ID

    handler = DetectionIngestHandler(
        socket_path=paths.detection_socket_file,
        queue_capacity=10,
        device_id_provider=_counting_provider,
        camera_id="camera1",
        class_names={0: "gun"},
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(cooldown_seconds=0.0),
        repository=DetectionEventRepository(paths.database_file),
    )

    assert call_count == 0  # never called at construction

    assert handler._resolve_device_id() == DEVICE_ID  # noqa: SLF001
    assert handler._resolve_device_id() == DEVICE_ID  # noqa: SLF001
    assert handler._resolve_device_id() == DEVICE_ID  # noqa: SLF001

    assert call_count == 1


def test_detection_factory_disabled_never_reads_class_labels_or_identity(tmp_path: Path) -> None:
    """Feature isolation: disabled must behave as if the feature does not exist at all — no I/O."""
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path)  # both switches default False
    # No labels.txt staged and no identity stored; a real read of either would raise here.
    identity_repository = DeviceIdentityRepository(paths.database_file)

    assert default_detection_components_factory(settings, paths, identity_repository) == ()


# ==================================================================================================
# main._components_factory — composition ordering matrix
# ==================================================================================================


def test_main_factory_both_disabled_registers_nothing(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    assert _components_factory(settings, paths, identity_repository) == ()


def test_main_factory_deepstream_only_registers_one_deepstream_manager(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path, deepstream_enabled=True)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    components = _components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    assert isinstance(components[0], DeepStreamProcessManager)


def test_main_factory_both_enabled_orders_ingest_handler_before_deepstream(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16" / "labels.txt").write_text(
        "gun\nknife\n", encoding="utf-8"
    )
    settings = _settings_for(
        tmp_path,
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH_PATH,
        deepstream_model_profile="yolov4-fp16",
    )
    identity_repository = DeviceIdentityRepository(paths.database_file)
    _store_identity(identity_repository)

    components = _components_factory(settings, paths, identity_repository)

    assert len(components) == 2
    assert isinstance(components[0], DetectionIngestHandler)
    assert isinstance(components[1], DeepStreamProcessManager)


def test_main_factory_no_duplicate_across_repeated_calls(tmp_path: Path) -> None:
    """Each call builds fresh instances — repeated invocation (e.g. an Agent restart re-running the
    lifespan) never returns/shares a stale component."""
    paths = _ready_paths(tmp_path)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16" / "labels.txt").write_text(
        "gun\n", encoding="utf-8"
    )
    settings = _settings_for(
        tmp_path,
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH_PATH,
        deepstream_model_profile="yolov4-fp16",
    )
    identity_repository = DeviceIdentityRepository(paths.database_file)
    _store_identity(identity_repository)

    first = _components_factory(settings, paths, identity_repository)
    second = _components_factory(settings, paths, identity_repository)

    assert first[0] is not second[0]
    assert first[1] is not second[1]


# ==================================================================================================
# Feature isolation: pyds/DeepStream-Python bindings never enter the Agent dependency graph
# ==================================================================================================


def test_no_pyds_binding_is_imported_by_the_wiring_modules() -> None:
    import weapon_detection_agent.detection.ingest_handler  # noqa: F401
    import weapon_detection_agent.main  # noqa: F401

    assert "pyds" not in sys.modules
    assert "gi" not in sys.modules


# ==================================================================================================
# Lifecycle ordering (real OperationalStateCoordinator, fake components standing in for the two
# real ones — the coordinator's own start-order/reverse-stop-order contract is what actually
# enforces T-87's required ordering; these tests prove it holds for this exact component shape)
# ==================================================================================================


def _coordinator(
    tmp_path: Path, components: tuple[FakeComponent, ...]
) -> OperationalStateCoordinator:
    paths = _ready_paths(tmp_path)
    return OperationalStateCoordinator(
        identity_repository=DeviceIdentityRepository(paths.database_file),
        components=components,  # type: ignore[arg-type]  # structural fakes of the component shape
        initial_state=OperationalState.OPERATIONAL,
    )


def test_ingest_handler_starts_before_deepstream(tmp_path: Path) -> None:
    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log)
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = _coordinator(tmp_path, (ingest, deepstream))

        await coordinator.start_operational_components()

        assert log == ["start:detection-ingest", "start:deepstream"]

    asyncio.run(_scenario())


def test_sync_worker_stops_first_when_all_three_are_registered(tmp_path: Path) -> None:
    """FS-06 §8: shutdown is the reverse of start order — the sync worker cancels first, then
    DeepStream, then the ingest handler."""

    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log)
        deepstream = FakeComponent("deepstream", log=log)
        sync = FakeComponent("detection-event-sync", log=log)
        coordinator = _coordinator(tmp_path, (ingest, deepstream, sync))
        await coordinator.start_operational_components()
        log.clear()

        await coordinator.enter_reactivation_required()

        assert log == [
            "stop:detection-event-sync",
            "stop:deepstream",
            "stop:detection-ingest",
        ]

    asyncio.run(_scenario())


def test_deepstream_stops_before_ingest_handler(tmp_path: Path) -> None:
    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log)
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = _coordinator(tmp_path, (ingest, deepstream))
        await coordinator.start_operational_components()
        log.clear()

        await coordinator.enter_reactivation_required()

        assert log == ["stop:deepstream", "stop:detection-ingest"]

    asyncio.run(_scenario())


def test_ingest_handler_startup_failure_prevents_deepstream_start(tmp_path: Path) -> None:
    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log, fail_start=True)
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = _coordinator(tmp_path, (ingest, deepstream))

        with pytest.raises(OperationalComponentStartError):
            await coordinator.start_operational_components()

        assert deepstream.start_count == 0
        assert log == []

    asyncio.run(_scenario())


def test_deepstream_startup_failure_cleans_up_already_started_ingest_handler(
    tmp_path: Path,
) -> None:
    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log)
        deepstream = FakeComponent("deepstream", log=log, fail_start=True)
        coordinator = _coordinator(tmp_path, (ingest, deepstream))

        with pytest.raises(OperationalComponentStartError):
            await coordinator.start_operational_components()

        assert ingest.start_count == 1
        assert ingest.stop_count == 1  # rolled back
        assert log == ["start:detection-ingest", "stop:detection-ingest"]

    asyncio.run(_scenario())


def test_shutdown_remains_bounded_and_repeat_start_is_a_safe_no_op(tmp_path: Path) -> None:
    async def _scenario() -> None:
        log: list[str] = []
        ingest = FakeComponent("detection-ingest", log=log)
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = _coordinator(tmp_path, (ingest, deepstream))

        await asyncio.wait_for(coordinator.start_operational_components(), timeout=5.0)
        await asyncio.wait_for(coordinator.start_operational_components(), timeout=5.0)  # no-op

        assert ingest.start_count == 1
        assert deepstream.start_count == 1

        await asyncio.wait_for(coordinator.enter_reactivation_required(), timeout=5.0)

    asyncio.run(_scenario())


# ==================================================================================================
# Real-integration: the actual DetectionIngestHandler (not a fake) inside a real coordinator, with a
# fake standing in only for DeepStream — proves the identity-unavailable failure and the socket-
# before-DeepStream ordering hold for the real component, not just the abstract fake shape above.
# ==================================================================================================


def _real_ingest_handler(
    paths, identity_repository: DeviceIdentityRepository
) -> DetectionIngestHandler:
    return DetectionIngestHandler(
        socket_path=paths.detection_socket_file,
        queue_capacity=10,
        device_id_provider=default_device_identity_provider(identity_repository),
        camera_id="camera1",
        class_names={0: "gun"},
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(cooldown_seconds=5.0),
        repository=DetectionEventRepository(paths.database_file),
    )


@requires_unix_sockets
def test_deepstream_never_starts_when_ingest_handler_cannot_obtain_identity(tmp_path: Path) -> None:
    async def _scenario() -> None:
        paths = _ready_paths(tmp_path)
        identity_repository = DeviceIdentityRepository(paths.database_file)  # nothing stored
        ingest = _real_ingest_handler(paths, identity_repository)
        log: list[str] = []
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = OperationalStateCoordinator(
            identity_repository=identity_repository,
            components=(ingest, deepstream),  # type: ignore[arg-type]
            initial_state=OperationalState.OPERATIONAL,
        )

        with pytest.raises(OperationalComponentStartError):
            await coordinator.start_operational_components()

        assert deepstream.start_count == 0
        assert not paths.detection_socket_file.exists()

    asyncio.run(_scenario())


# ==================================================================================================
# main._components_factory — DetectionEventSyncWorker wiring (IP-08 T-108, FS-06 §8)
# ==================================================================================================


def test_main_factory_sync_only_registers_one_sync_worker(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path, detection_sync_enabled=True)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    components = _components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    assert isinstance(components[0], DetectionEventSyncWorker)


def test_main_factory_sync_disabled_by_default_registers_nothing(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    settings = _settings_for(tmp_path)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    assert settings.detection_sync_enabled is False
    assert _components_factory(settings, paths, identity_repository) == ()


def test_main_factory_orders_ingest_deepstream_then_sync_worker_last(tmp_path: Path) -> None:
    paths = _ready_paths(tmp_path)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16" / "labels.txt").write_text(
        "gun\nknife\n", encoding="utf-8"
    )
    settings = _settings_for(
        tmp_path,
        deepstream_enabled=True,
        detection_events_enabled=True,
        detection_sync_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH_PATH,
        deepstream_model_profile="yolov4-fp16",
    )
    identity_repository = DeviceIdentityRepository(paths.database_file)
    _store_identity(identity_repository)

    components = _components_factory(settings, paths, identity_repository)

    assert len(components) == 3
    assert isinstance(components[0], DetectionIngestHandler)
    assert isinstance(components[1], DeepStreamProcessManager)
    assert isinstance(components[2], DetectionEventSyncWorker)


@requires_unix_sockets
def test_socket_is_bound_before_deepstream_starts_once_identity_is_available(
    tmp_path: Path,
) -> None:
    async def _scenario() -> None:
        paths = _ready_paths(tmp_path)
        identity_repository = DeviceIdentityRepository(paths.database_file)
        _store_identity(identity_repository)  # activation already completed
        ingest = _real_ingest_handler(paths, identity_repository)
        log: list[str] = []
        deepstream = FakeComponent("deepstream", log=log)
        coordinator = OperationalStateCoordinator(
            identity_repository=identity_repository,
            components=(ingest, deepstream),  # type: ignore[arg-type]
            initial_state=OperationalState.OPERATIONAL,
        )

        await coordinator.start_operational_components()
        try:
            assert paths.detection_socket_file.exists()  # ingest handler bound its socket
            assert deepstream.start_count == 1  # and DeepStream started afterward
        finally:
            await coordinator.enter_reactivation_required()

        assert not paths.detection_socket_file.exists()  # removed on stop

    asyncio.run(_scenario())
