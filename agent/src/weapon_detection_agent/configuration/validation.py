"""Pipeline-relevance validation for a fetched Device configuration (FS-11 §3/§7, IP-13 T-231).

Wire-shape parsing already happened in :mod:`weapon_detection_agent.configuration.client` — a
malformed response never reaches here. This module enforces the *business* rules a structurally
valid response must still satisfy before it is safe to apply: schema-version support, identity
consistency, camera-list invariants (uniqueness, non-negative ordering, absolute RTSP URL, bounded
count), and it rejects atomically — a configuration that fails any single rule is rejected as a
whole, never partially applied (FS-11 §7).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from uuid import UUID

from weapon_detection_agent.configuration.models import DeviceCameraConfig, DeviceConfiguration

SUPPORTED_SCHEMA_VERSION = 1
_APPROVED_URL_SCHEMES = frozenset({"rtsp", "rtsps"})

# FS-11 §11: an output path is a *relative* RTSP mount ("cameras/<guid>"), never an absolute URL and
# never an escape upwards. Bounded so a hostile/malformed response cannot produce an unusable mount.
_MAX_OUTPUT_PATH_LENGTH = 512
_FORBIDDEN_OUTPUT_PATH_SUBSTRINGS = ("..", "://", "\\", "?", "#", "//")

# FS-12 §3 — the frozen CameraKey contract, mirrored here as a *defence-in-depth* check. The Backend
# is the authority that rejects a bad key at creation time; the Agent re-checks because this is what
# becomes a live RTSP mount path on the Jetson, and a compromised or buggy Backend must not be able
# to steer that path.
_OUTPUT_PATH_PREFIX = "cameras/"
_CAMERA_KEY_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$")


class ConfigurationValidationError(Exception):
    """A fetched configuration failed a pipeline-relevance rule and must not be applied.

    ``reason`` is a short, stable, non-sensitive machine-readable code (never a StreamUrl or any
    other potentially credential-bearing value) — safe to log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_configuration(
    configuration: DeviceConfiguration,
    *,
    expected_device_id: UUID,
    max_cameras: int,
) -> None:
    """Raise :class:`ConfigurationValidationError` if ``configuration`` must not be applied.

    Returns ``None`` (does not mutate or return a "cleaned" copy) when every rule passes — the
    caller applies ``configuration`` exactly as received. Rules, in order (FS-11 §7):

    1. schema version is supported;
    2. DeviceId matches this Agent's own identity (never another Device's configuration);
    3. BranchId is present;
    4. no duplicate CameraId;
    5. no duplicate SourceOrder;
    6. every SourceOrder is non-negative;
    7. enabled-camera count is within ``max_cameras``;
    8. every camera has a non-empty, absolute rtsp(s):// StreamUrl;
    9. ConfigurationVersion is present and non-empty;
    10. every camera has a safe, relative, unique OutputPath (FS-11 §11).
    """
    if configuration.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationValidationError("unsupported_schema_version")

    if configuration.device_id != expected_device_id:
        raise ConfigurationValidationError("device_id_mismatch")

    if configuration.branch_id == UUID(int=0):
        raise ConfigurationValidationError("missing_branch_id")

    if not configuration.configuration_version:
        raise ConfigurationValidationError("missing_configuration_version")

    if len(configuration.cameras) > max_cameras:
        raise ConfigurationValidationError("too_many_cameras")

    seen_camera_ids: set[UUID] = set()
    seen_camera_keys: set[str] = set()
    seen_source_orders: set[int] = set()
    seen_output_paths: set[str] = set()
    for camera in configuration.cameras:
        if camera.camera_id in seen_camera_ids:
            raise ConfigurationValidationError("duplicate_camera_id")
        seen_camera_ids.add(camera.camera_id)

        # FS-12 §3: the key must be well-formed, and two Cameras must not share one — a duplicate
        # key would mean a duplicate mount, i.e. one Camera's frames published on another's URL.
        _validate_camera_key(camera)
        if camera.camera_key in seen_camera_keys:
            raise ConfigurationValidationError("duplicate_camera_key")
        seen_camera_keys.add(camera.camera_key)

        if camera.source_order < 0:
            raise ConfigurationValidationError("negative_source_order")

        if camera.source_order in seen_source_orders:
            raise ConfigurationValidationError("duplicate_source_order")
        seen_source_orders.add(camera.source_order)

        _validate_stream_url(camera)

        # Two Cameras sharing one mount would silently publish one camera's frames on the other's
        # URL — rejected as a whole configuration, never partially applied (FS-11 §7/§11).
        _validate_output_path(camera)
        if camera.output_path in seen_output_paths:
            raise ConfigurationValidationError("duplicate_output_path")
        seen_output_paths.add(camera.output_path)


def _validate_camera_key(camera: DeviceCameraConfig) -> None:
    """Enforce the FS-12 §3 key contract. The key itself is safe to log; a StreamUrl is not."""
    if not camera.camera_key:
        raise ConfigurationValidationError("empty_camera_key")

    if not _CAMERA_KEY_PATTERN.match(camera.camera_key):
        raise ConfigurationValidationError("invalid_camera_key")


def _validate_output_path(camera: DeviceCameraConfig) -> None:
    """FS-11 §11: the mount must be usable as an RTSP path segment set and must not be able to
    reach outside the Bridge's own mount namespace."""
    output_path = camera.output_path
    if not output_path:
        raise ConfigurationValidationError("empty_output_path")

    if len(output_path) > _MAX_OUTPUT_PATH_LENGTH:
        raise ConfigurationValidationError("output_path_too_long")

    if output_path.startswith("/"):
        raise ConfigurationValidationError("output_path_not_relative")

    if any(token in output_path for token in _FORBIDDEN_OUTPUT_PATH_SUBSTRINGS):
        raise ConfigurationValidationError("unsafe_output_path")

    # Control characters would corrupt the RTSP mount-point string the server registers.
    if any(character < " " or character == "\x7f" for character in output_path):
        raise ConfigurationValidationError("unsafe_output_path")

    # FS-12 §2.1: the mount must be exactly the key's derived path. Checking the *relationship*
    # rather than each value independently is what stops a Backend from advertising one public key
    # to an operator while quietly publishing that Camera's stream somewhere else.
    if output_path != _OUTPUT_PATH_PREFIX + camera.camera_key:
        raise ConfigurationValidationError("output_path_camera_key_mismatch")


def _validate_stream_url(camera: DeviceCameraConfig) -> None:
    if not camera.stream_url:
        raise ConfigurationValidationError("empty_stream_url")

    parsed = urlparse(camera.stream_url)
    if parsed.scheme not in _APPROVED_URL_SCHEMES:
        raise ConfigurationValidationError("unapproved_url_scheme")

    if not parsed.netloc:
        raise ConfigurationValidationError("stream_url_not_absolute")
