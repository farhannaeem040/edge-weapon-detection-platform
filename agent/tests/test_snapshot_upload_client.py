"""Unit tests for the Backend snapshot upload client (IP-10 T-144, FS-08 §9/§11).

Mirrors ``test_backend_sync_client.py``'s style: exercised entirely in-memory via
``httpx.MockTransport`` — no real Backend, network port, or filesystem beyond a small temp JPEG.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from weapon_detection_agent.snapshot.client import (
    SnapshotUploadClient,
    build_snapshot_upload_url,
)
from weapon_detection_agent.snapshot.models import (
    SnapshotUploadFailure,
    SnapshotUploadFailureReason,
    SnapshotUploadOutcome,
    SnapshotUploadResult,
)
from weapon_detection_agent.sync.client import DEVICE_ID_HEADER, DEVICE_SECRET_HEADER

DEVICE_ID = "device-test-001"
SHARED_SECRET = "test-shared-secret-ZZZ"  # noqa: S105 - obvious placeholder, not a real secret
BASE_URL = "http://backend.local:5230"
ALERT_ID = "alert-0001"
EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def jpeg_file(tmp_path: Path) -> Path:
    path = tmp_path / f"{EVENT_ID}.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-body")
    return path


def _client(handler: Handler) -> tuple[SnapshotUploadClient, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_capturing))
    client = SnapshotUploadClient(BASE_URL, timeout_seconds=10.0, http_client=http_client)
    return client, captured


def _upload(
    client: SnapshotUploadClient, jpeg_file: Path
) -> SnapshotUploadResult | SnapshotUploadFailure:
    async def _run() -> SnapshotUploadResult | SnapshotUploadFailure:
        try:
            return await client.upload(
                device_id=DEVICE_ID,
                shared_secret=SecretStr(SHARED_SECRET),
                alert_id=ALERT_ID,
                event_id=EVENT_ID,
                local_path=jpeg_file,
                content_type="image/jpeg",
                sha256="a" * 64,
            )
        finally:
            await client.aclose()

    return asyncio.run(_run())


def _success_response(outcome: str) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": {"outcome": outcome}})


def test_build_snapshot_upload_url_substitutes_alert_id() -> None:
    assert (
        build_snapshot_upload_url(BASE_URL, ALERT_ID)
        == f"{BASE_URL}/api/v1/alerts/{ALERT_ID}/snapshot"
    )


def test_build_snapshot_upload_url_tolerates_trailing_slash() -> None:
    assert (
        build_snapshot_upload_url(BASE_URL + "/", ALERT_ID)
        == f"{BASE_URL}/api/v1/alerts/{ALERT_ID}/snapshot"
    )


def test_accepted_outcome_is_recognized(jpeg_file: Path) -> None:
    client, requests = _client(lambda req: _success_response("accepted"))

    result = _upload(client, jpeg_file)

    assert result == SnapshotUploadResult(SnapshotUploadOutcome.ACCEPTED)
    assert len(requests) == 1


def test_duplicate_outcome_is_recognized(jpeg_file: Path) -> None:
    client, _ = _client(lambda req: _success_response("duplicate"))

    result = _upload(client, jpeg_file)

    assert result == SnapshotUploadResult(SnapshotUploadOutcome.DUPLICATE)


def test_device_headers_carry_credentials(jpeg_file: Path) -> None:
    client, requests = _client(lambda req: _success_response("accepted"))

    _upload(client, jpeg_file)

    request = requests[0]
    assert request.headers[DEVICE_ID_HEADER] == DEVICE_ID
    assert request.headers[DEVICE_SECRET_HEADER] == SHARED_SECRET


def test_multipart_body_carries_event_id_and_sha256(jpeg_file: Path) -> None:
    client, requests = _client(lambda req: _success_response("accepted"))

    _upload(client, jpeg_file)

    body = requests[0].content
    assert str(EVENT_ID).encode() in body
    assert (b"a" * 64) in body
    assert b"\xff\xd8\xff" in body  # the JPEG bytes themselves are present, streamed


def test_401_is_a_typed_failure_not_a_lock(jpeg_file: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(401, json={"success": False}))

    result = _upload(client, jpeg_file)

    assert isinstance(result, SnapshotUploadFailure)
    assert result.reason is SnapshotUploadFailureReason.UNAUTHORIZED
    assert result.status_code == 401


def test_409_conflict_is_a_named_failure(jpeg_file: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(409, json={"success": False}))

    result = _upload(client, jpeg_file)

    assert isinstance(result, SnapshotUploadFailure)
    assert result.reason is SnapshotUploadFailureReason.CONFLICT


def test_503_is_a_server_failure(jpeg_file: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(503, json={"success": False}))

    result = _upload(client, jpeg_file)

    assert isinstance(result, SnapshotUploadFailure)
    assert result.reason is SnapshotUploadFailureReason.SERVER_FAILURE


def test_malformed_response_body_is_invalid_response(jpeg_file: Path) -> None:
    client, _ = _client(lambda req: httpx.Response(200, content=b"not json"))

    result = _upload(client, jpeg_file)

    assert isinstance(result, SnapshotUploadFailure)
    assert result.reason is SnapshotUploadFailureReason.INVALID_RESPONSE


def test_timeout_is_a_typed_failure(jpeg_file: Path) -> None:
    def _raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    client, _ = _client(_raise_timeout)

    result = _upload(client, jpeg_file)

    assert result == SnapshotUploadFailure(SnapshotUploadFailureReason.TIMEOUT)


def test_transport_error_is_a_typed_failure(jpeg_file: Path) -> None:
    def _raise_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(_raise_transport)

    result = _upload(client, jpeg_file)

    assert result == SnapshotUploadFailure(SnapshotUploadFailureReason.TRANSPORT_FAILURE)


def test_never_logs_the_shared_secret(jpeg_file: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    client, _ = _client(lambda req: _success_response("accepted"))

    _upload(client, jpeg_file)

    assert SHARED_SECRET not in caplog.text
