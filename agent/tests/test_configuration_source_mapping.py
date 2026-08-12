"""Unit tests for the generation-scoped source_id -> CameraId mapping (FS-11 §9, IP-13 T-237)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from weapon_detection_agent.configuration.models import DeviceCameraConfig, DeviceConfiguration
from weapon_detection_agent.configuration.source_mapping import SourceGenerationTracker

DEVICE_ID = uuid4()
BRANCH_ID = uuid4()
GENERATED_AT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def _camera(camera_id, source_order, enabled=True):  # noqa: ANN001, ANN201 - test helper
    return DeviceCameraConfig(
        camera_id=camera_id,
        camera_key=str(camera_id),
        name=f"Camera {source_order}",
        stream_url=f"rtsp://camera.example.invalid:554/s{source_order}",
        enabled=enabled,
        source_order=source_order,
        output_path=f"cameras/{camera_id}",
    )


def _configuration(cameras):  # noqa: ANN001, ANN201 - test helper
    return DeviceConfiguration(
        schema_version=1,
        configuration_version="v1",
        device_id=DEVICE_ID,
        branch_id=BRANCH_ID,
        generated_at_utc=GENERATED_AT,
        cameras=tuple(cameras),
    )


def test_no_current_generation_before_first_install() -> None:
    tracker = SourceGenerationTracker()
    assert tracker.current is None


def test_install_maps_source_order_to_camera_id() -> None:
    camera_a = uuid4()
    camera_b = uuid4()
    tracker = SourceGenerationTracker()

    generation = tracker.install(_configuration([_camera(camera_a, 0), _camera(camera_b, 1)]))

    assert generation.resolve(0) == camera_a
    assert generation.resolve(1) == camera_b
    assert tracker.current is generation


def test_unknown_source_id_resolves_to_none_not_an_exception() -> None:
    tracker = SourceGenerationTracker()
    tracker.install(_configuration([_camera(uuid4(), 0)]))

    assert tracker.current is not None
    assert tracker.current.resolve(99) is None


def test_disabled_camera_is_excluded_from_the_mapping() -> None:
    tracker = SourceGenerationTracker()
    tracker.install(_configuration([_camera(uuid4(), 0, enabled=False)]))

    assert tracker.current is not None
    assert tracker.current.resolve(0) is None


def test_each_install_increments_the_generation_number() -> None:
    tracker = SourceGenerationTracker()
    first = tracker.install(_configuration([_camera(uuid4(), 0)]))
    second = tracker.install(_configuration([_camera(uuid4(), 0)]))

    assert second.generation == first.generation + 1


def test_installing_a_new_generation_replaces_current_not_merges() -> None:
    camera_old = uuid4()
    camera_new = uuid4()
    tracker = SourceGenerationTracker()
    tracker.install(_configuration([_camera(camera_old, 0)]))
    tracker.install(_configuration([_camera(camera_new, 0)]))

    assert tracker.current is not None
    assert tracker.current.resolve(0) == camera_new
