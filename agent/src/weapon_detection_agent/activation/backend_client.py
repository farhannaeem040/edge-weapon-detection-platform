"""Async HTTP client for the Backend activation endpoint (IP-02 T-37, §11).

Responsibility, and only this:

    Activation Key  →  one HTTP request to POST /api/v1/activate  →  validate the response
                    →  return a typed ActivationResult (or raise a typed, safe error)

**Exactly one request per call, no retries.** ``POST /api/v1/activate`` consumes a one-time
Activation Key and is **not idempotent**: a timeout or transport failure may occur *after* the
Backend has committed the activation but *before* the Agent sees the response, so an automatic retry
of the same key would be rejected as ``INVALID_ACTIVATION_KEY`` and hide that the original attempt
succeeded. Automatic retries for this endpoint are therefore prohibited (IP-02 §14, as amended by
the approved T-37 decision); surfacing an ambiguous outcome and requiring a fresh key belongs to the
activation orchestration (T-38). The general timeout/backoff shape (ARCH-001 §23) applies only to
future *idempotent* operations (heartbeat, config retrieval, alert sync).

Boundaries (T-37 owns none of these): it does not decide whether activation is required, read the
Activation Key from env/file, persist identity, replace a secret, compare Device IDs, initialize
SQLite, provision directories, configure logging globally, or wire into FastAPI startup. It performs
no import-time I/O and keeps no module-level client.
"""

from __future__ import annotations

import json
import logging
from types import TracebackType

import httpx
from pydantic import SecretStr

from weapon_detection_agent.activation.errors import (
    ActivationRejectedError,
    ActivationServerError,
    ActivationTimeoutError,
    ActivationTransportError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.models import ActivationResult

_LOGGER = logging.getLogger("weapon_detection_agent.activation.backend_client")

# The Backend activation path (verified against ActivateController.cs `[Route("api/v1/activate")]`).
ACTIVATE_PATH = "/api/v1/activate"

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401


class BackendActivationClient:
    """Calls ``POST /api/v1/activate`` once and returns a typed :class:`ActivationResult`.

    Construct with the validated Backend base URL and an explicit timeout (the caller passes
    ``settings.backend_base_url`` and ``settings.http_timeout_seconds`` — this client never reads
    settings, the environment, or a key file itself). An HTTPX client may be injected for tests; an
    injected client is caller-owned and is **not** closed by this client, while an internally
    created one is closed by :meth:`aclose` (or the async context manager).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = build_activate_url(base_url)
        self._origin = _origin_of(base_url)
        self._timeout = timeout_seconds
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient()
            self._owns_client = True

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        """Perform exactly one activation request and return the typed result.

        The complete plaintext key is sent verbatim in the JSON body only — never in the URL, a
        query string, a header, or a log. On any failure a typed, message-safe error is raised; the
        request is never retried.
        """
        _LOGGER.info("activation_request_started", extra={"backend_origin": self._origin})

        try:
            response = await self._client.post(
                self._url,
                json={"activationKey": activation_key.get_secret_value()},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            # TimeoutException is a subclass of TransportError, so it is handled first.
            _LOGGER.warning("activation_request_timed_out", extra={"backend_origin": self._origin})
            raise ActivationTimeoutError("the activation request timed out") from exc
        except httpx.TransportError as exc:
            _LOGGER.warning("activation_transport_failed", extra={"backend_origin": self._origin})
            raise ActivationTransportError(
                "the activation request could not reach the Backend"
            ) from exc

        return self._handle_response(response)

    async def aclose(self) -> None:
        """Close the underlying HTTPX client if this client owns it (no-op for an injected one)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BackendActivationClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _handle_response(self, response: httpx.Response) -> ActivationResult:
        status = response.status_code

        if status == _HTTP_OK:
            return self._parse_success(response)

        if status == _HTTP_UNAUTHORIZED:
            error_code = _safe_error_code(response)
            _LOGGER.warning(
                "activation_request_rejected",
                extra={"status": status, "error_code": error_code},
            )
            raise ActivationRejectedError(status_code=status, error_code=error_code)

        # Any other status (5xx server fault, or an unexpected 3xx/4xx) is an outcome the Agent does
        # not recognize as either success or the expected rejection. Never a raw body in the error.
        _LOGGER.warning("activation_server_error", extra={"status": status})
        raise ActivationServerError(status_code=status)

    def _parse_success(self, response: httpx.Response) -> ActivationResult:
        try:
            payload: object = response.json()
        except json.JSONDecodeError as exc:
            _LOGGER.warning("activation_response_invalid", extra={"backend_origin": self._origin})
            raise InvalidActivationResponseError(
                "the activation response body was not valid JSON"
            ) from exc

        if not isinstance(payload, dict):
            _LOGGER.warning("activation_response_invalid", extra={"backend_origin": self._origin})
            raise InvalidActivationResponseError(
                "the activation response envelope was not an object"
            )

        if payload.get("success") is not True:
            # A 200 that does not affirm success (or affirms failure) is contradictory — reject it
            # rather than trust a body the contract says should not occur.
            _LOGGER.warning("activation_response_invalid", extra={"backend_origin": self._origin})
            raise InvalidActivationResponseError("the activation response did not indicate success")

        try:
            result = ActivationResult.from_response_data(payload.get("data"))
        except ValueError as exc:
            # str(exc) names only the offending field, never a value (see models helper).
            _LOGGER.warning("activation_response_invalid", extra={"backend_origin": self._origin})
            raise InvalidActivationResponseError(str(exc)) from exc

        _LOGGER.info(
            "activation_request_succeeded",
            extra={"device_id": result.device_id, "branch_id": result.branch_id},
        )
        return result


def build_activate_url(base_url: str) -> str:
    """Join the Backend base URL with the activation path, robustly.

    Supports a base URL with or without a trailing slash and preserves any base-path prefix (e.g. a
    reverse-proxy mount), without ever producing a duplicate slash or replacing the configured host.
    Pure string arithmetic — no DNS or connectivity check.
    """
    return base_url.rstrip("/") + ACTIVATE_PATH


def _origin_of(base_url: str) -> str:
    """Return a safe ``scheme://host[:port]`` origin for logging — no path, query, or userinfo."""
    url = httpx.URL(base_url)
    origin = f"{url.scheme}://{url.host}"
    if url.port is not None:
        origin += f":{url.port}"
    return origin


def _safe_error_code(response: httpx.Response) -> str | None:
    """Best-effort extraction of the envelope's ``errorCode`` (a safe public enum value).

    Returns ``None`` rather than raising if the body is missing or unparseable — the caller already
    has the status code, and the raw body must never surface.
    """
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        code = payload.get("errorCode")
        if isinstance(code, str):
            return code
    return None
