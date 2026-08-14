"""Unit tests for Device configuration pipeline-relevance validation (FS-11 §3/§7, IP-13 T-231)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from weapon_detection_agent.configuration.models import DeviceCameraConfig, DeviceConfiguration
from weapon_detection_agent.configuration.validation import (
    ConfigurationValidationError,
    validate_configuration,
)

DEVICE_ID = uuid4()
BRANCH_ID = uuid4()
GENERATED_AT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def _camera(**overrides: object) -> DeviceCameraConfig:
    camera_id = uuid4()
    base: dict[str, object] = {
        "camera_id": camera_id,
        "camera_key": str(camera_id),
        "name": "Front Camera",
        "stream_url": "rtsp://camera.example.invalid:554/stream1",
        "enabled": True,
        "source_order": 0,
        "output_path": f"cameras/{camera_id}",
    }
    base.update(overrides)
    return DeviceCameraConfig(**base)  # type: ignore[arg-type]


def _configuration(
    cameras: tuple[DeviceCameraConfig, ...], **overrides: object
) -> DeviceConfiguration:
    base: dict[str, object] = {
        "schema_version": 1,
        "configuration_version": "abc123",
        "device_id": DEVICE_ID,
        "branch_id": BRANCH_ID,
        "generated_at_utc": GENERATED_AT,
        "cameras": cameras,
    }
    base.update(overrides)
    return DeviceConfiguration(**base)  # type: ignore[arg-type]


def test_valid_single_camera_passes() -> None:
    validate_configuration(
        _configuration((_camera(),)), expected_device_id=DEVICE_ID, max_cameras=8
    )  # no raise


def test_valid_multi_camera_passes() -> None:
    cameras = (_camera(source_order=0), _camera(source_order=1))
    validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)


def test_empty_camera_list_is_valid() -> None:
    validate_configuration(_configuration(()), expected_device_id=DEVICE_ID, max_cameras=8)


def test_unsupported_schema_version_rejected() -> None:
    config = _configuration((_camera(),), schema_version=2)
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(config, expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "unsupported_schema_version"


def test_device_id_mismatch_rejected() -> None:
    config = _configuration((_camera(),))
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(config, expected_device_id=uuid4(), max_cameras=8)
    assert excinfo.value.reason == "device_id_mismatch"


def test_missing_branch_id_rejected() -> None:
    config = _configuration((_camera(),), branch_id=UUID(int=0))
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(config, expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "missing_branch_id"


def test_missing_configuration_version_rejected() -> None:
    config = _configuration((_camera(),), configuration_version="")
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(config, expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "missing_configuration_version"


def test_duplicate_camera_id_rejected() -> None:
    camera_id = uuid4()
    cameras = (
        _camera(camera_id=camera_id, source_order=0),
        _camera(camera_id=camera_id, source_order=1),
    )
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "duplicate_camera_id"


def test_duplicate_source_order_rejected() -> None:
    cameras = (_camera(source_order=0), _camera(source_order=0))
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "duplicate_source_order"


def test_negative_source_order_rejected() -> None:
    cameras = (_camera(source_order=-1),)
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "negative_source_order"


def test_too_many_cameras_rejected() -> None:
    cameras = tuple(_camera(source_order=i) for i in range(3))
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=2)
    assert excinfo.value.reason == "too_many_cameras"


def test_empty_stream_url_rejected() -> None:
    cameras = (_camera(stream_url=""),)
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "empty_stream_url"


@pytest.mark.parametrize("scheme", ["http", "https", "ftp", "file"])
def test_unapproved_url_scheme_rejected(scheme: str) -> None:
    cameras = (_camera(stream_url=f"{scheme}://camera.example.invalid/stream1"),)
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "unapproved_url_scheme"


def test_rtsps_scheme_is_approved() -> None:
    cameras = (_camera(stream_url="rtsps://camera.example.invalid:554/stream1"),)
    validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)


def test_relative_url_rejected() -> None:
    cameras = (_camera(stream_url="rtsp:stream1"),)
    with pytest.raises(ConfigurationValidationError) as excinfo:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
    assert excinfo.value.reason == "stream_url_not_absolute"


def test_rejection_is_atomic_first_violation_wins_no_partial_application() -> None:
    # Two independent violations at once (duplicate CameraId AND a bad scheme) still raise exactly
    # one ConfigurationValidationError — the caller applies nothing at all, never a partial list.
    camera_id = uuid4()
    cameras = (
        _camera(camera_id=camera_id, source_order=0),
        _camera(camera_id=camera_id, source_order=1, stream_url="http://bad"),
    )
    with pytest.raises(ConfigurationValidationError):
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)


# --- FS-11 §11: per-camera annotated output path ------------------------------------------------


def test_duplicate_output_path_is_rejected() -> None:
    """FS-12 §2.1: two Cameras cannot share a mount.

    Under FS-12 a shared mount can only arise from a shared CameraKey, because `output_path` is
    required to equal `cameras/{camera_key}`. The key check runs first and is the one that fires, so
    this asserts the reason the validator actually reports rather than the pre-FS-12 code — the
    protection is the same, and `duplicate_output_path` survives as unreachable defence in depth.
    """
    cameras = (
        _camera(source_order=0, camera_key="same", output_path="cameras/same"),
        _camera(source_order=1, camera_key="same", output_path="cameras/same"),
    )

    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)

    assert exc_info.value.reason == "duplicate_camera_key"


def test_output_path_not_matching_camera_key_is_rejected() -> None:
    """FS-12 §2.1 — the Backend may not advertise one public key while publishing elsewhere."""
    cameras = (_camera(camera_key="front-entrance", output_path="cameras/somewhere-else"),)

    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)

    assert exc_info.value.reason == "output_path_camera_key_mismatch"


@pytest.mark.parametrize(
    "camera_key",
    [
        "",
        "ab",
        "Front-Entrance",
        "front entrance",
        "front_entrance",
        "front/entrance",
        "-front",
        "front-",
        "a" * 65,
    ],
)
def test_malformed_camera_key_is_rejected(camera_key: str) -> None:
    """FS-12 §3 defence in depth — the Agent re-checks what becomes a live RTSP mount path."""
    cameras = (_camera(camera_key=camera_key, output_path=f"cameras/{camera_key}"),)

    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)

    assert exc_info.value.reason in {"empty_camera_key", "invalid_camera_key", "unsafe_output_path"}


def test_camera_key_based_configuration_is_accepted() -> None:
    """The FS-12 happy path: two administrator-defined keys, two distinct mounts."""
    cameras = (
        _camera(source_order=0, camera_key="front-camera", output_path="cameras/front-camera"),
        _camera(
            camera_id=uuid4(),
            source_order=1,
            camera_key="rear-entrance",
            output_path="cameras/rear-entrance",
        ),
    )

    validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)


def test_empty_output_path_is_rejected() -> None:
    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(
            _configuration((_camera(output_path=""),)),
            expected_device_id=DEVICE_ID,
            max_cameras=8,
        )
    assert exc_info.value.reason == "empty_output_path"


def test_absolute_output_path_is_rejected() -> None:
    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(
            _configuration((_camera(output_path="/cameras/abc"),)),
            expected_device_id=DEVICE_ID,
            max_cameras=8,
        )
    assert exc_info.value.reason == "output_path_not_relative"


@pytest.mark.parametrize(
    "unsafe",
    [
        "cameras/../escape",
        "rtsp://elsewhere.invalid/cameras/abc",
        "cameras\abc",
        "cameras/abc?query=1",
        "cameras/abc#fragment",
        "cameras//abc",
        "cameras/ab\nc",
    ],
)
def test_unsafe_output_path_is_rejected(unsafe: str) -> None:
    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(
            _configuration((_camera(output_path=unsafe),)),
            expected_device_id=DEVICE_ID,
            max_cameras=8,
        )
    assert exc_info.value.reason in {"unsafe_output_path", "output_path_not_relative"}


def test_over_long_output_path_is_rejected() -> None:
    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(
            _configuration((_camera(output_path="cameras/" + "a" * 600),)),
            expected_device_id=DEVICE_ID,
            max_cameras=8,
        )
    assert exc_info.value.reason == "output_path_too_long"


def test_distinct_output_paths_are_accepted() -> None:
    # FS-12 §2.1: a mount must be its key's derived path, so distinct paths now mean distinct keys.
    cameras = (
        _camera(source_order=0, camera_key="cam-a", output_path="cameras/cam-a"),
        _camera(source_order=1, camera_key="cam-b", output_path="cameras/cam-b"),
    )
    validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)
