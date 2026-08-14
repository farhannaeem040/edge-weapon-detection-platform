"""Async HTTP client for the Backend Device-configuration endpoint (FS-11 §3/§5, IP-13 T-230).

Responsibility, and only this:

    Device ID + shared secret  →  one GET to /api/v1/device/configuration
                                       →  parse the response strictly  →  return a typed
                                          DeviceConfiguration (unvalidated for pipeline-relevance —
                                          see :mod:`weapon_detection_agent.configuration.
                                          validation`) or a typed :class:`~weapon_detection_agent.
                                          configuration.models.ConfigurationFetchFailure`

Mirrors :class:`~weapon_detection_agent.validation.client.CredentialValidationClient`'s
construction, header constants, and strict-envelope-before-trust discipline. **Exactly one request
per call, no retries** — poll cadence and backoff are
:class:`~weapon_detection_agent.configuration.coordinator.DeviceConfigurationCoordinator`'s job.

A ``401`` here never locks or reactivates the Agent (FS-11 §5 step "a 401 does not cause local
credential rotation") — identical rule to the sync client. This client never logs a ``streamUrl``
(it may embed userinfo credentials, same rationale as the Backend's own ``RtspUrlSanitizer``) or the
Device secret — only camera count and safe metadata.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import TracebackType
from uuid import UUID

import httpx
from pydantic import SecretStr

from weapon_detection_agent.configuration.models import (
    ConfigurationFailureReason,
    ConfigurationFetchFailure,
    DeviceCameraConfig,
    DeviceConfiguration,
)

_LOGGER = logging.getLogger("weapon_detection_agent.configuration.client")

# The Backend Device-configuration path (FS-11 §3 — frozen).
CONFIGURATION_PATH = "/api/v1/device/configuration"

# The established device-authentication headers (ARCH-001 §14.1). Not new fields.
DEVICE_ID_HEADER = "X-Device-Id"
DEVICE_SECRET_HEADER = "X-Device-Secret"

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_SERVICE_UNAVAILABLE = 503

# A hard ceiling on response size, independent of device_config_max_cameras (FS-11 §7): rejects a
# pathological/hostile payload before it is even handed to the JSON parser.
_MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB


class DeviceConfigurationClient:
    """Calls ``GET /api/v1/device/configuration`` once and returns a typed result.

    Construct with the validated Backend base URL, an explicit timeout, and the maximum enabled-
    camera count this Agent will accept (``settings.device_config_max_cameras`` — enforced here,
    before the caller's own pipeline-relevance validation, as a response-size/complexity guard).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        max_cameras: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = build_configuration_url(base_url)
        self._origin = _origin_of(base_url)
        self._timeout = timeout_seconds
        self._max_cameras = max_cameras
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(follow_redirects=False)
            self._owns_client = True

    async def fetch(
        self, device_id: str, shared_secret: SecretStr
    ) -> DeviceConfiguration | ConfigurationFetchFailure:
        """Fetch this Device's current configuration.

        Never retried; ``asyncio.CancelledError`` propagates unchanged. A timeout or transport
        failure returns :class:`~weapon_detection_agent.configuration.models.
        ConfigurationFetchFailure` rather than raising.
        """
        _LOGGER.info("device_config_fetch_started", extra={"backend_origin": self._origin})

        try:
            response = await self._client.get(
                self._url,
                headers={
                    DEVICE_ID_HEADER: device_id,
                    DEVICE_SECRET_HEADER: shared_secret.get_secret_value(),
                },
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            _LOGGER.warning(
                "device_config_fetch_failed",
                extra={
                    "backend_origin": self._origin,
                    "reason": ConfigurationFailureReason.TIMEOUT.value,
                },
            )
            return ConfigurationFetchFailure(ConfigurationFailureReason.TIMEOUT)
        except httpx.TransportError:
            _LOGGER.warning(
                "device_config_fetch_failed",
                extra={
                    "backend_origin": self._origin,
                    "reason": ConfigurationFailureReason.TRANSPORT_FAILURE.value,
                },
            )
            return ConfigurationFetchFailure(ConfigurationFailureReason.TRANSPORT_FAILURE)

        return self._classify(response)

    async def aclose(self) -> None:
        """Close the underlying HTTPX client if this client owns it (no-op for an injected one)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> DeviceConfigurationClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _classify(
        self, response: httpx.Response
    ) -> DeviceConfiguration | ConfigurationFetchFailure:
        status = response.status_code

        if status == _HTTP_OK:
            return self._parse_success(response)

        if status == _HTTP_UNAUTHORIZED:
            # Never locks or reactivates here (FS-11 §5) — treated exactly like a 5xx/timeout.
            _LOGGER.warning(
                "device_config_fetch_failed",
                extra={"status": status, "reason": ConfigurationFailureReason.UNAUTHORIZED.value},
            )
            return ConfigurationFetchFailure(ConfigurationFailureReason.UNAUTHORIZED, status)

        if status == _HTTP_SERVICE_UNAVAILABLE:
            _LOGGER.warning(
                "device_config_fetch_failed",
                extra={"status": status, "reason": ConfigurationFailureReason.UNAVAILABLE.value},
            )
            return ConfigurationFetchFailure(ConfigurationFailureReason.UNAVAILABLE, status)

        _LOGGER.warning(
            "device_config_fetch_failed",
            extra={"status": status, "reason": ConfigurationFailureReason.INVALID_RESPONSE.value},
        )
        return ConfigurationFetchFailure(ConfigurationFailureReason.INVALID_RESPONSE, status)

    def _parse_success(
        self, response: httpx.Response
    ) -> DeviceConfiguration | ConfigurationFetchFailure:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            return self._invalid_response(response.status_code, reason="response_too_large")

        payload = _safe_json(response)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return self._invalid_response(response.status_code, reason="malformed_envelope")

        data = payload.get("data")
        parsed = _parse_configuration(data, max_cameras=self._max_cameras)
        if parsed is None:
            return self._invalid_response(response.status_code, reason="malformed_configuration")

        _LOGGER.info(
            "device_config_fetch_completed",
            extra={
                "status": response.status_code,
                "camera_count": len(parsed.cameras),
                "configuration_version": parsed.configuration_version,
            },
        )
        return parsed

    def _invalid_response(self, status_code: int, *, reason: str) -> ConfigurationFetchFailure:
        # The body is never logged — only the status and the safe category are.
        _LOGGER.warning(
            "device_config_fetch_failed",
            extra={
                "status": status_code,
                "reason": ConfigurationFailureReason.INVALID_RESPONSE.value,
                "detail": reason,
            },
        )
        return ConfigurationFetchFailure(ConfigurationFailureReason.INVALID_RESPONSE, status_code)


def _parse_configuration(data: object, *, max_cameras: int) -> DeviceConfiguration | None:
    """Strictly parse the response `data` object. Returns ``None`` on any shape mismatch.

    This is wire-shape parsing only — pipeline-relevance rules (uniqueness, ordering, URL scheme,
    max-camera enforcement *as a rejection* rather than a truncation) are
    :mod:`weapon_detection_agent.configuration.validation`'s job. Here, exceeding ``max_cameras`` is
    treated as a malformed/untrustworthy response (reject outright) rather than silently truncated,
    since silently dropping cameras could misassign `source_id`s.
    """
    if not isinstance(data, dict):
        return None

    try:
        schema_version = data["schemaVersion"]
        configuration_version = data["configurationVersion"]
        device_id = UUID(data["deviceId"])
        branch_id = UUID(data["branchId"])
        generated_at_utc = _parse_iso_utc(data["generatedAtUtc"])
        raw_cameras = data["cameras"]
    except (KeyError, TypeError, ValueError):
        return None

    if not isinstance(schema_version, int) or not isinstance(configuration_version, str):
        return None
    if not isinstance(raw_cameras, list) or len(raw_cameras) > max_cameras:
        return None

    cameras: list[DeviceCameraConfig] = []
    for raw_camera in raw_cameras:
        camera = _parse_camera(raw_camera)
        if camera is None:
            return None
        cameras.append(camera)

    return DeviceConfiguration(
        schema_version=schema_version,
        configuration_version=configuration_version,
        device_id=device_id,
        branch_id=branch_id,
        generated_at_utc=generated_at_utc,
        cameras=tuple(cameras),
    )


def _parse_camera(raw: object) -> DeviceCameraConfig | None:
    if not isinstance(raw, dict):
        return None
    try:
        camera_id = UUID(raw["cameraId"])
        camera_key = raw["cameraKey"]
        name = raw["name"]
        stream_url = raw["streamUrl"]
        enabled = raw["enabled"]
        source_order = raw["sourceOrder"]
        output_path = raw["outputPath"]
    except (KeyError, TypeError, ValueError):
        return None

    if (
        not isinstance(camera_key, str)
        or not isinstance(name, str)
        or not isinstance(stream_url, str)
        or not isinstance(enabled, bool)
        or not isinstance(source_order, int)
        or isinstance(source_order, bool)
        or not isinstance(output_path, str)
    ):
        return None

    return DeviceCameraConfig(
        camera_id=camera_id,
        camera_key=camera_key,
        name=name,
        stream_url=stream_url,
        enabled=enabled,
        source_order=source_order,
        output_path=output_path,
    )


def _parse_iso_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("generatedAtUtc must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def build_configuration_url(base_url: str) -> str:
    """Join the Backend base URL with the configuration path, preserving any base-path prefix."""
    return base_url.rstrip("/") + CONFIGURATION_PATH


def _origin_of(base_url: str) -> str:
    """Return a safe ``scheme://host[:port]`` origin for logging — no path, query, or userinfo."""
    url = httpx.URL(base_url)
    origin = f"{url.scheme}://{url.host}"
    if url.port is not None:
        origin += f":{url.port}"
    return origin


def _safe_json(response: httpx.Response) -> object | None:
    """Parse the response body as JSON, returning ``None`` (never raising, never logging body)."""
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
