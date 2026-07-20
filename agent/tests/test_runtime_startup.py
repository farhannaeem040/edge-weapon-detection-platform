"""Startup-workflow tests: existing-identity, reactivation, failures, and lifecycle (IP-02 T-39).

The lifespan is driven with FastAPI's ``TestClient`` (no real port); the Backend client is a fake
injected through the factory (no real Backend), except one test that uses a real client over an
in-memory ``MockTransport`` to verify the T-37 ownership contract. Settings are injected with a
temporary root — never ``/opt``. An autouse fixture restores the Agent logger after each test.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from weapon_detection_agent import main
from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.errors import (
    ActivationKeyCleanupError,
    ActivationKeyMissingError,
    ActivationOutcomeAmbiguousError,
    ActivationPersistenceError,
    ActivationRejectedError,
    ActivationServerError,
    ActivationTimeoutError,
    ActivationTransportError,
    DeviceIdentityMismatchError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationOutcome
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import ConfigurationError, load_settings
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import RepositoryError
from weapon_detection_agent.persistence.models import DeviceIdentity
from weapon_detection_agent.persistence.schema import UnsupportedSchemaVersionError
from weapon_detection_agent.runtime.startup import default_backend_client_factory
from weapon_detection_agent.runtime.state import get_runtime

DEVICE_ID = "device-test-001"
OTHER_DEVICE_ID = "device-test-different"
BRANCH_ID = "branch-test-001"
SECRET_1 = "test-secret-one-ZZZ"
SECRET_2 = "test-secret-two-ZZZ"
FAKE_KEY = "keyid.test-activation-secret-ZZZ"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _restore_agent_logging() -> object:
    yield
    logger = logging.getLogger("weapon_detection_agent")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


class FakeBackendClient:
    def __init__(self, *, result: ActivationResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.activate_calls = 0
        self.closed = False

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.activate_calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    async def aclose(self) -> None:
        self.closed = True


def _result(device_id: str = DEVICE_ID, secret: str = SECRET_1) -> ActivationResult:
    return ActivationResult(
        device_id=device_id, shared_secret=SecretStr(secret), branch_id=BRANCH_ID
    )


def _loader(tmp_root: Path, *, key: str | None):
    def _load():  # type: ignore[no-untyped-def]
        return load_settings(
            backend_base_url="http://backend.local:5230",
            root_path=str(tmp_root / "weapon-detection"),
            activation_key=key,
            http_timeout_seconds=5,
            log_level="INFO",
        )

    return _load


def _app(
    tmp_root: Path, backend: object, *, key: str | None, clock: object = lambda: T0
) -> FastAPI:
    return create_app(
        settings_loader=_loader(tmp_root, key=key),
        clock=clock,  # type: ignore[arg-type]
        backend_client_factory=lambda settings: backend,  # type: ignore[arg-type,return-value]
    )


def _ready_root(tmp_path: Path) -> AgentPaths:
    return resolve_paths(tmp_path / "weapon-detection").provision()


def _prestore_identity(paths: AgentPaths, *, secret: str = SECRET_1) -> None:
    initialize_database(paths.database_file)
    DeviceIdentityRepository(paths.database_file).store(
        DeviceIdentity(
            device_id=DEVICE_ID,
            shared_secret=SecretStr(secret),
            activated_at=T0,
            last_activated_at=T0,
        )
    )


def _seed_config(paths: AgentPaths) -> tuple[str, str]:
    updated = T0.isoformat()
    with open_connection(paths.database_file) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
                ('{"seeded": true}', updated),
            )
    return ('{"seeded": true}', updated)


def _config_row(paths: AgentPaths) -> tuple[str, str] | None:
    with open_connection(paths.database_file) as connection:
        row = connection.execute("SELECT ConfigJson, UpdatedAt FROM ConfigCache").fetchone()
    return None if row is None else (row["ConfigJson"], row["UpdatedAt"])


# --- 24-28. Existing identity + no key (no-op) -------------------------------------------------


def test_existing_identity_no_key_starts_without_backend(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    _prestore_identity(paths)
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=None)

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.ALREADY_ACTIVATED

    assert backend.activate_calls == 0


def test_existing_identity_no_key_leaves_identity_unchanged(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    _prestore_identity(paths, secret=SECRET_1)
    app = _app(tmp_path, FakeBackendClient(result=_result()), key=None)

    with TestClient(app):
        pass

    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.shared_secret.get_secret_value() == SECRET_1
    assert stored.activated_at == T0 and stored.last_activated_at == T0


# --- 29-34. Reactivation -----------------------------------------------------------------------


def test_reactivation_replaces_secret_preserving_identity(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    _prestore_identity(paths, secret=SECRET_1)
    config_before = _seed_config(paths)
    backend = FakeBackendClient(result=_result(secret=SECRET_2))
    app = _app(tmp_path, backend, key=FAKE_KEY, clock=lambda: T1)

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.REACTIVATION

    assert backend.activate_calls == 1
    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID
    assert stored.activated_at == T0  # original preserved
    assert stored.last_activated_at == T1  # advanced
    assert stored.shared_secret.get_secret_value() == SECRET_2  # replaced
    assert _config_row(paths) == config_before  # ConfigCache untouched


def test_reactivation_removes_file_key_after_success(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    _prestore_identity(paths)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    app = _app(
        tmp_path, FakeBackendClient(result=_result(secret=SECRET_2)), key=None, clock=lambda: T1
    )

    with TestClient(app):
        pass

    assert not paths.activation_key_file.exists()


# --- 35-51. Startup failures -------------------------------------------------------------------


def test_missing_required_settings_fails_startup(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())

    def _bad_loader():  # type: ignore[no-untyped-def]
        return load_settings(root_path=str(tmp_path))  # missing WDA_BACKEND_BASE_URL

    app = create_app(
        settings_loader=_bad_loader,
        clock=lambda: T0,
        backend_client_factory=lambda s: backend,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass
    assert backend.activate_calls == 0
    assert get_runtime(app) is None


def test_provisioning_failure_stops_before_activation(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    backend = FakeBackendClient(result=_result())
    # root resolves under a *file* (blocker) → provisioning mkdir fails.
    app = _app(blocker, backend, key=FAKE_KEY)

    with pytest.raises(OSError):
        with TestClient(app):
            pass
    assert backend.activate_calls == 0
    assert get_runtime(app) is None


def test_invalid_schema_stops_activation(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    initialize_database(paths.database_file)
    with open_connection(paths.database_file) as connection:
        connection.execute("UPDATE SchemaVersion SET Version = 999")
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with pytest.raises(UnsupportedSchemaVersionError):
        with TestClient(app):
            pass
    assert backend.activate_calls == 0
    assert get_runtime(app) is None


def test_unactivated_no_key_fails_startup(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=None)  # fresh Agent, no key

    with pytest.raises(ActivationKeyMissingError):
        with TestClient(app):
            pass
    assert backend.activate_calls == 0
    assert backend.closed is True  # owned client closed on failure
    assert get_runtime(app) is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ActivationRejectedError(status_code=401, error_code="INVALID_ACTIVATION_KEY"),
            ActivationRejectedError,
        ),
        (ActivationTimeoutError("t"), ActivationOutcomeAmbiguousError),
        (ActivationTransportError("t"), ActivationTransportError),
        (ActivationServerError(status_code=503), ActivationServerError),
        (InvalidActivationResponseError("bad"), InvalidActivationResponseError),
    ],
)
def test_backend_failure_fails_startup_and_closes_client(
    tmp_path: Path, error: Exception, expected: type[Exception]
) -> None:
    backend = FakeBackendClient(error=error)
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with pytest.raises(expected):
        with TestClient(app):
            pass

    assert backend.activate_calls == 1  # exactly one request, no retry
    assert backend.closed is True
    assert get_runtime(app) is None
    # No identity was written.
    assert (
        DeviceIdentityRepository(tmp_path / "weapon-detection" / "database" / "agent.db").load()
        is None
    )


def test_device_id_mismatch_fails_startup(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    _prestore_identity(paths, secret=SECRET_1)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    backend = FakeBackendClient(result=_result(device_id=OTHER_DEVICE_ID, secret=SECRET_2))
    app = _app(tmp_path, backend, key=None, clock=lambda: T1)

    with pytest.raises(DeviceIdentityMismatchError):
        with TestClient(app):
            pass

    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID  # unchanged
    assert stored.shared_secret.get_secret_value() == SECRET_1  # unchanged
    assert paths.activation_key_file.exists()  # not deleted
    assert backend.closed is True
    assert get_runtime(app) is None


def test_persistence_failure_fails_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackendClient(result=_result())

    def _failing_store(self: object, identity: object) -> None:
        raise RepositoryError("injected store failure")

    monkeypatch.setattr(DeviceIdentityRepository, "store", _failing_store)
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with pytest.raises(ActivationPersistenceError):
        with TestClient(app):
            pass

    assert backend.activate_calls == 1  # no retry
    assert backend.closed is True
    assert get_runtime(app) is None


def test_key_cleanup_failure_fails_startup_but_keeps_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _ready_root(tmp_path)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    backend = FakeBackendClient(result=_result())

    def _failing_delete(self: object) -> None:
        raise ActivationKeyCleanupError("injected cleanup failure")

    monkeypatch.setattr(ActivationKeyResolver, "delete_key_file", _failing_delete)
    app = _app(tmp_path, backend, key=None)

    with pytest.raises(ActivationKeyCleanupError):
        with TestClient(app):
            pass

    # Identity persistence committed and is not rolled back by the cleanup failure (T-38 contract).
    assert DeviceIdentityRepository(paths.database_file).load() is not None
    assert backend.closed is True
    assert get_runtime(app) is None


def test_no_secret_in_startup_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    backend = FakeBackendClient(result=_result(secret=SECRET_1))
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        with TestClient(app):
            pass

    assert FAKE_KEY not in caplog.text
    assert SECRET_1 not in caplog.text


# --- 58-61. Lifecycle --------------------------------------------------------------------------


def test_independent_apps_do_not_share_runtime_state(tmp_path: Path) -> None:
    backend1 = FakeBackendClient(result=_result())
    app1 = _app(tmp_path / "a", backend1, key=FAKE_KEY)
    app2 = create_app()  # a second, independent app

    with TestClient(app1):
        assert get_runtime(app1) is not None
        assert get_runtime(app2) is None  # separate app.state


def test_repeated_lifespan_does_not_duplicate_log_handlers(tmp_path: Path) -> None:
    app = _app(tmp_path, FakeBackendClient(result=_result()), key=FAKE_KEY)

    with TestClient(app):
        first = len(logging.getLogger("weapon_detection_agent").handlers)
    with TestClient(app):
        second = len(logging.getLogger("weapon_detection_agent").handlers)

    assert first == second  # configure_logging replaces its own handlers, not stacks them


def test_second_startup_after_activation_is_noop(tmp_path: Path) -> None:
    paths = _ready_root(tmp_path)
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")

    backend1 = FakeBackendClient(result=_result())
    with TestClient(_app(tmp_path, backend1, key=None)):
        pass
    assert backend1.activate_calls == 1  # first activation

    backend2 = FakeBackendClient(result=_result())
    app2 = _app(tmp_path, backend2, key=None)  # key file now gone → no key
    with TestClient(app2):
        runtime = get_runtime(app2)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.ALREADY_ACTIVATED
    assert backend2.activate_calls == 0  # no Backend call on the second run


def test_injected_client_is_caller_owned(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "deviceId": DEVICE_ID,
                    "sharedSecret": SECRET_1,
                    "branchId": BRANCH_ID,
                },
            },
        )

    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BackendActivationClient(
        "http://backend.local:5230", timeout_seconds=5, http_client=injected
    )
    app = _app(tmp_path, client, key=FAKE_KEY)

    with TestClient(app):
        assert get_runtime(app) is not None

    # The runtime called client.aclose(), but a caller-owned httpx is NOT closed (T-37 contract).
    assert injected.is_closed is False
    asyncio.run(injected.aclose())


# --- Default factory and single-worker entry point ---------------------------------------------


def test_default_backend_client_factory_uses_settings() -> None:
    settings = load_settings(
        backend_base_url="http://server:9999", root_path="/x", http_timeout_seconds=7.5
    )
    client = default_backend_client_factory(settings)
    try:
        assert client._url == "http://server:9999/api/v1/activate"
        assert client._timeout == 7.5
    finally:
        asyncio.run(client.aclose())


def test_run_uses_single_uvicorn_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    captured: dict[str, object] = {}

    def _fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    main.run()

    assert captured["workers"] == 1
    assert captured["app"] == "weapon_detection_agent.main:app"


# --- Integration: full lifecycle across lifespans (§20) ----------------------------------------


def test_full_runtime_lifecycle(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    config_before = _seed_config(paths)

    # 1. First lifespan: activate from a file key.
    paths.activation_key_file.write_text("first-key", encoding="utf-8")
    backend1 = FakeBackendClient(result=_result(secret=SECRET_1))
    app1 = _app(tmp_path, backend1, key=None, clock=lambda: T0)
    with TestClient(app1):
        runtime = get_runtime(app1)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.FIRST_ACTIVATION
    assert backend1.activate_calls == 1
    assert backend1.closed is True  # owned client closed on shutdown
    assert not paths.activation_key_file.exists()  # file key removed
    assert DeviceIdentityRepository(paths.database_file).load() is not None  # persists between runs

    # 2. Second lifespan without a new key → no Backend call.
    backend2 = FakeBackendClient(result=_result(secret=SECRET_2))
    with TestClient(_app(tmp_path, backend2, key=None, clock=lambda: T1)):
        pass
    assert backend2.activate_calls == 0

    # 3. A newly written key triggers reactivation.
    paths.activation_key_file.write_text("second-key", encoding="utf-8")
    backend3 = FakeBackendClient(result=_result(secret=SECRET_2))
    app3 = _app(tmp_path, backend3, key=None, clock=lambda: T1)
    with TestClient(app3):
        runtime = get_runtime(app3)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.REACTIVATION
    assert backend3.activate_calls == 1

    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.device_id == DEVICE_ID  # unchanged
    assert stored.activated_at == T0  # original preserved
    assert stored.last_activated_at == T1  # advanced
    assert stored.shared_secret.get_secret_value() == SECRET_2  # replaced
    assert _config_row(paths) == config_before  # ConfigCache untouched
    with open_connection(paths.database_file) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()
    assert count == 1
    # No custom operational route exists on the app.
    assert "/health" not in {getattr(r, "path", None) for r in app3.routes}
