"""JSON (de)serialization for a :class:`~weapon_detection_agent.configuration.models.
DeviceConfiguration`, for ``ConfigCache`` storage (FS-11 §6, IP-13 T-232).

Kept separate from :mod:`weapon_detection_agent.configuration.client` (which parses the Backend's
wire response) because the cached shape is this Agent's own — stable across a Backend upgrade even
if the wire contract's field names ever changed — and separate from
:mod:`weapon_detection_agent.configuration.models` to keep the dataclasses free of I/O concerns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from weapon_detection_agent.configuration.models import DeviceCameraConfig, DeviceConfiguration
from weapon_detection_agent.persistence.models import to_iso_utc


def serialize_configuration(configuration: DeviceConfiguration) -> str:
    """Render ``configuration`` as the ``ConfigCache.ConfigJson`` payload (FS-11 §6)."""
    return json.dumps(
        {
            "schemaVersion": configuration.schema_version,
            "configurationVersion": configuration.configuration_version,
            "deviceId": str(configuration.device_id),
            "branchId": str(configuration.branch_id),
            "generatedAtUtc": to_iso_utc(configuration.generated_at_utc),
            "cameras": [
                {
                    "cameraId": str(camera.camera_id),
                    "cameraKey": camera.camera_key,
                    "name": camera.name,
                    "streamUrl": camera.stream_url,
                    "enabled": camera.enabled,
                    "sourceOrder": camera.source_order,
                    "outputPath": camera.output_path,
                }
                for camera in configuration.cameras
            ],
            # FS-11 §6: stored redundantly alongside configurationVersion today (identical value) so
            # a future change to what counts as "pipeline-relevant" needs only a new hash, not a
            # schema migration.
            "pipelineHash": configuration.configuration_version,
        }
    )


def deserialize_configuration(config_json: str) -> DeviceConfiguration:
    """Parse a previously-serialized ``ConfigJson`` payload back into a
    :class:`DeviceConfiguration`.

    Raises :class:`ValueError` for any malformed payload — a corrupt/hand-edited cache row is never
    silently accepted (mirrors the strict, exception-raising discipline of every other repository in
    this codebase, e.g. :class:`~weapon_detection_agent.persistence.config_cache_repository.
    ConfigCacheRepository.load`'s own ``InvalidConfigCacheStateError``).
    """
    try:
        data = json.loads(config_json)
    except json.JSONDecodeError as exc:
        raise ValueError("cached configuration is not valid JSON") from exc

    if not isinstance(data, dict):
        raise ValueError("cached configuration is not a JSON object")

    try:
        return DeviceConfiguration(
            schema_version=data["schemaVersion"],
            configuration_version=data["configurationVersion"],
            device_id=UUID(data["deviceId"]),
            branch_id=UUID(data["branchId"]),
            generated_at_utc=_parse_iso_utc(data["generatedAtUtc"]),
            cameras=tuple(_parse_camera(raw) for raw in data["cameras"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("cached configuration has an invalid shape") from exc


def _parse_camera(raw: object) -> DeviceCameraConfig:
    if not isinstance(raw, dict):
        raise ValueError("cached camera entry is not a JSON object")
    return DeviceCameraConfig(
        camera_id=UUID(raw["cameraId"]),
        camera_key=_parse_camera_key(raw),
        name=raw["name"],
        stream_url=raw["streamUrl"],
        enabled=raw["enabled"],
        source_order=raw["sourceOrder"],
        output_path=raw["outputPath"],
    )


def _parse_camera_key(raw: dict) -> str:
    """Read ``cameraKey``, tolerating an FS-11 cache written before the field existed.

    FS-12 §5/§7: an Agent upgrading in place loads a cache whose camera entries have no
    ``cameraKey`` at all. Rather than reject that cache — which would discard the last-known-good
    configuration precisely when the Backend might be unreachable — the key is recovered from the
    stored ``outputPath``, which is authoritative and always present.

    This is not a "derive the key from a label" path: ``outputPath`` *is* the mount, and an FS-11
    path is ``cameras/<guid>``, so the recovered key is the GUID string. That happens to satisfy the
    FS-12 key contract exactly (lowercase hex and hyphens, starting and ending alphanumeric), which
    is why the legacy cache still passes validation unchanged and needs no special case downstream.
    """
    camera_key = raw.get("cameraKey")
    if isinstance(camera_key, str) and camera_key:
        return camera_key

    output_path = raw["outputPath"]
    if not isinstance(output_path, str):
        raise ValueError("cached camera entry has an invalid outputPath")

    prefix = "cameras/"
    if not output_path.startswith(prefix):
        raise ValueError("cached camera entry has an unrecognised outputPath")

    recovered = output_path[len(prefix) :]
    if not recovered:
        raise ValueError("cached camera entry has an empty outputPath segment")

    return recovered


def _parse_iso_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("generatedAtUtc must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)
