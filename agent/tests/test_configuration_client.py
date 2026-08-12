"""Unit tests for the Device configuration client (FS-11 §3/§5, IP-13 T-230).

Mirrors ``test_backend_sync_client.py``'s style: exercised entirely in-memory via
``httpx.MockTransport`` — no real Backend, network port, SQLite, or filesystem. Async test bodies
are wrapped in ``asyncio.run(...)`` (this project has no pytest-asyncio plugin installed), matching
every other async client test file's own convention.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from weapon_detection_agent.configuration.client import (
    CONFIGURATION_PATH,
    DEVICE_ID_HEADER,
    DEVICE_SECRET_HEADER,
    DeviceConfigurationClient,
    build_configuration_url,
)
from weapon_detection_agent.configuration.models import (
    ConfigurationFailureReason,
    ConfigurationFetchFailure,
    DeviceConfiguration,
)

DEVICE_ID = "device-test-001"
SHARED_SECRET = "test-shared-secret-ZZZ"  # noqa: S105 - obvious placeholder, not a real secret
BASE_URL = "http://backend.local:5230"
CAMERA_ID = "2613b331-8783-4d51-903a-3e41a979a14c"
BRANCH_ID = "9b6796f7-b2f2-47f3-a2df-a06bf94c1345"
FULL_DEVICE_ID = "965032b6-26af-4506-81f9-2e7307290fa1"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(
    handler: Handler, *, max_cameras: int = 8, timeout: float = 10.0
) -> tuple[DeviceConfigurationClient, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_capturing)
    http_client = httpx.AsyncClient(transport=transport)
    client = DeviceConfigurationClient(
        BASE_URL, timeout_seconds=timeout, max_cameras=max_cameras, http_client=http_client
    )
    return client, captured


def _success_body(cameras: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "schemaVersion": 1,
            "configurationVersion": "abc123",
            "deviceId": FULL_DEVICE_ID,
            "branchId": BRANCH_ID,
            "generatedAtUtc": "2026-07-30T12:00:00Z",
            "cameras": cameras,
        },
    }


def _camera(**overrides: object) -> dict[str, object]:
    base = {
        "cameraId": CAMERA_ID,
        "cameraKey": CAMERA_ID,
        "name": "Front Camera",
        "streamUrl": "rtsp://camera.example.invalid:554/stream1",
        "enabled": True,
        "sourceOrder": 0,
        "outputPath": f"cameras/{CAMERA_ID}",
    }
    base.update(overrides)
    return base


def test_build_configuration_url_joins_base_and_path() -> None:
    assert build_configuration_url("http://host:1234") == f"http://host:1234{CONFIGURATION_PATH}"
    assert build_configuration_url("http://host:1234/") == f"http://host:1234{CONFIGURATION_PATH}"


def test_fetch_sends_device_headers_never_a_body() -> None:
    async def _run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.headers[DEVICE_ID_HEADER] == DEVICE_ID
            assert request.headers[DEVICE_SECRET_HEADER] == SHARED_SECRET
            return httpx.Response(200, json=_success_body([_camera()]))

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, DeviceConfiguration)
        assert len(result.cameras) == 1
        assert str(result.cameras[0].camera_id) == CAMERA_ID
        assert result.configuration_version == "abc123"

    asyncio.run(_run())


def test_fetch_valid_response_orders_cameras_as_received() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_success_body(
                    [_camera(sourceOrder=0), _camera(cameraId=BRANCH_ID, sourceOrder=1)]
                ),
            )

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, DeviceConfiguration)
        assert [c.source_order for c in result.cameras] == [0, 1]

    asyncio.run(_run())


def test_fetch_401_returns_unauthorized_never_locks() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"success": False, "errorCode": "INVALID_DEVICE_CREDENTIALS"}
            )

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, ConfigurationFetchFailure)
        assert result.reason is ConfigurationFailureReason.UNAUTHORIZED
        assert result.status_code == 401

    asyncio.run(_run())


def test_fetch_503_returns_unavailable() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503, json={"success": False, "errorCode": "DEVICE_AUTHENTICATION_UNAVAILABLE"}
            )

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, ConfigurationFetchFailure)
        assert result.reason is ConfigurationFailureReason.UNAVAILABLE

    asyncio.run(_run())


def test_fetch_malformed_envelope_is_invalid_response() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "data": {"nope": True}})

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, ConfigurationFetchFailure)
        assert result.reason is ConfigurationFailureReason.INVALID_RESPONSE

    asyncio.run(_run())


def test_fetch_more_cameras_than_max_is_rejected_not_truncated() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_success_body([_camera(sourceOrder=i) for i in range(3)])
            )

        client, _ = _client(handler, max_cameras=2)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, ConfigurationFetchFailure)
        assert result.reason is ConfigurationFailureReason.INVALID_RESPONSE

    asyncio.run(_run())


def test_fetch_timeout_returns_failure_not_raise() -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("boom")

        client, _ = _client(handler)
        result = await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        assert isinstance(result, ConfigurationFetchFailure)
        assert result.reason is ConfigurationFailureReason.TIMEOUT

    asyncio.run(_run())


def test_fetch_cancellation_propagates_and_is_not_converted_to_a_result() -> None:
    async def _run() -> None:
        def _raise_cancel(_: httpx.Request) -> httpx.Response:
            raise asyncio.CancelledError

        client, _ = _client(_raise_cancel)

        with pytest.raises(asyncio.CancelledError):
            await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

    asyncio.run(_run())


def test_fetch_never_logs_stream_url_or_secret(caplog: pytest.LogCaptureFixture) -> None:
    async def _run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_body([_camera()]))

        client, _ = _client(handler)
        with caplog.at_level("DEBUG"):
            await client.fetch(DEVICE_ID, SecretStr(SHARED_SECRET))

        for record in caplog.records:
            message = record.getMessage()
            assert SHARED_SECRET not in message
            assert "rtsp://camera.example.invalid" not in message

    asyncio.run(_run())
