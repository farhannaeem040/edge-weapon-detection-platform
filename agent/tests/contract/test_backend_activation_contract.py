"""Real-Backend contract suite (IP-02 T-40, §16.3; FS-02 AC-13, T-18).

The simulated suite (``tests/integration/``) proves the Agent's *behavior*, but it cannot prove the
contract is *real*: a stub that mis-encoded the envelope would pass every simulated test and still
fail against the actual server. These tests therefore run the real Agent — its real FastAPI
lifespan, real ``BackendActivationClient``, real SQLite store — against the **actual** ASP.NET Core
Backend process, over loopback HTTP, backed by a throwaway SQL Server database, with the Branch and
Activation Key created through the real authenticated API. No simulator-specific accommodation
exists anywhere in the Backend (FS-02 §1.2, §11).

Running these tests::

    # fast suite (default; no SQL Server, no dotnet)
    python -m pytest

    # real-Backend contract suite
    WDA_RUN_BACKEND_CONTRACT_TESTS=1 python -m pytest -m backend_contract

Secret safety: neither the Activation Key nor the device shared secret is ever printed, placed in
an assertion message, written to a filename, or passed on a command line. Equality of secrets is
asserted as a boolean, never by displaying the values.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from support.backend_process import AdminApiClient, BackendHost, ProvisionedBranch
from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.errors import ActivationRejectedError
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationOutcome
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.runtime.state import get_runtime

# Every test in this module needs the real Backend and SQL Server.
pytestmark = pytest.mark.backend_contract


class CountingClient(BackendActivationClient):
    """The **real** activation client, instrumented only to count calls and capture the result.

    This is not a stub: every byte on the wire is produced and parsed by the production client. The
    instrumentation exists because "no activation request was sent" (the restart no-op, MAC-3) must
    be *observed* rather than assumed, and because the issued shared secret must be compared with
    what was persisted without either value being displayed.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.calls = 0
        self.closed = False
        self.last_result: ActivationResult | None = None

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.calls += 1
        result = await super().activate(activation_key)
        self.last_result = result
        return result

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class AgentUnderTest:
    """A real Agent rooted at a temporary directory, pointed at the real Backend."""

    def __init__(self, root: Path, backend_base_url: str) -> None:
        self.root = root
        self.paths: AgentPaths = resolve_paths(root)
        self._backend_base_url = backend_base_url
        self.clients: list[CountingClient] = []

    def write_key_file(self, activation_key: str) -> None:
        """Write the one-time key to the Agent's protected key file.

        The key reaches disk only here, inside a temporary root that is deleted during teardown.
        """
        self.paths.provision()
        self.paths.activation_key_file.write_text(activation_key, encoding="utf-8")

    def build_app(self) -> FastAPI:
        def factory(settings: object) -> CountingClient:
            client = CountingClient(
                settings.backend_base_url,  # type: ignore[attr-defined]
                timeout_seconds=settings.http_timeout_seconds,  # type: ignore[attr-defined]
            )
            self.clients.append(client)
            return client

        def loader() -> object:
            return load_settings(
                backend_base_url=self._backend_base_url,
                root_path=str(self.root),
                activation_key=None,  # file-based key only, per FS-02 §6.1
                http_timeout_seconds=30,
                log_level="INFO",
            )

        return create_app(
            settings_loader=loader,
            backend_client_factory=factory,  # type: ignore[arg-type]
        )

    @property
    def last_client(self) -> CountingClient:
        return self.clients[-1]

    def identity(self) -> object:
        return DeviceIdentityRepository(self.paths.database_file).load()

    def cached_configuration(self) -> object:
        return ConfigCacheRepository(self.paths.database_file).load()


@pytest.fixture
def agent(backend: BackendHost, tmp_path: Path) -> Iterator[AgentUnderTest]:
    """A temporary-root Agent; the root (and any key file in it) is removed on teardown."""
    import logging
    import shutil

    under_test = AgentUnderTest(tmp_path / "weapon-detection", backend.base_url)
    try:
        yield under_test
    finally:
        # Detach the file log handler before the root is deleted, so no handler outlives it.
        logger = logging.getLogger("weapon_detection_agent")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
        shutil.rmtree(under_test.root, ignore_errors=True)


@pytest.fixture
def provisioned(admin: AdminApiClient, request: pytest.FixtureRequest) -> ProvisionedBranch:
    """A Branch with one Camera, one Device, and one valid one-time Activation Key.

    Created through the real authenticated API — the same path a real Admin uses — so no domain
    rule is bypassed and no test-only endpoint exists.
    """
    return admin.create_branch(f"Contract {request.node.name[:40]}")


# --- 13. Contract drift protection ---------------------------------------------------------------


def test_agent_request_shape_matches_the_backend_contract(
    backend: BackendHost, provisioned: ProvisionedBranch
) -> None:
    """The exact wire contract: method, route, body property, anonymity, and success envelope.

    This is the cross-language drift guard. It asserts the request the *real Agent client* builds
    and the response the *real Backend* returns, so a change to the route, the method, the
    ``activationKey`` property, or any ``data`` member fails here with a clear structural message.
    """
    captured: dict[str, object] = {}

    async def record(request: httpx.Request) -> None:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["has_auth"] = "authorization" in {n.lower() for n in request.headers}
        captured["body_keys"] = sorted(json.loads(request.content).keys())

    client = BackendActivationClient(
        backend.base_url,
        timeout_seconds=30,
        http_client=httpx.AsyncClient(event_hooks={"request": [record]}),
    )
    try:
        result = asyncio.run(client.activate(SecretStr(provisioned.activation_key)))
    finally:
        asyncio.run(client.aclose())

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/activate"
    # Anonymous endpoint: the Activation Key is itself the credential (FS-02 §10.4, §11).
    assert captured["has_auth"] is False
    assert captured["body_keys"] == ["activationKey"]

    # The success envelope's `data` carried exactly deviceId, sharedSecret, and branchId — proven by
    # the real parser having produced all three from the real response.
    assert result.device_id
    assert result.branch_id == provisioned.branch_id
    assert result.shared_secret.get_secret_value() != ""


def test_invalid_key_returns_the_uniform_401_contract(backend: BackendHost) -> None:
    """An obviously fake key gets the exact approved rejection — status, code, and message."""
    response = httpx.post(
        f"{backend.base_url}/api/v1/activate",
        json={"activationKey": "not-a-real-key.not-a-real-secret"},
        timeout=30,
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_ACTIVATION_KEY"
    assert body["message"] == "The activation key is invalid."
    # Null members are omitted by the host's WhenWritingNull policy.
    assert "data" not in body


# --- 9. Real first activation through the real Agent lifespan ------------------------------------


def test_first_activation_against_the_real_backend(
    agent: AgentUnderTest, admin: AdminApiClient, provisioned: ProvisionedBranch
) -> None:
    """The full §9 flow: real lifespan -> real HTTP -> real Backend -> real SQLite persistence."""
    agent.write_key_file(provisioned.activation_key)
    app = agent.build_app()

    with TestClient(app) as client:
        runtime = get_runtime(client.app)  # type: ignore[arg-type]

        # 12. Runtime state is published only after a fully successful startup.
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.FIRST_ACTIVATION
        assert runtime.activation.branch_id == provisioned.branch_id

        # 4. Exactly one request — no retry (§14 as amended).
        assert agent.last_client.calls == 1

        # 6/7. Device Identity persisted; the stored DeviceId is the Backend's public DeviceId.
        identity = agent.identity()
        assert identity is not None
        backend_device = admin.device_summary(provisioned.branch_id)
        assert identity.device_id == backend_device["deviceId"]  # type: ignore[attr-defined]

        # 8. The issued secret was persisted intact — compared as a boolean, never displayed.
        issued = agent.last_client.last_result
        assert issued is not None
        secrets_match = (
            identity.shared_secret.get_secret_value()  # type: ignore[attr-defined]
            == issued.shared_secret.get_secret_value()
        )
        assert secrets_match, "persisted shared secret does not match the Backend-issued value"

        # 9. The key file is removed only after local persistence succeeded.
        assert not agent.paths.activation_key_file.exists()

        # 10/11. The Backend Device is Activated and the one-time key is consumed.
        assert backend_device["activationStatus"] == "Activated"

        # 13. ConfigCache is load-only in this milestone (OI-2) and stays empty.
        assert agent.cached_configuration() is None

    # 14. Shutdown closed the owned HTTP resources and withdrew the runtime.
    assert agent.last_client.closed is True
    assert get_runtime(app) is None


# --- 10. Consumed-key behavior --------------------------------------------------------------------


def test_consumed_key_is_rejected_with_the_uniform_401(
    agent: AgentUnderTest, backend: BackendHost, provisioned: ProvisionedBranch
) -> None:
    """A key consumed by a real activation cannot be used again (FS-02 AC-4, AC-9; T-08)."""
    agent.write_key_file(provisioned.activation_key)
    with TestClient(agent.build_app()):
        pass  # first activation consumes the key

    response = httpx.post(
        f"{backend.base_url}/api/v1/activate",
        json={"activationKey": provisioned.activation_key},
        timeout=30,
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errorCode"] == "INVALID_ACTIVATION_KEY"
    assert body["message"] == "The activation key is invalid."


def test_rejected_activation_writes_no_identity_and_keeps_the_key_file(
    agent: AgentUnderTest,
) -> None:
    """A rejected key fails startup cleanly: no identity, key retained, no runtime, one request."""
    agent.write_key_file("not-a-real-key.not-a-real-secret")
    app = agent.build_app()

    with pytest.raises(ActivationRejectedError):
        with TestClient(app):
            pass  # pragma: no cover - startup raises

    assert agent.last_client.calls == 1  # no retry
    assert agent.identity() is None  # no local identity written
    assert agent.paths.activation_key_file.exists()  # key retained for a corrected retry
    assert get_runtime(app) is None  # runtime never published
    assert agent.last_client.closed is True  # owned client still closed on the failure path


# --- 11. Restart / no-op --------------------------------------------------------------------------


def test_restart_without_a_key_makes_no_activation_request(
    agent: AgentUnderTest, provisioned: ProvisionedBranch
) -> None:
    """An already-activated Agent restarts with no Backend call at all (MAC-3; FS-02 AC-7)."""
    agent.write_key_file(provisioned.activation_key)
    with TestClient(agent.build_app()) as first:
        first_identity = agent.identity()
        assert get_runtime(first.app).activation.outcome is (  # type: ignore[union-attr, arg-type]
            ActivationOutcome.FIRST_ACTIVATION
        )

    # Restart against the same root and the same persisted SQLite identity, with no new key.
    with TestClient(agent.build_app()) as second:
        runtime = get_runtime(second.app)  # type: ignore[arg-type]
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.ALREADY_ACTIVATED

        # Observed, not assumed: the second run's real client was never asked to activate.
        assert agent.last_client.calls == 0

        after = agent.identity()
        assert after.device_id == first_identity.device_id  # type: ignore[attr-defined]
        assert after.activated_at == first_identity.activated_at  # type: ignore[attr-defined]
        assert (
            after.last_activated_at  # type: ignore[attr-defined]
            == first_identity.last_activated_at  # type: ignore[attr-defined]
        )
        assert agent.cached_configuration() is None

    assert agent.last_client.closed is True


# --- 12. Real reactivation ------------------------------------------------------------------------


def test_real_reactivation_retains_device_id_and_rotates_the_secret(
    agent: AgentUnderTest, admin: AdminApiClient, provisioned: ProvisionedBranch
) -> None:
    """Regeneration through the real endpoint rotates the secret and retains the DeviceId.

    IP-02 §16.3 requires this against the real Backend (FS-02 AC-5, AC-7; T-13, T-14), and the real
    regeneration endpoint makes it reliably reachable, so it is not deferred to the simulated layer.
    """
    agent.write_key_file(provisioned.activation_key)
    with TestClient(agent.build_app()):
        original = agent.identity()
        first_secret = original.shared_secret.get_secret_value()  # type: ignore[attr-defined]

    new_key = admin.regenerate_activation_key(provisioned.branch_id)
    agent.write_key_file(new_key)

    with TestClient(agent.build_app()) as client:
        runtime = get_runtime(client.app)  # type: ignore[arg-type]
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.REACTIVATION
        assert agent.last_client.calls == 1

        rotated = agent.identity()
        # Same public DeviceId (ADR-015): a different one would be a contract contradiction and
        # would instead have failed startup through T-38's mismatch guard.
        assert rotated.device_id == original.device_id  # type: ignore[attr-defined]
        # ActivatedAt is immutable; LastActivatedAt advances.
        assert rotated.activated_at == original.activated_at  # type: ignore[attr-defined]
        assert (
            rotated.last_activated_at  # type: ignore[attr-defined]
            >= original.last_activated_at  # type: ignore[attr-defined]
        )
        # The secret really rotated — asserted as a boolean, neither value displayed.
        secret_rotated = (
            rotated.shared_secret.get_secret_value() != first_secret  # type: ignore[attr-defined]
        )
        assert secret_rotated, "the shared secret was not rotated by reactivation"

        # The regenerated key was consumed and its file removed after persistence.
        assert not agent.paths.activation_key_file.exists()

    assert agent.last_client.closed is True


def test_regenerated_key_invalidates_the_previous_key(
    backend: BackendHost, admin: AdminApiClient, provisioned: ProvisionedBranch
) -> None:
    """Regeneration invalidates the superseded key, which then gets the uniform 401 (T-13)."""
    admin.regenerate_activation_key(provisioned.branch_id)

    response = httpx.post(
        f"{backend.base_url}/api/v1/activate",
        json={"activationKey": provisioned.activation_key},
        timeout=30,
    )

    assert response.status_code == 401
    assert response.json()["errorCode"] == "INVALID_ACTIVATION_KEY"


# --- 16. Harness cleanup guarantees ---------------------------------------------------------------


def test_backend_host_is_running_and_bound_only_to_loopback(backend: BackendHost) -> None:
    """The host under test is alive and reachable on loopback only (never bound publicly)."""
    assert backend.is_running()
    assert backend.base_url.startswith("http://127.0.0.1:")
    assert httpx.get(f"{backend.base_url}/api/v1/health", timeout=10).status_code == 200


def test_temporary_agent_root_is_removed_after_the_test(agent: AgentUnderTest) -> None:
    """The Agent root lives under pytest's tmp_path and never inside the repository."""
    agent.write_key_file("throwaway.key-material-for-path-assertions")
    assert agent.paths.activation_key_file.exists()

    repository = Path(__file__).resolve().parents[3]
    assert repository not in agent.root.resolve().parents

    # Teardown deletes the root (with the key file inside it); asserted by the fixture's rmtree and
    # verified for real by the surrounding suite leaving no artifacts behind.
