"""Async HTTP client for the Backend detection-event sync endpoint (IP-08 T-105, FS-06 §7.3/§4).

Responsibility, and only this:

    a bounded batch of already-persisted DetectionEvent rows  →  one POST to
    /api/v1/sync/events, X-Device-Id + X-Device-Secret  →  parse the response envelope strictly
    →  return a typed per-event result
    (:class:`~weapon_detection_agent.sync.models.SyncBatchResult`) or a typed whole-batch failure
    (:class:`~weapon_detection_agent.sync.models.SyncBatchFailure`)

Mirrors :class:`~weapon_detection_agent.validation.client.CredentialValidationClient`'s construction
(``base_url``, ``timeout_seconds``, an injectable ``httpx.AsyncClient``), header constants, and
strict-envelope-before-trust discipline. **Exactly one request per call, no retries** — retry
cadence and backoff are the outbox worker's job (FS-06 §7.4/§9), not this client's.

This client never touches SQLite, never decides what is "pending," and never locks or reactivates
the Agent on a ``401`` — a sync-endpoint ``401`` is reported as an ordinary
:class:`~weapon_detection_agent.sync.models.SyncBatchFailure`, exactly like a ``500`` or a timeout
(FS-06 §6.1); only the dedicated
:class:`~weapon_detection_agent.validation.client.CredentialValidationClient` may ever confirm a
revocation. It logs only batch size and outcome counts — never ``X-Device-Secret``, full headers, or
full request/response JSON (FS-06 §9).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from types import TracebackType
from uuid import UUID

import httpx
from pydantic import SecretStr

from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.persistence.models import to_iso_utc
from weapon_detection_agent.sync.models import (
    SyncBatchFailure,
    SyncBatchResult,
    SyncEventQuotaInfo,
    SyncFailureReason,
)

_LOGGER = logging.getLogger("weapon_detection_agent.sync.client")

# The Backend detection-event sync path (ARCH-001 §14.1, FS-06 §4 — frozen).
SYNC_EVENTS_PATH = "/api/v1/sync/events"

# The established device-authentication headers (ARCH-001 §14.1). Not new fields.
DEVICE_ID_HEADER = "X-Device-Id"
DEVICE_SECRET_HEADER = "X-Device-Secret"

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401

_OUTCOME_ACCEPTED = "accepted"
_OUTCOME_DUPLICATE = "duplicate"
_OUTCOME_REJECTED = "rejected"
# FS-09 §5: the Backend's daily-quota-suppression outcome — a successful, terminal policy outcome,
# never an error and never retried (distinct from _OUTCOME_REJECTED, which stays pending).
_OUTCOME_QUOTA_EXCEEDED = "quota_exceeded"


class BackendSyncClient:
    """Calls ``POST /api/v1/sync/events`` once and returns a typed result.

    Construct with the validated Backend base URL and an explicit timeout (the caller passes
    ``settings.backend_base_url`` and ``settings.http_timeout_seconds`` — FS-06 §10 reuses
    ``http_timeout_seconds`` rather than adding a duplicate). An HTTPX client may be injected for
    tests; an injected client is caller-owned and is **not** closed here; an internally created one
    is closed by :meth:`aclose` (or the async context manager) and is created with redirect
    following disabled (a 3xx must never forward ``X-Device-Secret`` to another host).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = build_sync_events_url(base_url)
        self._origin = _origin_of(base_url)
        self._timeout = timeout_seconds
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(follow_redirects=False)
            self._owns_client = True

    async def send_batch(
        self,
        *,
        device_id: str,
        shared_secret: SecretStr,
        events: Sequence[DetectionEvent],
    ) -> SyncBatchResult | SyncBatchFailure:
        """Send ``events`` as one batch and return the typed outcome.

        The Device ID and shared secret travel only in the ``X-Device-Id``/``X-Device-Secret``
        headers of the POST — never in the URL, a query string, or the body. Never retried; a
        timeout or transport failure returns :class:`~weapon_detection_agent.sync.models.
        SyncBatchFailure` rather than raising. ``asyncio.CancelledError`` propagates unchanged.
        """
        body = {"events": [_serialize_event(event) for event in events]}

        _LOGGER.info(
            "detection_sync_request_started",
            extra={"backend_origin": self._origin, "batch_size": len(events)},
        )

        try:
            response = await self._client.post(
                self._url,
                json=body,
                headers={
                    DEVICE_ID_HEADER: device_id,
                    DEVICE_SECRET_HEADER: shared_secret.get_secret_value(),
                },
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            # TimeoutException is a subclass of TransportError, so it is handled first.
            _LOGGER.warning(
                "detection_sync_request_failed",
                extra={
                    "backend_origin": self._origin,
                    "reason": SyncFailureReason.TIMEOUT.value,
                    "batch_size": len(events),
                },
            )
            return SyncBatchFailure(SyncFailureReason.TIMEOUT)
        except httpx.TransportError:
            _LOGGER.warning(
                "detection_sync_request_failed",
                extra={
                    "backend_origin": self._origin,
                    "reason": SyncFailureReason.TRANSPORT_FAILURE.value,
                    "batch_size": len(events),
                },
            )
            return SyncBatchFailure(SyncFailureReason.TRANSPORT_FAILURE)

        return self._classify(response, len(events))

    async def aclose(self) -> None:
        """Close the underlying HTTPX client if this client owns it (no-op for an injected one)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BackendSyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _classify(
        self, response: httpx.Response, batch_size: int
    ) -> SyncBatchResult | SyncBatchFailure:
        status = response.status_code

        if status == _HTTP_OK:
            return self._parse_success(response, batch_size)

        if status == _HTTP_UNAUTHORIZED:
            # Never locks or reactivates here (FS-06 §6.1) — treated exactly like a 5xx/timeout.
            _LOGGER.warning(
                "detection_sync_request_failed",
                extra={
                    "status": status,
                    "reason": SyncFailureReason.UNAUTHORIZED.value,
                    "batch_size": batch_size,
                },
            )
            return SyncBatchFailure(SyncFailureReason.UNAUTHORIZED, status)

        reason = (
            SyncFailureReason.SERVER_FAILURE
            if 500 <= status < 600
            else SyncFailureReason.UNEXPECTED_STATUS
        )
        _LOGGER.warning(
            "detection_sync_request_failed",
            extra={"status": status, "reason": reason.value, "batch_size": batch_size},
        )
        return SyncBatchFailure(reason, status)

    def _parse_success(
        self, response: httpx.Response, batch_size: int
    ) -> SyncBatchResult | SyncBatchFailure:
        payload = _safe_json(response)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return self._invalid_response(response.status_code, batch_size)

        data = payload.get("data")
        if not isinstance(data, dict):
            return self._invalid_response(response.status_code, batch_size)

        results = data.get("results")
        if not isinstance(results, list):
            return self._invalid_response(response.status_code, batch_size)

        accepted, duplicate, rejected, quota_exceeded, alert_ids = _parse_results(results)

        _LOGGER.info(
            "detection_sync_request_completed",
            extra={
                "status": response.status_code,
                "batch_size": batch_size,
                "accepted": len(accepted),
                "duplicate": len(duplicate),
                "rejected": len(rejected),
                "quota_exceeded": len(quota_exceeded),
            },
        )
        return SyncBatchResult(
            accepted=frozenset(accepted),
            duplicate=frozenset(duplicate),
            rejected=rejected,
            quota_exceeded=quota_exceeded,
            alert_ids=alert_ids,
        )

    def _invalid_response(self, status_code: int, batch_size: int) -> SyncBatchFailure:
        # The body was unparseable or did not match the contract. It is never logged — only the
        # status and the safe category are.
        _LOGGER.warning(
            "detection_sync_request_failed",
            extra={
                "status": status_code,
                "reason": SyncFailureReason.INVALID_RESPONSE.value,
                "batch_size": batch_size,
            },
        )
        return SyncBatchFailure(SyncFailureReason.INVALID_RESPONSE, status_code)


def _parse_results(
    results: list[object],
) -> tuple[set[UUID], set[UUID], dict[UUID, str], dict[UUID, SyncEventQuotaInfo], dict[UUID, str]]:
    """Classify each response item, discarding anything malformed or ambiguous.

    An ``EventId`` named more than once in ``results`` is ambiguous — its outcome is dropped from
    every collection rather than guessed, so it stays pending (FS-06 §4.2: "duplicated ... response
    EventIds leave the corresponding rows pending"). An item with a missing/malformed ``eventId`` or
    an unrecognized ``outcome`` is silently skipped for the same reason — never crashes the batch.

    ``alert_ids`` (IP-10 T-143, FS-08 §11 — additive) collects a string ``alertId`` for any
    accepted/duplicate item that carries one; its absence on any item is never an error — a Backend
    response that omits it (or an older Backend) simply contributes no association for that item.

    ``quota_exceeded`` (FS-09 §5, IP-11 T-180) collects the Backend's quota context for any item
    with that outcome; a malformed/missing ``quota`` object still counts the event as
    quota-suppressed (the outcome name alone is authoritative) but with a best-effort/empty
    :class:`~weapon_detection_agent.sync.models.SyncEventQuotaInfo` — the worker's terminal-state
    decision never depends on these values.
    """
    accepted: set[UUID] = set()
    duplicate: set[UUID] = set()
    rejected: dict[UUID, str] = {}
    quota_exceeded: dict[UUID, SyncEventQuotaInfo] = {}
    alert_ids: dict[UUID, str] = {}
    seen: set[UUID] = set()
    ambiguous: set[UUID] = set()

    for item in results:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("eventId")
        if not isinstance(raw_id, str):
            continue
        try:
            event_id = UUID(raw_id)
        except ValueError:
            continue
        if event_id in seen:
            ambiguous.add(event_id)
            continue
        seen.add(event_id)

        outcome = item.get("outcome")
        if outcome == _OUTCOME_ACCEPTED:
            accepted.add(event_id)
        elif outcome == _OUTCOME_DUPLICATE:
            duplicate.add(event_id)
        elif outcome == _OUTCOME_REJECTED:
            error_code = item.get("errorCode")
            rejected[event_id] = error_code if isinstance(error_code, str) else "UNKNOWN"
        elif outcome == _OUTCOME_QUOTA_EXCEEDED:
            quota_exceeded[event_id] = _parse_quota_info(item.get("quota"))
        # any other/missing outcome: leave the event unacknowledged (still pending)

        if outcome in (_OUTCOME_ACCEPTED, _OUTCOME_DUPLICATE):
            alert_id = item.get("alertId")
            if isinstance(alert_id, str) and alert_id:
                alert_ids[event_id] = alert_id

    for event_id in ambiguous:
        accepted.discard(event_id)
        duplicate.discard(event_id)
        rejected.pop(event_id, None)
        quota_exceeded.pop(event_id, None)
        alert_ids.pop(event_id, None)

    return accepted, duplicate, rejected, quota_exceeded, alert_ids


def _parse_quota_info(raw_quota: object) -> SyncEventQuotaInfo:
    """Best-effort parse of a ``quota_exceeded`` item's ``quota`` object (FS-09 §5).

    A missing/malformed value never blocks classifying the event as quota-suppressed — only the
    diagnostic fields fall back to safe defaults.
    """
    if not isinstance(raw_quota, dict):
        return SyncEventQuotaInfo(maximum=0, local_date="")

    maximum = raw_quota.get("maximum")
    local_date = raw_quota.get("localDate")
    return SyncEventQuotaInfo(
        maximum=maximum if isinstance(maximum, int) else 0,
        local_date=local_date if isinstance(local_date, str) else "",
    )


def build_sync_events_url(base_url: str) -> str:
    """Join the Backend base URL with the sync path, preserving any base-path prefix.

    Tolerates a trailing slash and never produces a duplicate slash or replaces the configured host.
    Pure string arithmetic — no DNS or connectivity check.
    """
    return base_url.rstrip("/") + SYNC_EVENTS_PATH


def _origin_of(base_url: str) -> str:
    """Return a safe ``scheme://host[:port]`` origin for logging — no path, query, or userinfo."""
    url = httpx.URL(base_url)
    origin = f"{url.scheme}://{url.host}"
    if url.port is not None:
        origin += f":{url.port}"
    return origin


def _safe_json(response: httpx.Response) -> object | None:
    """Parse the response body as JSON, returning ``None`` (never raising, never logging the body).

    A missing, empty, or malformed body simply yields ``None`` so the caller classifies it as an
    invalid response — the raw content never surfaces in a log, an error, or the result.
    """
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _serialize_event(event: DetectionEvent) -> dict[str, object]:
    """Render one ``DetectionEvent`` as the FS-06 §4.1 wire shape (camelCase, no SnapshotReference).

    Raises :class:`ValueError` if ``event.created_at_utc`` is unset — every event this client is
    asked to send must have come from :meth:`~weapon_detection_agent.persistence.
    detection_event_repository.DetectionEventRepository.list_pending`, which always populates it; a
    ``None`` here is a caller bug, not a wire-trust concern (FS-05 identity fields are never read
    from any external payload in the first place).
    """
    if event.created_at_utc is None:
        raise ValueError("event has no created_at_utc and cannot be synced")

    return {
        "eventId": str(event.event_id),
        "cameraId": event.camera_id,
        "detectedAtUtc": to_iso_utc(event.detected_at_utc),
        "createdAtUtc": to_iso_utc(event.created_at_utc),
        "classId": event.class_id,
        "className": event.class_name,
        "confidence": event.confidence,
        "sourceId": event.source_id,
        "frameNumber": event.frame_number,
        "frameWidth": event.frame_width,
        "frameHeight": event.frame_height,
        "boundingBox": {
            "left": event.bbox_left,
            "top": event.bbox_top,
            "width": event.bbox_width,
            "height": event.bbox_height,
        },
    }
