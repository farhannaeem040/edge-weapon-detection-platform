"""Import-safety, application-factory, and lifespan tests (IP-02 T-39, §5, §12).

The lifespan is driven with FastAPI's ``TestClient`` as a context manager (no real network port);
the Backend client is a fake injected through the factory (no real Backend). Settings are injected
through a loader so tests use a temporary root, never ``/opt``. An autouse fixture restores the
Agent logger after each test so ``configure_logging`` (run in the lifespan) does not leak elsewhere.
"""

from __future__ import annotations

import builtins
import importlib
import logging
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationOutcome
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.database import open_connection
from weapon_detection_agent.runtime.state import get_runtime

DEVICE_ID = "device-test-001"
BRANCH_ID = "branch-test-001"
FAKE_SECRET = "test-shared-secret-ZZZ"
FAKE_KEY = "keyid.test-activation-secret-ZZZ"
T0 = datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)


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
        self.received_key: SecretStr | None = None

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.activate_calls += 1
        self.received_key = activation_key
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    async def aclose(self) -> None:
        self.closed = True


def _result() -> ActivationResult:
    return ActivationResult(
        device_id=DEVICE_ID, shared_secret=SecretStr(FAKE_SECRET), branch_id=BRANCH_ID
    )


def _loader(tmp_root: Path, *, key: str | None, url: str = "http://backend.local:5230"):
    def _load():  # type: ignore[no-untyped-def]
        return load_settings(
            backend_base_url=url,
            root_path=str(tmp_root / "weapon-detection"),
            activation_key=key,
            http_timeout_seconds=5,
            log_level="INFO",
        )

    return _load


def _app(tmp_root: Path, backend: object, *, key: str | None) -> FastAPI:
    return create_app(
        settings_loader=_loader(tmp_root, key=key),
        clock=lambda: T0,
        backend_client_factory=lambda settings: backend,  # type: ignore[arg-type,return-value]
    )


# --- 1-4. Import safety ------------------------------------------------------------------------


def test_import_main_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    before = len(logging.getLogger().handlers)

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing main must not perform I/O")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(sqlite3, "connect", _forbidden)

    module = importlib.reload(importlib.import_module("weapon_detection_agent.main"))

    assert module.app.title == "Weapon Detection Agent"
    assert len(logging.getLogger().handlers) == before  # no logging handlers configured on import


# --- 5-9. Application factory ------------------------------------------------------------------


def test_create_app_returns_fastapi() -> None:
    assert isinstance(create_app(), FastAPI)


def test_create_app_returns_independent_instances() -> None:
    assert create_app() is not create_app()


def test_create_app_does_not_run_startup(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    # Building the app must not enter the lifespan.
    assert get_runtime(app) is None
    assert backend.activate_calls == 0


def test_app_has_no_operational_routes() -> None:
    paths = {getattr(route, "path", None) for route in create_app().routes}
    for forbidden in ("/", "/health", "/status", "/ready", "/live", "/activate", "/alerts"):
        assert forbidden not in paths


# --- 10-23. Successful first activation --------------------------------------------------------


def test_first_activation_startup_publishes_runtime(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app) as client:
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.FIRST_ACTIVATION
        assert runtime.activation.device_id == DEVICE_ID
        assert runtime.activation.branch_id == BRANCH_ID
        # The app is serving: a built-in route responds.
        assert client.get("/openapi.json").status_code == 200

    assert backend.activate_calls == 1


def test_first_activation_uses_injected_dependencies(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        # Paths resolved from the injected root; timeout/base-url from injected settings.
        assert runtime.paths.root == tmp_path / "weapon-detection"
        assert runtime.settings.backend_base_url == "http://backend.local:5230"
        assert runtime.settings.http_timeout_seconds == 5

    # The resolved key was passed to the client as a SecretStr.
    assert isinstance(backend.received_key, SecretStr)
    assert backend.received_key.get_secret_value() == FAKE_KEY


def test_first_activation_persists_identity_and_provisions_layout(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        # Approved directories exist; no deferred ones.
        assert sorted(c.name for c in runtime.paths.root.iterdir()) == [
            "config",
            "database",
            "logs",
        ]
        stored = runtime.identity_repository.load()
        assert stored is not None
        assert stored.device_id == DEVICE_ID
        assert stored.activated_at == T0 and stored.last_activated_at == T0


def test_first_activation_removes_file_key_after_success(tmp_path: Path) -> None:
    # Provision first so the config dir exists, then place a file key and use no env key.
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=None)

    with TestClient(app):
        assert get_runtime(app) is not None

    assert not paths.activation_key_file.exists()


def test_runtime_repr_has_no_secret(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        runtime = get_runtime(app)
        assert runtime is not None
        assert FAKE_KEY not in repr(runtime)
        assert FAKE_SECRET not in repr(runtime)


def test_logging_configured_with_level_during_startup(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        agent_logger = logging.getLogger("weapon_detection_agent")
        # configure_logging ran: handlers installed and the level applied.
        assert agent_logger.handlers
        assert agent_logger.level == logging.INFO


# --- 52-57. Shutdown --------------------------------------------------------------------------


def test_shutdown_closes_owned_client_and_clears_state(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        assert backend.closed is False

    assert backend.closed is True  # closed on shutdown
    assert get_runtime(app) is None  # runtime reference cleared


def test_shutdown_preserves_identity_database_and_directories(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)
    root = tmp_path / "weapon-detection"

    with TestClient(app):
        pass

    # Nothing was deleted on shutdown.
    assert root.is_dir()
    assert (root / "database" / "agent.db").exists()
    with open_connection(root / "database" / "agent.db") as connection:
        (identities,) = connection.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()
        (configs,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()
    assert identities == 1
    assert configs == 0  # ConfigCache never written


# --- 62-68. Boundary --------------------------------------------------------------------------


def test_startup_writes_no_config_cache(tmp_path: Path) -> None:
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        pass

    with open_connection(tmp_path / "weapon-detection" / "database" / "agent.db") as connection:
        (configs,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()
    assert configs == 0


def test_empty_config_cache_does_not_block_startup(tmp_path: Path) -> None:
    # No ConfigCache row is ever written; startup must still complete (offline-startup, OI-2).
    backend = FakeBackendClient(result=_result())
    app = _app(tmp_path, backend, key=FAKE_KEY)

    with TestClient(app):
        assert get_runtime(app) is not None
