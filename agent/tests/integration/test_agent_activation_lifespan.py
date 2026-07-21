"""Simulated-Backend integration suite (IP-02 T-40, §16.2).

A stateful in-memory Backend serves ``POST /api/v1/activate`` with the **exact** delivered wire
contract (§11) through ``httpx.MockTransport``. It is a test double for the *Backend* only: every
Agent component under test is real — the real ``BackendActivationClient`` (so its request bytes and
envelope parsing are exercised), the real ``ActivationService`` and ``ActivationKeyResolver``, the
real T-35/T-36 SQLite store, and the real T-39 FastAPI lifespan — against a temporary root.

This complements the T-37/T-38/T-39 unit tests (which inject a fake client returning pre-built
results): here the whole cross-component flow runs against real JSON bytes, so a mis-encoded
envelope would be caught. Per the §14 amendment (approved during T-37), transport failures and
timeouts are **never retried**; the §16.2 "retries then fails" row is superseded accordingly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.errors import (
    ActivationOutcomeAmbiguousError,
    ActivationRejectedError,
    ActivationServerError,
    ActivationTransportError,
    DeviceIdentityMismatchError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.service import ActivationOutcome
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.database import open_connection
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.runtime.state import get_runtime

DEVICE_ID = "device-sim-11111111"
OTHER_DEVICE_ID = "device-sim-99999999"
BRANCH_ID = "branch-sim-22222222"
KEY_1 = "simkeyid1.sim-secret-one-ZZZ"
KEY_2 = "simkeyid2.sim-secret-two-ZZZ"
FAKE_KEY = "badkeyid.not-a-real-secret-ZZZ"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


class SimulatedBackend:
    """Exact §11 ``POST /api/v1/activate`` contract with one-time-key state and failure modes."""

    def __init__(self) -> None:
        self._keys: dict[str, tuple[str, str]] = {}
        self._consumed: set[str] = set()
        self.request_count = 0
        self.issued_secrets: list[str] = []
        self.mode = "normal"  # normal | timeout | transport | server | malformed
        self._secret_seq = 0

    def register_key(self, key: str, *, device_id: str, branch_id: str) -> None:
        self._keys[key] = (device_id, branch_id)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        if self.mode == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if self.mode == "transport":
            raise httpx.ConnectError("simulated transport failure", request=request)
        if self.mode == "server":
            return httpx.Response(503)
        if self.mode == "malformed":
            return httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )

        # Contract assertions on the real Agent request: exact method/route/body, no auth header.
        assert request.method == "POST"
        assert request.url.path == "/api/v1/activate"
        assert "authorization" not in {name.lower() for name in request.headers}
        body = json.loads(request.content)
        assert list(body.keys()) == ["activationKey"]
        key = body["activationKey"]

        if not isinstance(key, str) or key in self._consumed or key not in self._keys:
            return self._reject()

        device_id, branch_id = self._keys[key]
        self._consumed.add(key)  # one-time key (BR-003)
        self._secret_seq += 1
        secret = f"sim-issued-secret-{self._secret_seq}-ZZZ"
        self.issued_secrets.append(secret)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"deviceId": device_id, "sharedSecret": secret, "branchId": branch_id},
            },
        )

    @staticmethod
    def _reject() -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "success": False,
                "message": "The activation key is invalid.",
                "errorCode": "INVALID_ACTIVATION_KEY",
            },
        )


class _SpyClient(BackendActivationClient):
    """A real activation client that records whether the runtime closed it on shutdown."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


@pytest.fixture
def build_app(tmp_path: Path) -> Iterator[object]:
    """Yield a builder that wires the real lifespan to a SimulatedBackend over MockTransport."""
    injected: list[httpx.AsyncClient] = []
    spies: list[_SpyClient] = []
    root = tmp_path / "weapon-detection"

    def _build(sim: SimulatedBackend, *, key: str | None, clock: object = lambda: T0) -> object:
        client = httpx.AsyncClient(transport=httpx.MockTransport(sim.handler))
        injected.append(client)

        def factory(settings: object) -> _SpyClient:
            spy = _SpyClient(
                settings.backend_base_url,  # type: ignore[attr-defined]
                timeout_seconds=settings.http_timeout_seconds,  # type: ignore[attr-defined]
                http_client=client,
            )
            spies.append(spy)
            return spy

        def loader() -> object:
            return load_settings(
                backend_base_url="http://sim.backend:5230",
                root_path=str(root),
                activation_key=key,
                http_timeout_seconds=5,
                log_level="INFO",
            )

        return create_app(
            settings_loader=loader,
            clock=clock,  # type: ignore[arg-type]
            backend_client_factory=factory,  # type: ignore[arg-type]
        )

    # Expose the created spies and the Agent root to the test through the builder itself.
    _build.spies = spies  # type: ignore[attr-defined]
    _build.root = root  # type: ignore[attr-defined]

    yield _build

    for client in injected:
        asyncio.run(client.aclose())
    logger = logging.getLogger("weapon_detection_agent")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


def _root_of(build_app: object) -> AgentPaths:
    return resolve_paths(build_app.root)  # type: ignore[attr-defined]


def _write_key(paths: AgentPaths, key: str) -> None:
    paths.provision()
    paths.activation_key_file.write_text(key, encoding="utf-8")


def _config_count(paths: AgentPaths) -> int:
    with open_connection(paths.database_file) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()
    return int(count)


# --- 1-8. First activation through the real lifespan against the simulated wire ----------------


def test_first_activation_stores_identity_and_removes_file_key(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    paths = _root_of(build_app)
    _write_key(paths, KEY_1)
    app = build_app(sim, key=None)  # type: ignore[operator]

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.FIRST_ACTIVATION
        assert runtime.activation.device_id == DEVICE_ID
        assert runtime.activation.branch_id == BRANCH_ID  # branch id surfaced through the result

    assert sim.request_count == 1  # exactly one request
    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID
    # The stored secret is exactly the one the simulated Backend issued (checked, never printed).
    assert stored.shared_secret.get_secret_value() == sim.issued_secrets[0]
    assert not paths.activation_key_file.exists()  # removed only after persistence
    assert _config_count(paths) == 0  # ConfigCache untouched


# --- 9. Restart without a key → no activation request ------------------------------------------


def test_restart_without_key_makes_no_request(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    paths = _root_of(build_app)
    _write_key(paths, KEY_1)

    with TestClient(build_app(sim, key=None)):  # type: ignore[operator]
        pass
    assert sim.request_count == 1

    # Second lifespan, no key present (it was consumed+deleted) → offline no-op.
    app2 = build_app(sim, key=None, clock=lambda: T1)  # type: ignore[operator]
    with TestClient(app2):
        runtime = get_runtime(app2)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.ALREADY_ACTIVATED
    assert sim.request_count == 1  # no additional request


# --- 10-11. Reactivation with a matching Device ID ---------------------------------------------


def test_reactivation_updates_only_secret_and_last_activated_at(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    sim.register_key(KEY_2, device_id=DEVICE_ID, branch_id=BRANCH_ID)  # same device
    paths = _root_of(build_app)

    _write_key(paths, KEY_1)
    with TestClient(build_app(sim, key=None, clock=lambda: T0)):  # type: ignore[operator]
        pass
    first = DeviceIdentityRepository(paths.database_file).load()
    assert first is not None

    _write_key(paths, KEY_2)
    with TestClient(build_app(sim, key=None, clock=lambda: T1)) as _c:  # type: ignore[operator]
        pass
    second = DeviceIdentityRepository(paths.database_file).load()

    assert sim.request_count == 2
    assert second is not None
    assert second.device_id == DEVICE_ID  # unchanged
    assert second.activated_at == first.activated_at  # original ActivatedAt preserved
    assert second.last_activated_at == T1  # advanced
    assert second.shared_secret.get_secret_value() == sim.issued_secrets[1]  # rotated


# --- 12. Device ID mismatch fails without changing identity ------------------------------------


def test_device_id_mismatch_fails_and_preserves_identity(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    sim.register_key(KEY_2, device_id=OTHER_DEVICE_ID, branch_id=BRANCH_ID)  # different device!
    paths = _root_of(build_app)

    _write_key(paths, KEY_1)
    with TestClient(build_app(sim, key=None)):  # type: ignore[operator]
        pass
    before = DeviceIdentityRepository(paths.database_file).load()
    assert before is not None

    _write_key(paths, KEY_2)
    app2 = build_app(sim, key=None, clock=lambda: T1)  # type: ignore[operator]
    with pytest.raises(DeviceIdentityMismatchError):
        with TestClient(app2):
            pass

    after = DeviceIdentityRepository(paths.database_file).load()
    assert after is not None
    assert after.device_id == DEVICE_ID
    assert after.shared_secret.get_secret_value() == before.shared_secret.get_secret_value()
    assert paths.activation_key_file.exists()  # not deleted on mismatch
    assert get_runtime(app2) is None  # runtime not published


# --- 13-16. Failure modes: no retry, ambiguous timeout, safe mapping ---------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("timeout", ActivationOutcomeAmbiguousError),
        ("transport", ActivationTransportError),
        ("server", ActivationServerError),
        ("malformed", InvalidActivationResponseError),
    ],
)
def test_failure_modes_fail_startup_without_retry(
    build_app: object, mode: str, expected: type[Exception]
) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    sim.mode = mode
    paths = _root_of(build_app)
    _write_key(paths, KEY_1)
    app = build_app(sim, key=None)  # type: ignore[operator]

    with pytest.raises(expected):
        with TestClient(app):
            pass

    assert sim.request_count == 1  # exactly one attempt, never retried
    assert DeviceIdentityRepository(paths.database_file).load() is None
    assert paths.activation_key_file.exists()
    assert get_runtime(app) is None


def test_invalid_key_maps_to_uniform_rejection(build_app: object) -> None:
    sim = SimulatedBackend()  # FAKE_KEY is not registered → uniform 401
    paths = _root_of(build_app)
    _write_key(paths, FAKE_KEY)
    app = build_app(sim, key=None)  # type: ignore[operator]

    with pytest.raises(ActivationRejectedError) as excinfo:
        with TestClient(app):
            pass

    assert excinfo.value.status_code == 401
    assert excinfo.value.error_code == "INVALID_ACTIVATION_KEY"
    assert sim.request_count == 1
    assert DeviceIdentityRepository(paths.database_file).load() is None
    assert paths.activation_key_file.exists()


def test_consumed_key_is_rejected_on_a_second_agent(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    paths = _root_of(build_app)

    _write_key(paths, KEY_1)
    with TestClient(build_app(sim, key=None)):  # type: ignore[operator]
        pass  # first activation consumes KEY_1

    # A fresh Agent presenting the now-consumed key is rejected identically.
    _write_key(paths, KEY_1)
    with pytest.raises(ActivationRejectedError):
        with TestClient(build_app(sim, key=None, clock=lambda: T1)):  # type: ignore[operator]
            pass


# --- 18-19. Runtime publication and resource shutdown ------------------------------------------


def test_runtime_published_only_after_success_and_client_closed(build_app: object) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    paths = _root_of(build_app)
    _write_key(paths, KEY_1)
    app = build_app(sim, key=None)  # type: ignore[operator]

    assert get_runtime(app) is None  # not before startup
    with TestClient(app):
        assert get_runtime(app) is not None
    assert get_runtime(app) is None  # cleared on shutdown

    spies = build_app.spies  # type: ignore[attr-defined]
    assert spies and spies[-1].closed is True  # runtime closed its owned client


# --- 20. No secret or key appears in logs, errors, or representations --------------------------


def test_no_secret_or_key_in_logs(build_app: object, caplog: pytest.LogCaptureFixture) -> None:
    sim = SimulatedBackend()
    sim.register_key(KEY_1, device_id=DEVICE_ID, branch_id=BRANCH_ID)
    paths = _root_of(build_app)
    _write_key(paths, KEY_1)
    app = build_app(sim, key=None)  # type: ignore[operator]

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        with TestClient(app):
            runtime = get_runtime(app)
            assert runtime is not None
            issued = sim.issued_secrets[0]
            assert KEY_1 not in repr(runtime) and issued not in repr(runtime)

    issued = sim.issued_secrets[0]
    assert KEY_1 not in caplog.text
    assert issued not in caplog.text
    assert "sim-secret-one" not in caplog.text
