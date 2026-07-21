"""Unit tests for the Backend activation client (IP-02 T-37, §11, §13).

The client is exercised entirely in-memory via ``httpx.MockTransport`` — no real Backend, no
network port, no SQLite, no filesystem. Async methods are driven with ``asyncio.run`` so no
async-test plugin is required. All fixtures use obviously fake values; the fake key and secret must
never appear in captured logs or errors, which the secret-safety tests assert.
"""

from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import logging
import socket
import sys
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from weapon_detection_agent.activation.backend_client import (
    ACTIVATE_PATH,
    BackendActivationClient,
    build_activate_url,
)
from weapon_detection_agent.activation.errors import (
    ActivationRejectedError,
    ActivationServerError,
    ActivationTimeoutError,
    ActivationTransportError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.models import ActivationResult

FAKE_KEY = "keyid.test-activation-secret-ZZZ"
FAKE_SHARED_SECRET = "test-shared-secret-ZZZ"
DEVICE_ID = "device-test-001"
BRANCH_ID = "branch-test-001"
BASE_URL = "http://backend.local:5230"

Handler = Callable[[httpx.Request], httpx.Response]


def _success_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "deviceId": DEVICE_ID,
                "sharedSecret": FAKE_SHARED_SECRET,
                "branchId": BRANCH_ID,
            },
        },
    )


def _client(
    handler: Handler, *, base_url: str = BASE_URL, timeout: float = 10.0
) -> tuple[BackendActivationClient, httpx.AsyncClient, list[httpx.Request]]:
    """Build a client backed by a MockTransport, capturing every request it makes."""
    captured: list[httpx.Request] = []

    def _capturing(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_capturing))
    client = BackendActivationClient(base_url, timeout_seconds=timeout, http_client=http_client)
    return client, http_client, captured


def _activate(client: BackendActivationClient, key: str = FAKE_KEY) -> ActivationResult:
    async def _run() -> ActivationResult:
        try:
            return await client.activate(SecretStr(key))
        finally:
            await client.aclose()

    return asyncio.run(_run())


# --- URL construction --------------------------------------------------------------------------


def test_build_url_without_trailing_slash() -> None:
    assert build_activate_url("http://host:5230") == "http://host:5230/api/v1/activate"


def test_build_url_with_trailing_slash() -> None:
    assert build_activate_url("http://host:5230/") == "http://host:5230/api/v1/activate"


def test_build_url_preserves_base_path_prefix() -> None:
    assert build_activate_url("http://host/gateway") == "http://host/gateway/api/v1/activate"


def test_build_url_no_duplicate_slashes() -> None:
    assert "//api" not in build_activate_url("http://host:5230/")
    assert build_activate_url("http://host:5230///") == "http://host:5230/api/v1/activate"


# --- Exact request -----------------------------------------------------------------------------


def test_request_method_path_body_and_content_type() -> None:
    client, _http, captured = _client(_success_response)
    _activate(client)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == ACTIVATE_PATH
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.content) == {"activationKey": FAKE_KEY}


def test_key_absent_from_url_and_query() -> None:
    client, _http, captured = _client(_success_response)
    _activate(client)

    request = captured[0]
    assert FAKE_KEY not in str(request.url)
    assert request.url.query == b""


def test_no_authorization_header_is_sent() -> None:
    client, _http, captured = _client(_success_response)
    _activate(client)

    header_names = {name.lower() for name in captured[0].headers}
    assert "authorization" not in header_names


def test_request_body_has_only_the_activation_key_field() -> None:
    client, _http, captured = _client(_success_response)
    _activate(client)

    assert list(json.loads(captured[0].content).keys()) == ["activationKey"]


def test_configured_timeout_is_applied() -> None:
    client, _http, captured = _client(_success_response, timeout=3.5)
    _activate(client)

    # httpx records the per-request timeout in the request extensions.
    timeout = captured[0].extensions["timeout"]
    assert timeout["read"] == 3.5
    assert timeout["connect"] == 3.5


def test_exactly_one_request_on_success() -> None:
    client, _http, captured = _client(_success_response)
    _activate(client)

    assert len(captured) == 1


def test_no_retry_on_rejection() -> None:
    client, _http, captured = _client(lambda r: httpx.Response(401, json={"success": False}))

    async def _run() -> None:
        try:
            with pytest.raises(ActivationRejectedError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())
    assert len(captured) == 1  # one request only — the 401 was not retried


# --- Success parsing ---------------------------------------------------------------------------


def test_success_returns_typed_result() -> None:
    client, _http, _captured = _client(_success_response)

    result = _activate(client)

    assert isinstance(result, ActivationResult)
    assert result.device_id == DEVICE_ID
    assert result.branch_id == BRANCH_ID


def test_success_shared_secret_is_secretstr_and_redacted() -> None:
    client, _http, _captured = _client(_success_response)

    result = _activate(client)

    assert isinstance(result.shared_secret, SecretStr)
    assert result.shared_secret.get_secret_value() == FAKE_SHARED_SECRET
    assert FAKE_SHARED_SECRET not in repr(result)
    assert FAKE_SHARED_SECRET not in str(result)


def test_success_with_no_configuration_invents_none() -> None:
    client, _http, _captured = _client(_success_response)

    result = _activate(client)

    assert set(vars(result).keys()) == {"device_id", "shared_secret", "branch_id"}


# --- Response validation -----------------------------------------------------------------------


def _expect_invalid(handler: Handler) -> None:
    client, _http, _captured = _client(handler)

    async def _run() -> None:
        try:
            with pytest.raises(InvalidActivationResponseError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_invalid_json_is_rejected() -> None:
    _expect_invalid(
        lambda r: httpx.Response(
            200, content=b"not json", headers={"content-type": "application/json"}
        )
    )


def test_empty_body_is_rejected() -> None:
    _expect_invalid(lambda r: httpx.Response(200, content=b""))


def test_non_object_envelope_is_rejected() -> None:
    _expect_invalid(lambda r: httpx.Response(200, json=[1, 2, 3]))


def test_success_not_true_is_rejected() -> None:
    _expect_invalid(lambda r: httpx.Response(200, json={"success": False, "data": {}}))


def test_missing_data_is_rejected() -> None:
    _expect_invalid(lambda r: httpx.Response(200, json={"success": True}))


@pytest.mark.parametrize("field", ["deviceId", "sharedSecret", "branchId"])
def test_missing_required_field_is_rejected(field: str) -> None:
    data = {"deviceId": DEVICE_ID, "sharedSecret": FAKE_SHARED_SECRET, "branchId": BRANCH_ID}
    del data[field]
    _expect_invalid(lambda r: httpx.Response(200, json={"success": True, "data": data}))


@pytest.mark.parametrize("field", ["deviceId", "sharedSecret", "branchId"])
def test_blank_required_field_is_rejected(field: str) -> None:
    data = {"deviceId": DEVICE_ID, "sharedSecret": FAKE_SHARED_SECRET, "branchId": BRANCH_ID}
    data[field] = ""
    _expect_invalid(lambda r: httpx.Response(200, json={"success": True, "data": data}))


def test_wrong_field_type_is_rejected() -> None:
    data = {"deviceId": 1, "sharedSecret": FAKE_SHARED_SECRET, "branchId": BRANCH_ID}
    _expect_invalid(lambda r: httpx.Response(200, json={"success": True, "data": data}))


# --- HTTP failure mapping ----------------------------------------------------------------------


def _raise(exc: Exception) -> Handler:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return _handler


def test_timeout_maps_to_timeout_error() -> None:
    client, _http, _captured = _client(_raise(httpx.ConnectTimeout("slow")))

    async def _run() -> None:
        try:
            with pytest.raises(ActivationTimeoutError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_transport_failure_maps_to_transport_error() -> None:
    client, _http, _captured = _client(_raise(httpx.ConnectError("no route")))

    async def _run() -> None:
        try:
            with pytest.raises(ActivationTransportError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_401_maps_to_rejection_with_error_code() -> None:
    handler = lambda r: httpx.Response(  # noqa: E731
        401,
        json={
            "success": False,
            "message": "The activation key is invalid.",
            "errorCode": "INVALID_ACTIVATION_KEY",
        },
    )
    client, _http, _captured = _client(handler)

    async def _run() -> ActivationRejectedError:
        try:
            with pytest.raises(ActivationRejectedError) as excinfo:
                await client.activate(SecretStr(FAKE_KEY))
            return excinfo.value
        finally:
            await client.aclose()

    error = asyncio.run(_run())
    assert error.status_code == 401
    assert error.error_code == "INVALID_ACTIVATION_KEY"


def test_500_maps_to_server_error() -> None:
    client, _http, _captured = _client(lambda r: httpx.Response(503))

    async def _run() -> None:
        try:
            with pytest.raises(ActivationServerError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())


@pytest.mark.parametrize("status", [400, 403, 404, 302])
def test_unexpected_status_maps_to_server_error(status: int) -> None:
    client, _http, _captured = _client(lambda r: httpx.Response(status))

    async def _run() -> None:
        try:
            with pytest.raises(ActivationServerError):
                await client.activate(SecretStr(FAKE_KEY))
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_error_carries_no_raw_response_body() -> None:
    # A 401 body with sentinel text must not leak into the exception message.
    sentinel = "SENSITIVE-BODY-TEXT-ZZZ"
    handler = lambda r: httpx.Response(401, json={"success": False, "message": sentinel})  # noqa: E731
    client, _http, _captured = _client(handler)

    async def _run() -> str:
        try:
            with pytest.raises(ActivationRejectedError) as excinfo:
                await client.activate(SecretStr(FAKE_KEY))
            return str(excinfo.value)
        finally:
            await client.aclose()

    message = asyncio.run(_run())
    assert sentinel not in message


# --- Secret safety in logs and errors ----------------------------------------------------------


def test_activation_key_and_secret_absent_from_logs(caplog: pytest.LogCaptureFixture) -> None:
    client, _http, _captured = _client(_success_response)

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        _activate(client)

    assert FAKE_KEY not in caplog.text
    assert FAKE_SHARED_SECRET not in caplog.text
    assert "activationKey" not in caplog.text  # raw request JSON is not logged
    for record in caplog.records:
        assert FAKE_KEY not in str(record.__dict__)
        assert FAKE_SHARED_SECRET not in str(record.__dict__)


def test_secret_absent_from_invalid_response_error() -> None:
    # A 200 with a valid secret but a blank branchId — the error must not echo the secret.
    data = {"deviceId": DEVICE_ID, "sharedSecret": FAKE_SHARED_SECRET, "branchId": ""}
    client, _http, _captured = _client(
        lambda r: httpx.Response(200, json={"success": True, "data": data})
    )

    async def _run() -> str:
        try:
            with pytest.raises(InvalidActivationResponseError) as excinfo:
                await client.activate(SecretStr(FAKE_KEY))
            return str(excinfo.value)
        finally:
            await client.aclose()

    message = asyncio.run(_run())
    assert FAKE_SHARED_SECRET not in message


def test_activation_key_absent_from_rejection_error() -> None:
    client, _http, _captured = _client(lambda r: httpx.Response(401, json={"success": False}))

    async def _run() -> ActivationRejectedError:
        try:
            with pytest.raises(ActivationRejectedError) as excinfo:
                await client.activate(SecretStr(FAKE_KEY))
            return excinfo.value
        finally:
            await client.aclose()

    error = asyncio.run(_run())
    assert FAKE_KEY not in str(error)
    assert FAKE_KEY not in repr(error)


# --- Lifecycle ---------------------------------------------------------------------------------


def test_owned_client_is_closed() -> None:
    client = BackendActivationClient(BASE_URL, timeout_seconds=5.0)

    asyncio.run(client.aclose())

    assert client._client.is_closed is True


def test_injected_client_is_not_closed() -> None:
    injected = httpx.AsyncClient(transport=httpx.MockTransport(_success_response))
    client = BackendActivationClient(BASE_URL, timeout_seconds=5.0, http_client=injected)

    async def _run() -> None:
        await client.aclose()
        assert injected.is_closed is False  # caller-owned: not closed by the client
        await injected.aclose()

    asyncio.run(_run())


def test_async_context_manager_closes_owned_client() -> None:
    async def _run() -> BackendActivationClient:
        async with BackendActivationClient(BASE_URL, timeout_seconds=5.0) as client:
            pass
        return client

    client = asyncio.run(_run())
    assert client._client.is_closed is True


def test_repeated_construction_does_not_raise() -> None:
    async def _run() -> None:
        for _ in range(10):
            c = BackendActivationClient(BASE_URL, timeout_seconds=5.0)
            await c.aclose()

    asyncio.run(_run())


# --- Import safety -----------------------------------------------------------------------------


def test_import_activation_modules_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing activation modules must not perform I/O")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    module_names = (
        "weapon_detection_agent.activation",
        "weapon_detection_agent.activation.errors",
        "weapon_detection_agent.activation.models",
        "weapon_detection_agent.activation.backend_client",
    )
    saved = {name: sys.modules.pop(name) for name in module_names if name in sys.modules}
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_importing_main_remains_side_effect_free() -> None:
    module = importlib.reload(importlib.import_module("weapon_detection_agent.main"))

    assert module.app.title == "Weapon Detection Agent"
