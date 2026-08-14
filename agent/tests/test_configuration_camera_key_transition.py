"""FS-12 §5/§7 — the GUID-mount → CameraKey-mount transition (IP-14 T-279/T-281/T-282).

Covers the three things that make the one-time production rollout safe: an FS-11 cache written
before ``cameraKey`` existed still loads, the resulting configuration still validates, and the
detection identity is unaffected by any of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from weapon_detection_agent.configuration.models import DeviceCameraConfig, DeviceConfiguration
from weapon_detection_agent.configuration.serialization import (
    deserialize_configuration,
    serialize_configuration,
)
from weapon_detection_agent.configuration.source_mapping import SourceGenerationTracker
from weapon_detection_agent.configuration.validation import (
    ConfigurationValidationError,
    validate_configuration,
)

DEVICE_ID = UUID("965032b6-26af-4506-81f9-2e7307290fa1")
BRANCH_ID = UUID("9b6796f7-b2f2-47f3-a2df-a06bf94c1345")
FRONT_ID = UUID("2613b331-8783-4d51-903a-3e41a979a14c")
REAR_ID = UUID("ad8a1f09-7fba-4794-8f73-63c7e2c57c92")


def _camera(camera_id: UUID, camera_key: str, source_order: int) -> DeviceCameraConfig:
    return DeviceCameraConfig(
        camera_id=camera_id,
        camera_key=camera_key,
        name="Display Only",
        stream_url=f"rtsp://camera.example.invalid:554/stream{source_order}",
        enabled=True,
        source_order=source_order,
        output_path=f"cameras/{camera_key}",
    )


def _configuration(
    cameras: tuple[DeviceCameraConfig, ...], version: str = "v-new"
) -> DeviceConfiguration:
    return DeviceConfiguration(
        schema_version=1,
        configuration_version=version,
        device_id=DEVICE_ID,
        branch_id=BRANCH_ID,
        generated_at_utc=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        cameras=cameras,
    )


def _legacy_fs11_cache() -> str:
    """An FS-11 ConfigCache payload — GUID output paths, and no ``cameraKey`` field at all."""
    return json.dumps(
        {
            "schemaVersion": 1,
            "configurationVersion": "0cb4b45941bef175",
            "deviceId": str(DEVICE_ID),
            "branchId": str(BRANCH_ID),
            "generatedAtUtc": "2026-07-31T17:46:16.670789+00:00",
            "cameras": [
                {
                    "cameraId": str(FRONT_ID),
                    "name": "Front Camera",
                    "streamUrl": "rtsp://100.77.146.5:8554/camera1",
                    "enabled": True,
                    "sourceOrder": 0,
                    "outputPath": f"cameras/{FRONT_ID}",
                },
                {
                    "cameraId": str(REAR_ID),
                    "name": "Rear Entrance",
                    "streamUrl": "rtsp://100.77.146.5:8554/camera2",
                    "enabled": True,
                    "sourceOrder": 1,
                    "outputPath": f"cameras/{REAR_ID}",
                },
            ],
            "pipelineHash": "0cb4b45941bef175",
        }
    )


# --- Cache compatibility (FS-12 §5) ------------------------------------------------------------


def test_legacy_fs11_cache_still_loads() -> None:
    """An in-place upgrade must not discard last-known-good state: the Backend may be down."""
    configuration = deserialize_configuration(_legacy_fs11_cache())

    assert len(configuration.cameras) == 2
    assert configuration.cameras[0].camera_id == FRONT_ID
    assert configuration.cameras[1].camera_id == REAR_ID


def test_legacy_cache_recovers_camera_key_from_output_path() -> None:
    """The recovered key is the GUID string, which keeps the stored mount byte-identical."""
    configuration = deserialize_configuration(_legacy_fs11_cache())

    assert configuration.cameras[0].camera_key == str(FRONT_ID)
    assert configuration.cameras[0].output_path == f"cameras/{FRONT_ID}"


def test_legacy_cache_still_validates() -> None:
    """A GUID satisfies the FS-12 key contract, so the legacy cache needs no special case."""
    configuration = deserialize_configuration(_legacy_fs11_cache())

    validate_configuration(configuration, expected_device_id=DEVICE_ID, max_cameras=8)


def test_legacy_cache_is_not_silently_corrupted() -> None:
    """Round-tripping a legacy cache preserves every field and upgrades it in place."""
    original = deserialize_configuration(_legacy_fs11_cache())

    round_tripped = deserialize_configuration(serialize_configuration(original))

    assert round_tripped == original
    assert json.loads(serialize_configuration(original))["cameras"][0]["cameraKey"] == str(FRONT_ID)


def test_cache_with_unrecognised_output_path_is_rejected_not_guessed() -> None:
    payload = json.loads(_legacy_fs11_cache())
    payload["cameras"][0]["outputPath"] = "somewhere/else"

    with pytest.raises(ValueError):
        deserialize_configuration(json.dumps(payload))


def test_camera_key_round_trips_through_the_cache() -> None:
    configuration = _configuration(
        (_camera(FRONT_ID, "front-camera", 0), _camera(REAR_ID, "rear-entrance", 1))
    )

    round_tripped = deserialize_configuration(serialize_configuration(configuration))

    assert round_tripped == configuration
    assert [c.output_path for c in round_tripped.cameras] == [
        "cameras/front-camera",
        "cameras/rear-entrance",
    ]


# --- The one-restart transition (FS-12 §7) -----------------------------------------------------


def test_migration_changes_configuration_version_exactly_once() -> None:
    """GUID cache vs CameraKey Backend response differ, so the coordinator restarts once — and the
    re-serialized new configuration then compares equal to itself, so it does not restart again."""
    cached = deserialize_configuration(_legacy_fs11_cache())
    fetched = _configuration(
        (_camera(FRONT_ID, "front-camera", 0), _camera(REAR_ID, "rear-entrance", 1))
    )

    assert cached.configuration_version != fetched.configuration_version

    # The guard against a restart loop: what is persisted must deserialize back to exactly what was
    # applied, or the next comparison would differ again and restart the Bridge forever.
    assert deserialize_configuration(serialize_configuration(fetched)) == fetched


def test_stable_configuration_does_not_appear_changed_after_a_cache_round_trip() -> None:
    configuration = _configuration((_camera(FRONT_ID, "front-camera", 0),))

    first = deserialize_configuration(serialize_configuration(configuration))
    second = deserialize_configuration(serialize_configuration(first))

    assert first == second
    assert first.configuration_version == configuration.configuration_version


# --- Detection identity regression (FS-12 §2, Phase 7) -----------------------------------------


def test_source_mapping_resolves_immutable_guids_not_camera_keys() -> None:
    registry = SourceGenerationTracker()
    configuration = _configuration(
        (_camera(FRONT_ID, "front-camera", 0), _camera(REAR_ID, "rear-entrance", 1))
    )

    generation = registry.install(configuration)

    assert generation.resolve(0) == FRONT_ID
    assert generation.resolve(1) == REAR_ID


def test_resolved_identity_is_a_uuid_never_a_camera_key() -> None:
    registry = SourceGenerationTracker()
    generation = registry.install(_configuration((_camera(FRONT_ID, "front-camera", 0),)))

    resolved = generation.resolve(0)

    assert isinstance(resolved, UUID)
    assert str(resolved) != "front-camera"


def test_changing_the_camera_key_does_not_change_the_detection_identity() -> None:
    """FS-12 §2 — the key is public output identity; CameraId is detection identity."""
    registry = SourceGenerationTracker()

    before = registry.install(_configuration((_camera(FRONT_ID, "front-camera", 0),)))
    after = registry.install(_configuration((_camera(FRONT_ID, "a-totally-different-key", 0),)))

    assert before.resolve(0) == after.resolve(0) == FRONT_ID


def test_unknown_source_id_is_rejected_safely() -> None:
    registry = SourceGenerationTracker()
    generation = registry.install(_configuration((_camera(FRONT_ID, "front-camera", 0),)))

    assert generation.resolve(7) is None


def test_camera_name_is_never_an_identity() -> None:
    registry = SourceGenerationTracker()
    configuration = _configuration((_camera(FRONT_ID, "front-camera", 0),))

    generation = registry.install(configuration)

    assert generation.resolve(0) == FRONT_ID
    assert configuration.cameras[0].name == "Display Only"


# --- Atomic rejection keeps last-known-good running (FS-12 §3) ---------------------------------


def test_invalid_new_configuration_is_rejected_whole() -> None:
    good = _camera(FRONT_ID, "front-camera", 0)
    bad = _camera(REAR_ID, "Rear Entrance", 1)  # uppercase + space: not a valid key

    with pytest.raises(ConfigurationValidationError):
        validate_configuration(
            _configuration((good, bad)), expected_device_id=DEVICE_ID, max_cameras=8
        )


def test_duplicate_camera_key_across_two_cameras_is_rejected() -> None:
    cameras = (_camera(FRONT_ID, "shared-key", 0), _camera(uuid4(), "shared-key", 1))

    with pytest.raises(ConfigurationValidationError) as exc_info:
        validate_configuration(_configuration(cameras), expected_device_id=DEVICE_ID, max_cameras=8)

    assert exc_info.value.reason == "duplicate_camera_key"
