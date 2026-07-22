"""Async HTTP client for the Backend credential-validation endpoint (IP-05 T-57, FS-02 §10.5).

Responsibility, and only this:

    Device ID + shared secret  →  one POST to /api/v1/device/credentials/validate
                                       →  classify the response  →  return a typed
                                          CredentialValidationResult (Valid / ConfirmedRejected /
                                          Indeterminate)

**Exactly one request per call, no retries.** Unlike activation (also one-shot, but because its key
is single-use), validation is idempotent — it changes nothing — but this client still makes
exactly one attempt and never retries: retry cadence is the polling loop's job (T-59), driven by
``WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS``, which this client neither knows nor uses.

Detect-only. This client never activates, never reads or requests an Activation Key, never persists
anything, never clears the local secret, and never changes operational state — those belong to
T-58–T-61. It follows no redirects (so the secret is never forwarded to another host), sends the
credentials only in the ``X-Device-Id`` / ``X-Device-Secret`` headers of a bodyless POST, and logs
only safe metadata (operation, status, classification) — never a header, the secret, or the body.
"""

from __future__ import annotations

import json
import logging
from types import TracebackType

import httpx
from pydantic import SecretStr

from weapon_detection_agent.validation.models import (
    CredentialValidationResult,
    IndeterminateReason,
)

_LOGGER = logging.getLogger("weapon_detection_agent.validation.client")

# The Backend credential-validation path (verified against DeviceCredentialValidationController.cs
# `[Route("api/v1/device/credentials")]` + `[HttpPost("validate")]`).
VALIDATION_PATH = "/api/v1/device/credentials/validate"

# The established device-authentication headers (ARCH-001 §14.1). Not new fields.
DEVICE_ID_HEADER = "X-Device-Id"
DEVICE_SECRET_HEADER = "X-Device-Secret"

# The one confirmed-rejection error code (T-52 DeviceCredentialFailure.ErrorCode). Only a 401 whose
# envelope carries exactly this code is a confirmed rejection the Agent may later lock on.
INVALID_DEVICE_CREDENTIALS_CODE = "INVALID_DEVICE_CREDENTIALS"

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401


class CredentialValidationClient:
    """Calls ``POST /api/v1/device/credentials/validate`` once and returns a typed result.

    Construct with the validated Backend base URL and an explicit timeout (the caller passes
    ``settings.backend_base_url`` and ``settings.http_timeout_seconds`` — this client never reads
    settings, the environment, or the validation interval). An HTTPX client may be injected for
    tests; an injected client is caller-owned and is **not** closed here; an internally created
    one is closed by :meth:`aclose` (or the async context manager) and is created with redirect
    following disabled.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = build_validation_url(base_url)
        self._origin = _origin_of(base_url)
        self._timeout = timeout_seconds
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            # No redirect following: a 3xx must never forward X-Device-Secret to another host.
            self._client = httpx.AsyncClient(follow_redirects=False)
            self._owns_client = True

    async def validate(
        self, device_id: str, shared_secret: SecretStr
    ) -> CredentialValidationResult:
        """Perform exactly one validation request and return the typed outcome.

        The Device ID and shared secret travel verbatim (never trimmed or case-normalized) only in
        the ``X-Device-Id`` / ``X-Device-Secret`` headers of a bodyless POST — never in the URL, a
        query string, the body, or a log. It is never retried; ``follow_redirects=False`` is
        set on the request itself so an injected client cannot re-enable it. Timeout and transport
        failures return an Indeterminate result rather than raising; only cancellation and genuine
        programmer errors propagate.
        """
        _LOGGER.info("credential_validation_started", extra={"backend_origin": self._origin})

        try:
            response = await self._client.post(
                self._url,
                headers={
                    DEVICE_ID_HEADER: device_id,
                    DEVICE_SECRET_HEADER: shared_secret.get_secret_value(),
                },
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            # TimeoutException is a subclass of TransportError, so it is handled first. No response
            # arrived, so the outcome is Indeterminate — never a confirmed rejection.
            _LOGGER.warning(
                "credential_validation_indeterminate",
                extra={"backend_origin": self._origin, "reason": IndeterminateReason.TIMEOUT.value},
            )
            return CredentialValidationResult.indeterminate(IndeterminateReason.TIMEOUT)
        except httpx.TransportError:
            _LOGGER.warning(
                "credential_validation_indeterminate",
                extra={
                    "backend_origin": self._origin,
                    "reason": IndeterminateReason.TRANSPORT_FAILURE.value,
                },
            )
            return CredentialValidationResult.indeterminate(IndeterminateReason.TRANSPORT_FAILURE)

        # asyncio.CancelledError (a BaseException, not Exception) is deliberately not caught here,
        # so a cancelled validation stays cancelled for a clean polling shutdown (T-59).
        return self._classify(response)

    async def aclose(self) -> None:
        """Close the underlying HTTPX client if this client owns it (no-op for an injected one)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CredentialValidationClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _classify(self, response: httpx.Response) -> CredentialValidationResult:
        status = response.status_code

        if status == _HTTP_OK:
            return self._classify_success(response)

        if status == _HTTP_UNAUTHORIZED:
            return self._classify_rejection(response)

        # Any other status — 3xx (redirect not followed), other 4xx (403/404/408/409/429), or 5xx —
        # is neither the affirmed success nor the confirmed rejection, so it is Indeterminate. A 5xx
        # is categorized as a server fault; everything else as an unexpected status.
        reason = (
            IndeterminateReason.SERVER_FAILURE
            if 500 <= status < 600
            else IndeterminateReason.UNEXPECTED_STATUS
        )
        _LOGGER.warning(
            "credential_validation_indeterminate",
            extra={"status": status, "reason": reason.value},
        )
        return CredentialValidationResult.indeterminate(reason, status)

    def _classify_success(self, response: httpx.Response) -> CredentialValidationResult:
        # A 200 is Valid only when it is the exact standard success envelope: success == true, no
        # contradictory errorCode, and no unexpected data (the endpoint returns no data). Any
        # malformed 200 is Indeterminate, never Valid — a 2xx alone does not mean valid.
        payload = _safe_json(response)
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or payload.get("errorCode") is not None
            or payload.get("data") is not None
        ):
            return self._invalid_response(response.status_code)

        _LOGGER.info("credential_validation_valid", extra={"status": response.status_code})
        return CredentialValidationResult.valid(response.status_code)

    def _classify_rejection(self, response: httpx.Response) -> CredentialValidationResult:
        # A 401 is a confirmed rejection only when it is the exact standard failure envelope:
        # success == false and errorCode == INVALID_DEVICE_CREDENTIALS. The message is
        # deliberately not consulted. Every other 401 shape is Indeterminate.
        payload = _safe_json(response)
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not False
            or payload.get("errorCode") != INVALID_DEVICE_CREDENTIALS_CODE
        ):
            return self._invalid_response(response.status_code)

        _LOGGER.warning(
            "credential_validation_confirmed_rejected", extra={"status": response.status_code}
        )
        return CredentialValidationResult.confirmed_rejected(response.status_code)

    def _invalid_response(self, status_code: int) -> CredentialValidationResult:
        # The body was unparseable or did not match the contract. It is never logged or in the
        # result — only the status and the safe category are.
        _LOGGER.warning(
            "credential_validation_indeterminate",
            extra={"status": status_code, "reason": IndeterminateReason.INVALID_RESPONSE.value},
        )
        return CredentialValidationResult.indeterminate(
            IndeterminateReason.INVALID_RESPONSE, status_code
        )


def build_validation_url(base_url: str) -> str:
    """Join the Backend base URL with the validation path, preserving any base-path prefix.

    Tolerates a trailing slash and never produces a duplicate slash or replaces the configured host.
    Pure string arithmetic — no DNS or connectivity check.
    """
    return base_url.rstrip("/") + VALIDATION_PATH


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
