"""Unit tests for the published runtime state (IP-02 T-39, §13).

``AgentRuntime`` must be safe to hold and represent — no secret may leak through its ``repr`` or
``str`` — and ``get_runtime`` must report absence before startup. No lifespan runs here; the object
is built directly with fake, obviously non-credential values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI

from weapon_detection_agent.activation.service import ActivationOutcome, ActivationServiceResult
from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.runtime.state import AgentRuntime, get_runtime

FAKE_KEY = "keyid.fake-activation-secret-ZZZ"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _runtime(tmp_path: Path) -> AgentRuntime:
    paths = resolve_paths(tmp_path / "weapon-detection")
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=str(paths.root),
        activation_key=FAKE_KEY,
    )
    return AgentRuntime(
        settings=settings,
        paths=paths,
        identity_repository=DeviceIdentityRepository(paths.database_file),
        config_cache_repository=ConfigCacheRepository(paths.database_file),
        activation=ActivationServiceResult(
            outcome=ActivationOutcome.FIRST_ACTIVATION,
            device_id="device-test-001",
            activated_at=T0,
            last_activated_at=T0,
            branch_id="branch-test-001",
        ),
    )


def test_runtime_fields_are_populated(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    assert runtime.activation.outcome is ActivationOutcome.FIRST_ACTIVATION
    assert runtime.activation.device_id == "device-test-001"
    assert isinstance(runtime.identity_repository, DeviceIdentityRepository)
    assert isinstance(runtime.config_cache_repository, ConfigCacheRepository)


def test_activation_key_absent_from_runtime_repr_and_str(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    # The env key lives inside settings.activation_key (a SecretStr) and must not leak.
    assert FAKE_KEY not in repr(runtime)
    assert FAKE_KEY not in str(runtime)
    assert "fake-activation-secret" not in repr(runtime)


def test_get_runtime_absent_before_publish() -> None:
    app = FastAPI()

    assert get_runtime(app) is None


def test_get_runtime_returns_published_value(tmp_path: Path) -> None:
    app = FastAPI()
    runtime = _runtime(tmp_path)
    app.state.runtime = runtime

    assert get_runtime(app) is runtime


def test_get_runtime_none_after_cleared(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.runtime = _runtime(tmp_path)
    app.state.runtime = None

    assert get_runtime(app) is None
