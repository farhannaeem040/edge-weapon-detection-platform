"""Unit tests for the Backend credential-validation client (IP-05 T-57, FS-02 §10.5).

The client is exercised entirely in-memory via ``httpx.MockTransport`` — no real Backend, no network
port, no SQLite, no filesystem. Async methods run via ``asyncio.run`` so no async-test plugin
is required. Every value is an obvious placeholder; the fake secret must never appear in captured
logs, results, or errors, which the secret-safety tests assert.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from weapon_detection_agent.validation.client import (
    VALIDATION_PATH,
    CredentialValidationClient,
    build_validation_url,
)
from weapon_detection_agent.validation.models import (
    CredentialValidationResult,
    IndeterminateReason,
)

DEVICE_ID = "device-test-001"
SHARED_SECRET = "test-shared-secret-ZZZ"  # noqa: S105 - obvious placeholder, not a real secret
BASE_URL = "http://backend.local:5230"
ACTIVATE_PATH = "/api/v1/activate"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(
    handler: Handler, *, base_url: str = BASE_URL, timeout: float = 10.0
) -> tuple[CredentialValidationClient, list[httpx.Request]]:
    """Build a client backed by a MockTransport, capturing every request it makes."""
    captured: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_capturing))
    client = CredentialValidationClient(base_url, timeout_seconds=timeout, http_client=http_client)
    return client, captured


def _validate(
    client: CredentialValidationClient,
    device_id: str = DEVICE_ID,
    secret: str = SHARED_SECRET,
) -> CredentialValidationResult:
    async def _run() -> CredentialValidationResult:
        try:
            return await client.validate(device_id, SecretStr(secret))
        finally:
            await client.aclose()

    return asyncio.run(_run())


def _success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"success": True})


def _confirmed_rejection(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        401,
        json={
            "success": False,
            "message": "The device credentials are invalid.",
            "errorCode": "INVALID_DEVICE_CREDENTIALS",
        },
    )


# --- URL construction --------------------------------------------------------------------------


def test_build_url_without_trailing_slash() -> None:
    assert build_validation_url("http://host:5230") == "http://host:5230" + VALIDATION_PATH


def test_build_url_with_trailing_slash_has_no_double_slash() -> None:
    assert build_validation_url("http://host:5230/") == "http://host:5230" + VALIDATION_PATH


def test_build_url_preserves_a_base_path_prefix() -> None:
    assert (
        build_validation_url("http://host:8080/api-gateway")
        == "http://host:8080/api-gateway" + VALIDATION_PATH
    )


# --- 1. Correct request ------------------------------------------------------------------------


def test_request_is_a_single_bodyless_post_with_the_credential_headers() -> None:
    client, captured = _client(_success)

    _validate(client)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == VALIDATION_PATH
    assert request.headers["X-Device-Id"] == DEVICE_ID
    assert request.headers["X-Device-Secret"] == SHARED_SECRET
    assert request.content == b""
    # The credentials never appear in the URL or query string.
    assert DEVICE_ID not in str(request.url)
    assert SHARED_SECRET not in str(request.url)


# --- 2. Valid response -------------------------------------------------------------------------


def test_200_standard_success_is_valid() -> None:
    client, _ = _client(_success)
    result = _validate(client)
    assert result.is_valid
    assert result.status_code == 200


def test_200_success_with_explicit_null_data_is_valid() -> None:
    client, _ = _client(lambda r: httpx.Response(200, json={"success": True, "data": None}))
    assert _validate(client).is_valid


# --- 3. Malformed successful response ----------------------------------------------------------


def test_malformed_200_json_is_indeterminate() -> None:
    client, _ = _client(lambda r: httpx.Response(200, content=b"not json{"))
    result = _validate(client)
    assert result.is_indeterminate
    assert result.indeterminate_reason is IndeterminateReason.INVALID_RESPONSE


def test_200_with_success_false_is_indeterminate_not_valid() -> None:
    client, _ = _client(lambda r: httpx.Response(200, json={"success": False}))
    result = _validate(client)
    assert result.is_indeterminate
    assert not result.is_valid


def test_200_with_unexpected_data_is_indeterminate() -> None:
    client, _ = _client(lambda r: httpx.Response(200, json={"success": True, "data": {"x": 1}}))
    assert _validate(client).is_indeterminate


def test_200_with_contradictory_error_code_is_indeterminate() -> None:
    client, _ = _client(
        lambda r: httpx.Response(200, json={"success": True, "errorCode": "SOMETHING"})
    )
    assert _validate(client).is_indeterminate


# --- 4. Confirmed rejection --------------------------------------------------------------------


def test_401_with_exact_envelope_is_confirmed_rejected() -> None:
    client, _ = _client(_confirmed_rejection)
    result = _validate(client)
    assert result.is_confirmed_rejected
    assert result.status_code == 401


def test_confirmed_rejection_does_not_depend_on_message_text() -> None:
    client, _ = _client(
        lambda r: httpx.Response(
            401, json={"success": False, "errorCode": "INVALID_DEVICE_CREDENTIALS"}
        )
    )
    assert _validate(client).is_confirmed_rejected


# --- 5. Ambiguous 401 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(401),  # no body
        lambda r: httpx.Response(401, content=b"not json{"),  # malformed JSON
        lambda r: httpx.Response(401, json={"success": False}),  # missing errorCode
        lambda r: httpx.Response(401, json={"success": False, "errorCode": "OTHER_CODE"}),
        lambda r: httpx.Response(
            401, json={"success": True, "errorCode": "INVALID_DEVICE_CREDENTIALS"}
        ),  # success=true
    ],
)
def test_ambiguous_401_is_indeterminate_never_confirmed(handler: Handler) -> None:
    client, _ = _client(handler)
    result = _validate(client)
    assert result.is_indeterminate
    assert not result.is_confirmed_rejected


# --- 6. Other HTTP statuses --------------------------------------------------------------------


@pytest.mark.parametrize("status", [403, 404, 408, 409, 429])
def test_other_4xx_are_indeterminate_unexpected_status(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status))
    result = _validate(client)
    assert result.is_indeterminate
    assert not result.is_confirmed_rejected
    assert result.indeterminate_reason is IndeterminateReason.UNEXPECTED_STATUS
    assert result.status_code == status


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_are_indeterminate_server_failure(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status))
    result = _validate(client)
    assert result.is_indeterminate
    assert not result.is_confirmed_rejected
    assert result.indeterminate_reason is IndeterminateReason.SERVER_FAILURE


# --- 7. Unexpected 2xx -------------------------------------------------------------------------


@pytest.mark.parametrize("status", [201, 202, 204])
def test_unexpected_2xx_is_indeterminate(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status))
    result = _validate(client)
    assert result.is_indeterminate
    assert not result.is_valid
    assert result.indeterminate_reason is IndeterminateReason.UNEXPECTED_STATUS


# --- 8. Timeout / 9. Transport failure (no retry) ----------------------------------------------


def test_timeout_is_indeterminate_timeout_with_no_retry() -> None:
    def _raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, captured = _client(_raise_timeout)
    result = _validate(client)

    assert result.is_indeterminate
    assert result.indeterminate_reason is IndeterminateReason.TIMEOUT
    assert result.status_code is None
    assert len(captured) == 1  # exactly one attempt, no retry


def test_transport_failure_is_indeterminate_with_no_retry() -> None:
    def _raise_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, captured = _client(_raise_transport)
    result = _validate(client)

    assert result.is_indeterminate
    assert result.indeterminate_reason is IndeterminateReason.TRANSPORT_FAILURE
    assert len(captured) == 1


# --- 10. Cancellation propagates ---------------------------------------------------------------


def test_cancellation_propagates_and_is_not_converted_to_a_result() -> None:
    def _raise_cancel(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    client, _ = _client(_raise_cancel)

    with pytest.raises(asyncio.CancelledError):
        _validate(client)


# --- 11. Redirect safety -----------------------------------------------------------------------


def test_redirect_is_not_followed_no_second_request_and_secret_not_forwarded() -> None:
    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://elsewhere.example/redirected"})

    client, captured = _client(_redirect)
    result = _validate(client)

    assert result.is_indeterminate
    assert result.status_code == 302
    # Exactly one request, to the original validation URL — the redirect target is never contacted,
    # so X-Device-Secret is never forwarded to another host.
    assert len(captured) == 1
    assert captured[0].url.path == VALIDATION_PATH
    assert captured[0].url.host == "backend.local"


# --- 12. Exact-value handling ------------------------------------------------------------------


def test_device_id_and_secret_are_transmitted_verbatim() -> None:
    mixed_device_id = "Device-ID-MixedCase-001"
    mixed_secret = "Secret-MixedCase-Value-ZZZ"  # noqa: S105 - placeholder
    client, captured = _client(_success)

    _validate(client, device_id=mixed_device_id, secret=mixed_secret)

    # No case-normalization or silent rewriting of the values.
    assert captured[0].headers["X-Device-Id"] == mixed_device_id
    assert captured[0].headers["X-Device-Secret"] == mixed_secret


# --- 14. Timeout configuration -----------------------------------------------------------------


def test_uses_the_http_timeout_not_the_validation_interval() -> None:
    # The client is constructed with the HTTP timeout only; it never gets the validation interval
    # (30s). Constructing with a distinct 7.0 proves the request timeout is the HTTP timeout.
    client, captured = _client(_success, timeout=7.0)

    _validate(client)

    timeout = captured[0].extensions["timeout"]
    assert timeout["read"] == 7.0
    assert timeout["connect"] == 7.0
    assert timeout["read"] != 30  # not the WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS default


# --- 15. No activation behaviour ---------------------------------------------------------------


def test_client_never_calls_the_activation_endpoint() -> None:
    client, captured = _client(_confirmed_rejection)
    _validate(client)

    for request in captured:
        assert ACTIVATE_PATH not in request.url.path
    assert not hasattr(client, "activate")  # this client cannot activate


# --- 13. Secret safety -------------------------------------------------------------------------


def test_result_repr_contains_no_secret() -> None:
    client, _ = _client(_confirmed_rejection)
    result = _validate(client)
    assert SHARED_SECRET not in repr(result)
    assert SHARED_SECRET not in str(result)


def test_logs_contain_no_secret_or_malformed_body_across_outcomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="weapon_detection_agent.validation.client")

    malformed_body = "MALFORMED-BODY-WITH-SECRET-" + SHARED_SECRET
    handlers: list[Handler] = [
        _success,
        _confirmed_rejection,
        lambda r: httpx.Response(200, content=malformed_body.encode()),  # malformed 200
        lambda r: httpx.Response(500),
    ]
    for handler in handlers:
        client, _ = _client(handler)
        _validate(client)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    rendered += "\n" + "\n".join(str(record.__dict__) for record in caplog.records)

    assert SHARED_SECRET not in rendered
    assert DEVICE_ID not in rendered
    assert malformed_body not in rendered  # a malformed body is never logged
