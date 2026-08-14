"""Async HTTP client for the Backend snapshot upload endpoint (IP-10 T-144, FS-08 §9/§11).

Mirrors :class:`~weapon_detection_agent.sync.client.BackendSyncClient` exactly: same constructor
shape (``base_url``, ``timeout_seconds``, an injectable ``httpx.AsyncClient``), the same device-auth
header constants, the same typed-result-not-exception discipline, and the same **exactly one request
per call, no retries** posture — retry cadence/backoff is :class:`~weapon_detection_agent.snapshot.
worker.SnapshotUploadWorker`'s job, not this client's.

Streams the file from disk via a file handle passed to ``httpx``'s multipart encoder rather than
reading it fully into a buffer first (FS-08 §11). Never logs secrets, image bytes, or full paths —
truncated ``EventId``/``AlertId``, byte counts, HTTP status only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import TracebackType
from uuid import UUID

import httpx
from pydantic import SecretStr

from weapon_detection_agent.snapshot.models import (
    SnapshotUploadFailure,
    SnapshotUploadFailureReason,
    SnapshotUploadOutcome,
    SnapshotUploadResult,
)
from weapon_detection_agent.sync.client import DEVICE_ID_HEADER, DEVICE_SECRET_HEADER

_LOGGER = logging.getLogger("weapon_detection_agent.snapshot.client")

# The architecturally frozen route (ARCH-001 §14.1, FS-08 §9). {alertId} is path-substituted.
_SNAPSHOT_PATH_TEMPLATE = "/api/v1/alerts/{alert_id}/snapshot"

_HTTP_CREATED = 201
_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_CONFLICT = 409

_OUTCOME_ACCEPTED = "accepted"
_OUTCOME_DUPLICATE = "duplicate"

_LOGGED_ID_PREFIX_LENGTH = 8


def build_snapshot_upload_url(base_url: str, alert_id: str) -> str:
    """Join the Backend base URL with the per-Alert snapshot path, preserving any base-path prefix.

    Pure string arithmetic — no DNS or connectivity check, mirrors
    :func:`~weapon_detection_agent.sync.client.build_sync_events_url`.
    """
    return base_url.rstrip("/") + _SNAPSHOT_PATH_TEMPLATE.format(alert_id=alert_id)


def _origin_of(base_url: str) -> str:
    url = httpx.URL(base_url)
    origin = f"{url.scheme}://{url.host}"
    if url.port is not None:
        origin += f":{url.port}"
    return origin


class SnapshotUploadClient:
    """Calls ``POST /api/v1/alerts/{alertId}/snapshot`` once and returns a typed result."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._origin = _origin_of(base_url)
        self._timeout = timeout_seconds
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(follow_redirects=False)
            self._owns_client = True

    async def upload(
        self,
        *,
        device_id: str,
        shared_secret: SecretStr,
        alert_id: str,
        event_id: UUID,
        local_path: Path,
        content_type: str,
        sha256: str,
    ) -> SnapshotUploadResult | SnapshotUploadFailure:
        """Upload the JPEG at ``local_path`` for ``event_id``/``alert_id``; return a typed outcome.

        Opens ``local_path`` for streaming read; a missing file raises :class:`FileNotFoundError`
        to the caller — the worker is responsible for the "verify file still exists" check
        *before* calling this (FS-08 §11), so this client itself does not translate that into a
        :class:`SnapshotUploadFailure` (it is not an HTTP-layer concern). Never retried; a timeout
        or transport failure returns :class:`SnapshotUploadFailure` rather than raising.
        ``asyncio.CancelledError`` propagates unchanged.
        """
        url = build_snapshot_upload_url(self._base_url, alert_id)

        _LOGGER.info(
            "snapshot_upload_request_started",
            extra={
                "backend_origin": self._origin,
                "event_id": str(event_id)[:_LOGGED_ID_PREFIX_LENGTH],
                "alert_id": alert_id[:_LOGGED_ID_PREFIX_LENGTH],
            },
        )

        with local_path.open("rb") as file_obj:
            try:
                response = await self._client.post(
                    url,
                    data={"eventId": str(event_id), "sha256": sha256},
                    files={"file": (f"{event_id}.jpg", file_obj, content_type)},
                    headers={
                        DEVICE_ID_HEADER: device_id,
                        DEVICE_SECRET_HEADER: shared_secret.get_secret_value(),
                    },
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            except httpx.TimeoutException:
                return self._failed(SnapshotUploadFailureReason.TIMEOUT, None, event_id, alert_id)
            except httpx.TransportError:
                return self._failed(
                    SnapshotUploadFailureReason.TRANSPORT_FAILURE, None, event_id, alert_id
                )

        return self._classify(response, event_id, alert_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> SnapshotUploadClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _classify(
        self, response: httpx.Response, event_id: UUID, alert_id: str
    ) -> SnapshotUploadResult | SnapshotUploadFailure:
        status = response.status_code

        if status in (_HTTP_CREATED, _HTTP_OK):
            return self._parse_success(response, status, event_id, alert_id)

        if status == _HTTP_UNAUTHORIZED:
            # Never locks/reactivates/rotates credentials here (FS-08 §11 mirrors FS-06 §6.1) —
            # treated exactly like a 5xx/timeout.
            return self._failed(
                SnapshotUploadFailureReason.UNAUTHORIZED, status, event_id, alert_id
            )

        if status == _HTTP_CONFLICT:
            return self._failed(SnapshotUploadFailureReason.CONFLICT, status, event_id, alert_id)

        reason = (
            SnapshotUploadFailureReason.SERVER_FAILURE
            if 500 <= status < 600
            else SnapshotUploadFailureReason.UNEXPECTED_STATUS
        )
        return self._failed(reason, status, event_id, alert_id)

    def _parse_success(
        self, response: httpx.Response, status: int, event_id: UUID, alert_id: str
    ) -> SnapshotUploadResult | SnapshotUploadFailure:
        payload = _safe_json(response)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return self._failed(
                SnapshotUploadFailureReason.INVALID_RESPONSE, status, event_id, alert_id
            )

        data = payload.get("data")
        outcome = data.get("outcome") if isinstance(data, dict) else None
        if outcome == _OUTCOME_ACCEPTED:
            result = SnapshotUploadResult(SnapshotUploadOutcome.ACCEPTED)
        elif outcome == _OUTCOME_DUPLICATE:
            result = SnapshotUploadResult(SnapshotUploadOutcome.DUPLICATE)
        else:
            return self._failed(
                SnapshotUploadFailureReason.INVALID_RESPONSE, status, event_id, alert_id
            )

        _LOGGER.info(
            "snapshot_upload_request_completed",
            extra={
                "status": status,
                "outcome": result.outcome.value,
                "event_id": str(event_id)[:_LOGGED_ID_PREFIX_LENGTH],
            },
        )
        return result

    def _failed(
        self,
        reason: SnapshotUploadFailureReason,
        status: int | None,
        event_id: UUID,
        alert_id: str,
    ) -> SnapshotUploadFailure:
        _LOGGER.warning(
            "snapshot_upload_request_failed",
            extra={
                "backend_origin": self._origin,
                "status": status,
                "reason": reason.value,
                "event_id": str(event_id)[:_LOGGED_ID_PREFIX_LENGTH],
                "alert_id": alert_id[:_LOGGED_ID_PREFIX_LENGTH],
            },
        )
        return SnapshotUploadFailure(reason, status)


def _safe_json(response: httpx.Response) -> object | None:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
