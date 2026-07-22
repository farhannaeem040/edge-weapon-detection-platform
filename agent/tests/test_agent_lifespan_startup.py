"""FastAPI lifespan integration for the T-61 startup policy (IP-05 T-61, §5, §12).

These drive the real application lifespan with FastAPI's ``TestClient`` (no real network port) and
injected fakes for the Backend activation client and the T-57 validation client. They prove the
supervisor is wired into startup/shutdown, that the locked branches keep the FastAPI process alive,
that no Agent health endpoint is introduced, and that a persisted lock survives a process restart.
Temporary roots only — never ``/opt`` or the Jetson database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.state import get_runtime
from weapon_detection_agent.runtime.supervisor import StartupBranch
from weapon_detection_agent.validation.models import CredentialValidationResult

DEVICE_ID = "device-lifespan-001"
BRANCH_ID = "branch-lifespan-001"
SECRET_1 = "ZZZ-lifespan-secret-must-never-appear-ZZZ"  # noqa: S105 - placeholder
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
    def __init__(self, *, result: ActivationResult | None = None) -> None:
        self._result = result
        self.activate_calls = 0
        self.closed = False

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.activate_calls += 1
        assert self._result is not None
        return self._result

    async def aclose(self) -> None:
        self.closed = True


class FakeValidationClient:
    def __init__(self, *, result: CredentialValidationResult | None = None) -> None:
        self._result = result if result is not None else CredentialValidationResult.valid(200)
        self.validate_calls = 0
        self.closed = False

    async def validate(
        self, device_id: str, shared_secret: SecretStr
    ) -> CredentialValidationResult:
        self.validate_calls += 1
        return self._result

    async def aclose(self) -> None:
        self.closed = True


def _prepared_root(tmp_path: Path) -> AgentPaths:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths


def _store_operational(paths: AgentPaths) -> None:
    DeviceIdentityRepository(paths.database_file).store(
        DeviceIdentity(
            device_id=DEVICE_ID,
            shared_secret=SecretStr(SECRET_1),
            activated_at=T0,
            last_activated_at=T0,
        )
    )


def _loader(root: Path, *, key: str | None):  # type: ignore[no-untyped-def]
    def _load():  # type: ignore[no-untyped-def]
        return load_settings(
            backend_base_url="http://backend.local:5230",
            root_path=str(root / "weapon-detection"),
            activation_key=key,
            http_timeout_seconds=5,
            credential_validation_interval_seconds=30,
            log_level="INFO",
        )

    return _load


def _app(
    root: Path, backend: FakeBackendClient, validation: FakeValidationClient, *, key: str | None
) -> FastAPI:
    return create_app(
        settings_loader=_loader(root, key=key),
        clock=lambda: T0,
        backend_client_factory=lambda settings: backend,  # type: ignore[arg-type,return-value]
        validation_client_factory=lambda s: validation,  # type: ignore[arg-type,return-value]
    )


# --- 21. FastAPI lifespan wiring ---------------------------------------------------------------


def test_lifespan_drives_supervisor_startup_and_shutdown(tmp_path: Path) -> None:
    _prepared_root(tmp_path)  # provisioned so the first run is a first activation
    paths = resolve_paths(tmp_path / "weapon-detection")
    paths.activation_key_file.write_text("a-file-key", encoding="utf-8")
    backend = FakeBackendClient(
        result=ActivationResult(
            device_id=DEVICE_ID, shared_secret=SecretStr(SECRET_1), branch_id=BRANCH_ID
        )
    )
    validation = FakeValidationClient()
    app = _app(tmp_path, backend, validation, key=None)

    with TestClient(app) as client:
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.supervisor is not None
        assert runtime.supervisor.startup_branch is StartupBranch.FIRST_ACTIVATION
        assert runtime.supervisor.state is OperationalState.OPERATIONAL
        assert runtime.supervisor.is_monitor_running is True
        assert client.get("/openapi.json").status_code == 200  # the app is serving

    assert get_runtime(app) is None  # runtime cleared on shutdown
    assert backend.closed is True  # owned Backend client closed
    assert validation.closed is True  # owned validation client closed by supervisor shutdown


def test_locked_startup_keeps_fastapi_process_alive(tmp_path: Path) -> None:
    paths = _prepared_root(tmp_path)
    _store_operational(paths)
    DeviceIdentityRepository(paths.database_file).mark_reactivation_required()  # persisted lock
    backend = FakeBackendClient()
    validation = FakeValidationClient()
    app = _app(tmp_path, backend, validation, key=None)

    with TestClient(app) as client:
        runtime = get_runtime(app)
        assert runtime is not None
        assert runtime.supervisor is not None
        # Branch C: locked, but the FastAPI process is up and serving built-in routes.
        assert runtime.supervisor.startup_branch is StartupBranch.LOCKED_REACTIVATION_REQUIRED
        assert runtime.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert runtime.supervisor.is_monitor_running is False
        assert client.get("/openapi.json").status_code == 200

    assert backend.activate_calls == 0  # no activation in locked mode
    assert validation.validate_calls == 0  # no validation of a NULL secret


def test_no_agent_health_endpoint_is_introduced(tmp_path: Path) -> None:
    paths = _prepared_root(tmp_path)
    _store_operational(paths)
    app = _app(tmp_path, FakeBackendClient(), FakeValidationClient(), key=None)

    paths_defined = {getattr(route, "path", None) for route in app.routes}
    for forbidden in ("/health", "/status", "/ready", "/live", "/activate"):
        assert forbidden not in paths_defined


# --- 22. Restart persistence — a locked state survives a process restart ------------------------


def test_persisted_lock_survives_restart_into_branch_c(tmp_path: Path) -> None:
    paths = _prepared_root(tmp_path)
    _store_operational(paths)

    # First run: a confirmed rejection at startup persists the lock (Branch D3).
    backend1 = FakeBackendClient()
    validation1 = FakeValidationClient(result=CredentialValidationResult.confirmed_rejected(401))
    app1 = _app(tmp_path, backend1, validation1, key=None)
    with TestClient(app1):
        runtime1 = get_runtime(app1)
        assert runtime1 is not None and runtime1.supervisor is not None
        assert runtime1.supervisor.startup_branch is StartupBranch.STARTUP_CONFIRMED_REJECTED
    stored = DeviceIdentityRepository(paths.database_file).load()
    assert stored is not None
    assert stored.operational_state is OperationalState.REACTIVATION_REQUIRED  # persisted
    assert stored.shared_secret is None

    # Restart: a brand-new app on the same database, no key → Branch C, nothing operational.
    backend2 = FakeBackendClient()
    validation2 = FakeValidationClient()
    app2 = _app(tmp_path, backend2, validation2, key=None)
    with TestClient(app2):
        runtime2 = get_runtime(app2)
        assert runtime2 is not None
        assert runtime2.supervisor is not None
        assert runtime2.supervisor.startup_branch is StartupBranch.LOCKED_REACTIVATION_REQUIRED
        assert runtime2.supervisor.is_monitor_running is False

    assert backend2.activate_calls == 0
    assert validation2.validate_calls == 0  # no validation, no components, no monitor
