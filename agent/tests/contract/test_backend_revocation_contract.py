"""Real-Backend revocation & reactivation contract suite (IP-05 P1 / T-63, §5, §10.5).

The simulated suite (``tests/integration/test_agent_revocation_reactivation.py``) proves the Agent's
*behaviour* against exact-but-simulated bytes; it cannot prove the **validation and revocation
contract is real**. These tests run the real Agent — its real ``CredentialValidationClient``, real
``CredentialValidationMonitor``, real ``BackendActivationClient``, real SQLite store, real FastAPI
lifespan — against the **actual** ASP.NET Core Backend over loopback HTTP, backed by a throwaway SQL
Server database, with the Branch, Activation Key, and key regeneration driven through the real
authenticated API. No simulator-specific accommodation exists anywhere in the Backend.

They complement (and never duplicate) the T-40 activation contract suite: here the focus is the
``POST /api/v1/device/credentials/validate`` endpoint and the full credential-revocation →
Agent-lock → manual-reactivation cycle.

Running these tests::

    WDA_RUN_BACKEND_CONTRACT_TESTS=1 python -m pytest -m backend_contract

Secret safety: the Activation Key and the device shared secret are never printed, placed in an
assertion message, written to a filename, or passed on a command line. Secret equality/inequality is
asserted as a boolean, never by displaying the values.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from support.backend_process import AdminApiClient, BackendHost, ProvisionedBranch
from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationOutcome
from weapon_detection_agent.app import create_app
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.runtime.state import get_runtime
from weapon_detection_agent.validation.client import (
    DEVICE_ID_HEADER,
    DEVICE_SECRET_HEADER,
    INVALID_DEVICE_CREDENTIALS_CODE,
    VALIDATION_PATH,
    CredentialValidationClient,
)
from weapon_detection_agent.validation.monitor import CredentialValidationMonitor

# Every test in this module needs the real Backend and SQL Server.
pytestmark = pytest.mark.backend_contract

DEVICE_CREDENTIALS_MESSAGE = "The device credentials are invalid."


class CountingClient(BackendActivationClient):
    """The real activation client, instrumented only to count calls and capture the result."""

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
                activation_key=None,
                http_timeout_seconds=30,
                credential_validation_interval_seconds=30,
                log_level="INFO",
            )

        return create_app(settings_loader=loader, backend_client_factory=factory)  # type: ignore[arg-type]

    @property
    def last_client(self) -> CountingClient:
        return self.clients[-1]

    @property
    def repo(self) -> DeviceIdentityRepository:
        return DeviceIdentityRepository(self.paths.database_file)

    def identity(self) -> DeviceIdentity | None:
        return self.repo.load()


@pytest.fixture
def agent(backend: BackendHost, tmp_path: Path) -> Iterator[AgentUnderTest]:
    under_test = AgentUnderTest(tmp_path / "weapon-detection", backend.base_url)
    try:
        yield under_test
    finally:
        logger = logging.getLogger("weapon_detection_agent")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
        shutil.rmtree(under_test.root, ignore_errors=True)


@pytest.fixture
def provisioned(admin: AdminApiClient, request: pytest.FixtureRequest) -> ProvisionedBranch:
    """A Branch with one Camera, one Device, and one valid one-time Activation Key (real API)."""
    return admin.create_branch(f"Revocation {request.node.name[:40]}")


# --- Helpers ------------------------------------------------------------------------------------


def _activate_agent(agent: AgentUnderTest, activation_key: str) -> DeviceIdentity:
    """Activate the real Agent through its real lifespan and return the persisted identity."""
    agent.write_key_file(activation_key)
    with TestClient(agent.build_app()):
        pass  # first activation persists the identity, then the key file is removed
    identity = agent.identity()
    assert identity is not None
    return identity


def _validate(backend: BackendHost, device_id: str, secret: SecretStr) -> object:
    """Classify a credential against the real endpoint through the real T-57 client."""

    async def _run() -> object:
        client = CredentialValidationClient(backend.base_url, timeout_seconds=30)
        try:
            return await client.validate(device_id, secret)
        finally:
            await client.aclose()

    return asyncio.run(_run())


# --- 2/4. The validation endpoint: live credential accepted, revoked credential uniformly 401 ---


def test_live_credential_is_accepted_and_a_wrong_secret_is_confirmed_rejected(
    agent: AgentUnderTest, backend: BackendHost, provisioned: ProvisionedBranch
) -> None:
    identity = _activate_agent(agent, provisioned.activation_key)
    assert identity.shared_secret is not None

    # The live credential validates.
    live = _validate(backend, identity.device_id, identity.shared_secret)
    assert live.is_valid  # type: ignore[attr-defined]

    # A deliberately wrong secret for the same real device is the uniform confirmed rejection.
    wrong = _validate(backend, identity.device_id, SecretStr("not-the-real-secret"))
    assert wrong.is_confirmed_rejected  # type: ignore[attr-defined]
    assert wrong.status_code == 401  # type: ignore[attr-defined]


# --- 3. Regeneration clears the Backend secret and sets ReactivationRequired --------------------


def test_regeneration_revokes_the_secret_and_sets_reactivation_required(
    agent: AgentUnderTest,
    admin: AdminApiClient,
    backend: BackendHost,
    provisioned: ProvisionedBranch,
) -> None:
    identity = _activate_agent(agent, provisioned.activation_key)
    assert identity.shared_secret is not None
    before = admin.device_summary(provisioned.branch_id)
    assert before["activationStatus"] == "Activated"

    admin.regenerate_activation_key(provisioned.branch_id)

    after = admin.device_summary(provisioned.branch_id)
    # Backend immediately becomes ReactivationRequired, keeping the permanent DeviceId (§4.1/§4.2).
    assert after["activationStatus"] == "ReactivationRequired"
    assert after["deviceId"] == before["deviceId"]

    # The now-revoked shared secret no longer authenticates against the validation endpoint.
    revoked = _validate(backend, identity.device_id, identity.shared_secret)
    assert revoked.is_confirmed_rejected  # type: ignore[attr-defined]

    # The prior (already-consumed) Activation Key is invalid too (uniform activation 401).
    reactivate_old = httpx.post(
        f"{backend.base_url}/api/v1/activate",
        json={"activationKey": provisioned.activation_key},
        timeout=30,
    )
    assert reactivate_old.status_code == 401
    assert reactivate_old.json()["errorCode"] == "INVALID_ACTIVATION_KEY"


def test_revoked_validation_returns_the_uniform_401_revealing_no_reason(
    agent: AgentUnderTest,
    admin: AdminApiClient,
    backend: BackendHost,
    provisioned: ProvisionedBranch,
) -> None:
    """The raw wire envelope of a revoked-credential validation — byte-shape and non-disclosure."""
    identity = _activate_agent(agent, provisioned.activation_key)
    assert identity.shared_secret is not None
    admin.regenerate_activation_key(provisioned.branch_id)

    response = httpx.post(
        f"{backend.base_url}{VALIDATION_PATH}",
        headers={
            DEVICE_ID_HEADER: identity.device_id,
            DEVICE_SECRET_HEADER: identity.shared_secret.get_secret_value(),
        },
        timeout=30,
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errorCode"] == INVALID_DEVICE_CREDENTIALS_CODE
    assert body["message"] == DEVICE_CREDENTIALS_MESSAGE
    # The rejection reason is never disclosed: no ReactivationRequired detail, no secret, no data.
    serialized = response.text
    assert "ReactivationRequired" not in serialized
    assert identity.shared_secret.get_secret_value() not in serialized
    assert "data" not in body  # WhenWritingNull omits the null data member


# --- 5. The real Agent processes the confirmed rejection: clears the secret and locks -----------


def test_agent_monitor_locks_on_the_real_confirmed_rejection(
    agent: AgentUnderTest,
    admin: AdminApiClient,
    backend: BackendHost,
    provisioned: ProvisionedBranch,
) -> None:
    """A real validation monitor, run against the real revoked credential, locks the runtime."""
    original = _activate_agent(agent, provisioned.activation_key)
    admin.regenerate_activation_key(provisioned.branch_id)  # revoke while the Agent still holds it

    async def _run_monitor() -> object:
        client = CredentialValidationClient(backend.base_url, timeout_seconds=30)
        sleeper_called: list[float] = []

        async def _sleeper(
            delay: float,
        ) -> None:  # pragma: no cover - a confirmed 401 ends the loop
            sleeper_called.append(delay)

        monitor = CredentialValidationMonitor(
            identity_repository=agent.repo,
            validation_client=client,
            interval_seconds=30,
            sleeper=_sleeper,
        )
        try:
            result = await monitor.run()
        finally:
            await client.aclose()
        assert sleeper_called == []  # the confirmed rejection ends the loop with no interval wait
        return result

    result = asyncio.run(_run_monitor())
    assert result.is_reactivation_required  # type: ignore[attr-defined]

    locked = agent.identity()
    assert locked is not None
    assert locked.operational_state is OperationalState.REACTIVATION_REQUIRED  # persisted lock
    assert locked.shared_secret is None  # local secret cleared to NULL
    assert locked.device_id == original.device_id  # DeviceId preserved
    assert locked.activated_at == original.activated_at  # ActivatedAt preserved
    assert locked.last_activated_at == original.last_activated_at  # unchanged by a lock


# --- 7/8/9. Manual reactivation with the new key restores Operational, rotating the secret ------


def test_real_reactivation_restores_operational_and_rotates_the_secret(
    agent: AgentUnderTest,
    admin: AdminApiClient,
    backend: BackendHost,
    provisioned: ProvisionedBranch,
) -> None:
    original = _activate_agent(agent, provisioned.activation_key)
    original_secret = original.shared_secret
    assert original_secret is not None

    new_key = admin.regenerate_activation_key(provisioned.branch_id)
    agent.write_key_file(new_key)

    with TestClient(agent.build_app()) as client:
        runtime = get_runtime(client.app)  # type: ignore[arg-type]
        assert runtime is not None
        assert runtime.activation.outcome is ActivationOutcome.REACTIVATION
        assert agent.last_client.calls == 1  # exactly one activation request, no retry

        reactivated = agent.identity()
        assert reactivated is not None
        assert reactivated.device_id == original.device_id  # same DeviceId
        assert reactivated.activated_at == original.activated_at  # ActivatedAt preserved
        assert reactivated.last_activated_at >= original.last_activated_at  # advanced
        assert reactivated.operational_state is OperationalState.OPERATIONAL  # unlocked
        assert reactivated.shared_secret is not None
        secret_rotated = (
            reactivated.shared_secret.get_secret_value() != original_secret.get_secret_value()
        )
        assert secret_rotated, "the shared secret was not rotated by reactivation"
        # The regenerated key file was consumed only after local persistence succeeded.
        assert not agent.paths.activation_key_file.exists()

    # The Backend Device is Activated again, and the rotated secret now validates.
    summary = admin.device_summary(provisioned.branch_id)
    assert summary["activationStatus"] == "Activated"
    assert summary["deviceId"] == original.device_id

    rotated_identity = agent.identity()
    assert rotated_identity is not None and rotated_identity.shared_secret is not None
    revalidated = _validate(backend, rotated_identity.device_id, rotated_identity.shared_secret)
    assert revalidated.is_valid  # type: ignore[attr-defined]
    assert agent.last_client.closed is True


# --- 11. No key or secret ever appears in logs across the revocation/reactivation flow ----------


def test_no_key_or_secret_in_logs_across_the_real_flow(
    agent: AgentUnderTest,
    admin: AdminApiClient,
    provisioned: ProvisionedBranch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        original = _activate_agent(agent, provisioned.activation_key)
        first_secret = original.shared_secret
        assert first_secret is not None

        new_key = admin.regenerate_activation_key(provisioned.branch_id)
        agent.write_key_file(new_key)
        with TestClient(agent.build_app()):
            pass
        rotated = agent.identity()
        assert rotated is not None and rotated.shared_secret is not None

    rendered = caplog.text + "\n".join(str(record.__dict__) for record in caplog.records)
    for forbidden in (
        provisioned.activation_key,
        new_key,
        first_secret.get_secret_value(),
        rotated.shared_secret.get_secret_value(),
    ):
        assert forbidden not in rendered
