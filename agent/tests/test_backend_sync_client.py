"""Unit tests for the Backend detection-event sync client (IP-08 T-105, FS-06 §7.3/§4).

Mirrors ``test_credential_validation_client.py``'s style: exercised entirely in-memory via
``httpx.MockTransport`` — no real Backend, network port, SQLite, or filesystem.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.sync.client import (
    DEVICE_ID_HEADER,
    DEVICE_SECRET_HEADER,
    SYNC_EVENTS_PATH,
    BackendSyncClient,
    build_sync_events_url,
)
from weapon_detection_agent.sync.models import (
    SyncBatchFailure,
    SyncBatchResult,
    SyncEventQuotaInfo,
    SyncFailureReason,
)

DEVICE_ID = "device-test-001"
SHARED_SECRET = "test-shared-secret-ZZZ"  # noqa: S105 - obvious placeholder, not a real secret
BASE_URL = "http://backend.local:5230"

EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
OTHER_EVENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

Handler = Callable[[httpx.Request], httpx.Response]


def _event(*, event_id: UUID = EVENT_ID, created_at_utc: datetime | None = None) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        device_id=DEVICE_ID,
        camera_id="camera1",
        source_id=0,
        class_id=0,
        class_name="gun",
        confidence=0.91,
        frame_number=12345,
        detected_at_utc=datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc),
        frame_width=640,
        frame_height=640,
        bbox_left=210.0,
        bbox_top=130.0,
        bbox_width=95.0,
        bbox_height=70.0,
        created_at_utc=created_at_utc or datetime(2026, 7, 24, 18, 30, 1, tzinfo=timezone.utc),
    )


def _client(
    handler: Handler, *, base_url: str = BASE_URL, timeout: float = 10.0
) -> tuple[BackendSyncClient, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_capturing))
    client = BackendSyncClient(base_url, timeout_seconds=timeout, http_client=http_client)
    return client, captured


def _send(
    client: BackendSyncClient,
    events: list[DetectionEvent] | None = None,
    *,
    device_id: str = DEVICE_ID,
    secret: str = SHARED_SECRET,
) -> SyncBatchResult | SyncBatchFailure:
    events = events if events is not None else [_event()]

    async def _run() -> SyncBatchResult | SyncBatchFailure:
        try:
            return await client.send_batch(
                device_id=device_id, shared_secret=SecretStr(secret), events=events
            )
        finally:
            await client.aclose()

    return asyncio.run(_run())


def _accepted_response(*event_ids: UUID) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "results": [
                    {"eventId": str(eid), "outcome": "accepted", "alertId": str(uuid4())}
                    for eid in event_ids
                ]
            },
        },
    )


# --- URL construction --------------------------------------------------------------------------


def test_build_url_without_trailing_slash() -> None:
    assert build_sync_events_url("http://host:5230") == "http://host:5230" + SYNC_EVENTS_PATH


def test_build_url_with_trailing_slash_has_no_double_slash() -> None:
    assert build_sync_events_url("http://host:5230/") == "http://host:5230" + SYNC_EVENTS_PATH


# --- Request shape / headers --------------------------------------------------------------------


def test_request_is_a_post_with_the_credential_headers() -> None:
    client, captured = _client(lambda r: _accepted_response(EVENT_ID))

    _send(client)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == SYNC_EVENTS_PATH
    assert request.headers[DEVICE_ID_HEADER] == DEVICE_ID
    assert request.headers[DEVICE_SECRET_HEADER] == SHARED_SECRET
    assert DEVICE_ID_HEADER == "X-Device-Id"
    assert DEVICE_SECRET_HEADER == "X-Device-Secret"
    assert SHARED_SECRET not in str(request.url)


def test_request_body_matches_the_fs06_wire_shape() -> None:
    client, captured = _client(lambda r: _accepted_response(EVENT_ID))
    event = _event()

    _send(client, [event])

    body = captured[0].content
    import json as _json

    payload = _json.loads(body)
    (wire_event,) = payload["events"]
    assert wire_event["eventId"] == str(EVENT_ID)
    assert wire_event["cameraId"] == "camera1"
    assert wire_event["classId"] == 0
    assert wire_event["className"] == "gun"
    assert wire_event["confidence"] == 0.91
    assert wire_event["sourceId"] == 0
    assert wire_event["frameNumber"] == 12345
    assert wire_event["frameWidth"] == 640
    assert wire_event["frameHeight"] == 640
    assert wire_event["boundingBox"] == {
        "left": 210.0,
        "top": 130.0,
        "width": 95.0,
        "height": 70.0,
    }
    assert "detectedAtUtc" in wire_event
    assert "createdAtUtc" in wire_event
    assert "snapshotReference" not in wire_event
    assert "deliveryStatus" not in wire_event


def test_event_without_created_at_utc_raises() -> None:
    client, _ = _client(lambda r: _accepted_response(EVENT_ID))
    naked = DetectionEvent(
        event_id=EVENT_ID,
        device_id=DEVICE_ID,
        camera_id="camera1",
        source_id=0,
        class_id=0,
        class_name="gun",
        confidence=0.9,
        frame_number=1,
        detected_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        frame_width=640,
        frame_height=480,
        bbox_left=0.0,
        bbox_top=0.0,
        bbox_width=10.0,
        bbox_height=10.0,
    )

    with pytest.raises(ValueError, match="created_at_utc"):
        _send(client, [naked])


# --- Successful envelope parsing -----------------------------------------------------------------


def test_accepted_outcome_is_reported() -> None:
    client, _ = _client(lambda r: _accepted_response(EVENT_ID))

    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert result.accepted == {EVENT_ID}
    assert result.duplicate == frozenset()
    assert result.rejected == {}


def test_duplicate_outcome_is_reported() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"results": [{"eventId": str(EVENT_ID), "outcome": "duplicate"}]},
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert result.duplicate == {EVENT_ID}


def test_rejected_outcome_carries_error_code() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {"eventId": str(EVENT_ID), "outcome": "rejected", "errorCode": "BAD_DATA"}
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert result.rejected == {EVENT_ID: "BAD_DATA"}


def test_partial_batch_mixed_outcomes() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {"eventId": str(EVENT_ID), "outcome": "accepted"},
                        {"eventId": str(OTHER_EVENT_ID), "outcome": "rejected", "errorCode": "X"},
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(client, [_event(event_id=EVENT_ID), _event(event_id=OTHER_EVENT_ID)])

    assert isinstance(result, SyncBatchResult)
    assert result.accepted == {EVENT_ID}
    assert result.rejected == {OTHER_EVENT_ID: "X"}


def test_missing_event_id_in_response_is_simply_absent_from_every_set() -> None:
    client, _ = _client(lambda r: _accepted_response())  # empty results
    result = _send(client, [_event(event_id=EVENT_ID)])

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID not in result.accepted
    assert EVENT_ID not in result.duplicate
    assert EVENT_ID not in result.rejected


def test_duplicated_event_id_in_response_leaves_it_unacknowledged() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {"eventId": str(EVENT_ID), "outcome": "accepted"},
                        {"eventId": str(EVENT_ID), "outcome": "rejected", "errorCode": "X"},
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID not in result.accepted
    assert EVENT_ID not in result.duplicate
    assert EVENT_ID not in result.rejected


def test_malformed_event_id_in_response_is_skipped() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"results": [{"eventId": "not-a-uuid", "outcome": "accepted"}]},
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert result.accepted == frozenset()


def test_quota_exceeded_outcome_is_collected_with_its_quota_info() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {
                            "eventId": str(EVENT_ID),
                            "outcome": "quota_exceeded",
                            "alertId": None,
                            "errorCode": "BRANCH_DAILY_ALERT_QUOTA_REACHED",
                            "quota": {"maximum": 15, "localDate": "2026-07-29"},
                        }
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID not in result.accepted
    assert EVENT_ID not in result.duplicate
    assert EVENT_ID not in result.rejected
    assert result.quota_exceeded == {
        EVENT_ID: SyncEventQuotaInfo(maximum=15, local_date="2026-07-29")
    }


def test_quota_exceeded_outcome_never_populates_alert_ids() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {
                            "eventId": str(EVENT_ID),
                            "outcome": "quota_exceeded",
                            "quota": {"maximum": 15, "localDate": "2026-07-29"},
                        }
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID not in result.alert_ids


def test_quota_exceeded_outcome_with_missing_quota_object_still_classifies_the_event() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"results": [{"eventId": str(EVENT_ID), "outcome": "quota_exceeded"}]},
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID in result.quota_exceeded


def test_mixed_batch_can_contain_accepted_duplicate_rejected_and_quota_exceeded() -> None:
    duplicate_id = uuid4()
    rejected_id = uuid4()

    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "results": [
                        {"eventId": str(EVENT_ID), "outcome": "accepted"},
                        {"eventId": str(duplicate_id), "outcome": "duplicate"},
                        {"eventId": str(rejected_id), "outcome": "rejected", "errorCode": "X"},
                        {
                            "eventId": str(OTHER_EVENT_ID),
                            "outcome": "quota_exceeded",
                            "quota": {"maximum": 15, "localDate": "2026-07-29"},
                        },
                    ]
                },
            },
        )

    client, _ = _client(_handler)
    result = _send(
        client,
        [
            _event(event_id=EVENT_ID),
            _event(event_id=duplicate_id),
            _event(event_id=rejected_id),
            _event(event_id=OTHER_EVENT_ID),
        ],
    )

    assert isinstance(result, SyncBatchResult)
    assert result.accepted == {EVENT_ID}
    assert result.duplicate == {duplicate_id}
    assert result.rejected == {rejected_id: "X"}
    assert OTHER_EVENT_ID in result.quota_exceeded


def test_unrecognized_outcome_leaves_event_pending() -> None:
    def _handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"results": [{"eventId": str(EVENT_ID), "outcome": "something_else"}]},
            },
        )

    client, _ = _client(_handler)
    result = _send(client)

    assert isinstance(result, SyncBatchResult)
    assert EVENT_ID not in result.accepted
    assert EVENT_ID not in result.duplicate
    assert EVENT_ID not in result.rejected


# --- Failure classification (FS-06 §9) ------------------------------------------------------------


def test_401_is_a_failure_not_an_exception_and_never_locks() -> None:
    client, _ = _client(lambda r: httpx.Response(401, json={"success": False}))

    result = _send(client)

    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.UNAUTHORIZED
    assert result.status_code == 401
    # No mention of locking/reactivation anywhere in this client's public surface.
    assert not hasattr(client, "mark_reactivation_required")


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_server_failure(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status))
    result = _send(client)
    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.SERVER_FAILURE
    assert result.status_code == status


@pytest.mark.parametrize("status", [400, 413, 403, 404])
def test_other_status_is_unexpected_status(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status))
    result = _send(client)
    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.UNEXPECTED_STATUS
    assert result.status_code == status


def test_malformed_200_body_is_invalid_response_failure() -> None:
    client, _ = _client(lambda r: httpx.Response(200, content=b"not json{"))
    result = _send(client)
    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.INVALID_RESPONSE


def test_200_success_false_is_invalid_response_failure() -> None:
    client, _ = _client(lambda r: httpx.Response(200, json={"success": False}))
    result = _send(client)
    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.INVALID_RESPONSE


def test_200_missing_results_is_invalid_response_failure() -> None:
    client, _ = _client(lambda r: httpx.Response(200, json={"success": True, "data": {}}))
    result = _send(client)
    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.INVALID_RESPONSE


def test_timeout_is_a_failure_with_no_retry() -> None:
    def _raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, captured = _client(_raise_timeout)
    result = _send(client)

    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.TIMEOUT
    assert result.status_code is None
    assert len(captured) == 1


def test_transport_failure_is_a_failure_with_no_retry() -> None:
    def _raise_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, captured = _client(_raise_transport)
    result = _send(client)

    assert isinstance(result, SyncBatchFailure)
    assert result.reason is SyncFailureReason.TRANSPORT_FAILURE
    assert len(captured) == 1


def test_cancellation_propagates_and_is_not_converted_to_a_result() -> None:
    def _raise_cancel(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    client, _ = _client(_raise_cancel)

    with pytest.raises(asyncio.CancelledError):
        _send(client)


def test_redirect_is_not_followed() -> None:
    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://elsewhere.example/redirected"})

    client, captured = _client(_redirect)
    result = _send(client)

    assert isinstance(result, SyncBatchFailure)
    assert result.status_code == 302
    assert len(captured) == 1


# --- Secret safety ---------------------------------------------------------------------------


def test_logs_never_contain_the_secret_full_headers_or_full_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="weapon_detection_agent.sync.client")

    malformed_body = "MALFORMED-BODY-WITH-SECRET-" + SHARED_SECRET
    handlers: list[Handler] = [
        lambda r: _accepted_response(EVENT_ID),
        lambda r: httpx.Response(401, json={"success": False}),
        lambda r: httpx.Response(200, content=malformed_body.encode()),
        lambda r: httpx.Response(500),
    ]
    for handler in handlers:
        client, _ = _client(handler)
        _send(client)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    rendered += "\n" + "\n".join(str(record.__dict__) for record in caplog.records)

    assert SHARED_SECRET not in rendered
    assert malformed_body not in rendered
