"""Simulated-Backend revocation & reactivation contract suite (IP-05 P1 / T-62, §5-§10).

The existing ``test_agent_activation_lifespan.py`` proves the *activation* wire against a simulated
Backend but stubs the validation client; the per-task unit suites prove each piece (T-57 client,
T-58 repository, T-59 monitor, T-60 coordinator, T-61 supervisor) with fakes. What no existing suite
proves is the **whole revocation-and-reactivation lifecycle running against real JSON bytes** — the
real :class:`BackendActivationClient` *and* the real :class:`CredentialValidationClient`, the real
repository/service/monitor/coordinator, and a real operational component — so a mis-encoded
validation envelope, a mis-classified real 401, or a broken lock/reactivation hand-off would be
caught here where a fake could hide it.

A single stateful :class:`SimulatedBackend` serves **both** the exact ``POST /api/v1/activate`` and
``POST /api/v1/device/credentials/validate`` contracts (§5, §11) over ``httpx.MockTransport``. It is
a double for the *Backend* only — every Agent component under test is real, driven through the T-61
:class:`AgentRuntimeSupervisor` across multiple "boots" against one persistent temporary SQLite
database (never the Jetson database).

No real interval elapses: the monitor's wait seam is a scripted/injected sleeper. Every secret and
key is an obvious placeholder and must never appear in a log, ``repr``, or exception — the
secret-safety test asserts this. This suite opens no socket beyond the in-memory MockTransport.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from weapon_detection_agent.activation.backend_client import BackendActivationClient
from weapon_detection_agent.activation.errors import DeviceIdentityMismatchError
from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.service import ActivationOutcome, ActivationService
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import OperationalState
from weapon_detection_agent.runtime.supervisor import AgentRuntimeSupervisor, StartupBranch
from weapon_detection_agent.validation.client import (
    INVALID_DEVICE_CREDENTIALS_CODE,
    VALIDATION_PATH,
    CredentialValidationClient,
)
from weapon_detection_agent.validation.monitor import CredentialValidationMonitor

DEVICE_ID = "device-revoke-11111111"
OTHER_DEVICE_ID = "device-revoke-99999999"
BRANCH_ID = "branch-revoke-22222222"
KEY_1 = "revokekeyid1.revoke-secret-one-ZZZ"  # noqa: S105 - placeholder
KEY_2 = "revokekeyid2.revoke-secret-two-ZZZ"  # noqa: S105 - placeholder
KEY_3 = "revokekeyid3.revoke-secret-three-ZZZ"  # noqa: S105 - placeholder
ACTIVATE_PATH = "/api/v1/activate"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 6, 1, tzinfo=timezone.utc)


# --- The stateful simulated Backend (both endpoints) -------------------------------------------


class SimulatedBackend:
    """The exact §11 activation contract and §5 validation contract, with one-time-key + revocation
    state, over an in-memory transport."""

    def __init__(self) -> None:
        self._keys: dict[str, tuple[str, str]] = {}
        self._consumed: set[str] = set()
        # The single live secret for the device (mirrors the Backend's ProtectedSharedSecret); None
        # once revoked (regeneration), exactly as ReactivationRequired clears it (§4.2).
        self._current_secret: str | None = None
        self._secret_seq = 0
        self.issued_secrets: list[str] = []
        self.activate_count = 0
        self.validate_count = 0
        self.validation_mode = "normal"  # normal | server | timeout | malformed

    def register_key(
        self, key: str, *, device_id: str = DEVICE_ID, branch_id: str = BRANCH_ID
    ) -> None:
        self._keys[key] = (device_id, branch_id)

    def regenerate(self, new_key: str, *, device_id: str = DEVICE_ID) -> None:
        """Mirror an Admin key regeneration on an activated device (FS-02 §5.3, §4.2).

        The live shared secret is revoked immediately (cleared), and a fresh one-time key is issued;
        the permanent DeviceId is preserved. This is exactly what makes the next validation of the
        old secret a confirmed rejection.
        """
        self._current_secret = None
        self.register_key(new_key, device_id=device_id)

    @property
    def current_secret(self) -> str | None:
        return self._current_secret

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == ACTIVATE_PATH:
            return self._activate(request)
        if request.url.path == VALIDATION_PATH:
            return self._validate(request)
        return httpx.Response(404)  # pragma: no cover - no other route is ever called

    # -- POST /api/v1/activate ------------------------------------------------------------------

    def _activate(self, request: httpx.Request) -> httpx.Response:
        self.activate_count += 1
        assert request.method == "POST"
        assert "authorization" not in {name.lower() for name in request.headers}
        body = json.loads(request.content)
        assert list(body.keys()) == ["activationKey"]
        key = body["activationKey"]

        if not isinstance(key, str) or key in self._consumed or key not in self._keys:
            return _reject_activation()

        device_id, branch_id = self._keys[key]
        self._consumed.add(key)  # one-time key
        self._secret_seq += 1
        secret = f"revoke-issued-secret-{self._secret_seq}-ZZZ"
        self.issued_secrets.append(secret)
        self._current_secret = secret
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"deviceId": device_id, "sharedSecret": secret, "branchId": branch_id},
            },
        )

    # -- POST /api/v1/device/credentials/validate -----------------------------------------------

    def _validate(self, request: httpx.Request) -> httpx.Response:
        self.validate_count += 1
        if self.validation_mode == "timeout":
            raise httpx.ReadTimeout("simulated validation timeout", request=request)
        if self.validation_mode == "server":
            return httpx.Response(503)
        if self.validation_mode == "malformed":
            return httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )

        assert request.method == "POST"
        assert not request.content  # bodyless POST — credentials travel only in the headers
        device_id = request.headers.get("X-Device-Id")
        secret = request.headers.get("X-Device-Secret")

        # 200 only when the presented secret is the current live one for this device; every other
        # case (revoked/cleared secret, wrong secret, unknown device) is the uniform confirmed 401.
        if (
            device_id == DEVICE_ID
            and self._current_secret is not None
            and secret == self._current_secret
        ):
            return httpx.Response(200, json={"success": True, "data": None})
        return httpx.Response(
            401,
            json={
                "success": False,
                "message": "The device credentials are invalid.",
                "errorCode": INVALID_DEVICE_CREDENTIALS_CODE,
            },
        )


def _reject_activation() -> httpx.Response:
    return httpx.Response(
        401,
        json={
            "success": False,
            "message": "The activation key is invalid.",
            "errorCode": "INVALID_ACTIVATION_KEY",
        },
    )


# --- Test doubles: operational component and controllable sleepers -----------------------------


class FakeComponent:
    """A deterministic operational component with start/stop counters and running state."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.start_count = 0
        self.stop_count = 0
        self.running = False

    async def start(self) -> None:
        self.start_count += 1
        self.running = True

    async def stop(self) -> None:
        self.stop_count += 1
        self.running = False


class _BlockingSleeper:
    """Blocks forever on the first wait, so the monitor validates once and then idles.

    Used for a boot whose monitor should simply run (validate once) and be cleanly cancelled by the
    supervisor's shutdown — mirroring production where the interval is real seconds.
    """

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.Event().wait()  # never set — cancelled on shutdown


class _RevokingSleeper:
    """Triggers a Backend regeneration on the ``at_wait``-th wait, then yields.

    This makes the *running* monitor observe a revocation that happens while operational: the first
    validation is Valid, the sleeper revokes the secret during the interval wait, and the next
    validation receives the confirmed 401 — a bounded, one-interval detection, never instantaneous.
    """

    def __init__(self, sim: SimulatedBackend, *, new_key: str, at_wait: int = 1) -> None:
        self._sim = sim
        self._new_key = new_key
        self._at_wait = at_wait
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) == self._at_wait:
            self._sim.regenerate(self._new_key)
        await asyncio.sleep(0)  # hand control back without consuming real time


class _StopLoop(Exception):
    """A private sentinel to end an otherwise-infinite monitor loop (not a lifecycle outcome)."""


class _ScriptedSleeper:
    """Records every requested delay; stops after ``max_waits`` waits (for direct monitor runs)."""

    def __init__(self, max_waits: int) -> None:
        self._max_waits = max_waits
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self._max_waits:
            raise _StopLoop
        await asyncio.sleep(0)


# --- Harness: real clients over the simulated Backend, one persistent SQLite database ----------


@dataclass
class _Boot:
    supervisor: AgentRuntimeSupervisor
    component: FakeComponent


@dataclass
class _Harness:
    """Boots a real supervisor (real clients over the sim) against one persistent Agent root."""

    sim: SimulatedBackend
    paths: AgentPaths
    _clients: list[httpx.AsyncClient] = field(default_factory=list)

    @property
    def repo(self) -> DeviceIdentityRepository:
        return DeviceIdentityRepository(self.paths.database_file)

    def write_key(self, key: str) -> None:
        self.paths.activation_key_file.write_text(key, encoding="utf-8")

    def _new_transport_client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.sim.handler))
        self._clients.append(client)
        return client

    def build_validation_client(self) -> CredentialValidationClient:
        return CredentialValidationClient(
            "http://sim.backend:5230", timeout_seconds=5, http_client=self._new_transport_client()
        )

    def boot(
        self,
        *,
        clock: datetime,
        sleeper: object,
        component_name: str = "deepstream",
    ) -> _Boot:
        """Construct a fresh supervisor with real activation and validation clients over the sim."""
        settings = load_settings(
            backend_base_url="http://sim.backend:5230",
            root_path=str(self.paths.root),
            activation_key=None,
            http_timeout_seconds=5,
            credential_validation_interval_seconds=30,
            log_level="INFO",
        )
        resolver = ActivationKeyResolver(
            environment_key=None, key_file_path=self.paths.activation_key_file
        )
        activation_client = BackendActivationClient(
            settings.backend_base_url, timeout_seconds=5, http_client=self._new_transport_client()
        )
        service = ActivationService(
            backend_client=activation_client,
            identity_repository=self.repo,
            key_resolver=resolver,
            clock=lambda: clock,
        )
        validation_client = self.build_validation_client()
        component = FakeComponent(component_name)

        def monitor_factory() -> CredentialValidationMonitor:
            return CredentialValidationMonitor(
                identity_repository=self.repo,
                validation_client=validation_client,
                interval_seconds=settings.credential_validation_interval_seconds,
                sleeper=sleeper,  # type: ignore[arg-type]
            )

        supervisor = AgentRuntimeSupervisor(
            settings=settings,
            identity_repository=self.repo,
            key_resolver=resolver,
            activation_service=service,
            validation_client=validation_client,
            components=(component,),
            monitor_factory=monitor_factory,
            owns_validation_client=False,  # the harness closes every transport client itself
        )
        return _Boot(supervisor, component)

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()


@pytest.fixture
def harness(tmp_path: Path) -> _Harness:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return _Harness(sim=SimulatedBackend(), paths=paths)


@pytest.fixture(autouse=True)
def _restore_agent_logging() -> object:
    yield
    logger = logging.getLogger("weapon_detection_agent")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


# --- 1-6. The full connected revocation & manual-reactivation lifecycle ------------------------


def test_full_revocation_and_reactivation_lifecycle(harness: _Harness) -> None:
    """Activate → operate → running monitor detects revocation → lock → restart locked → reactivate.

    One end-to-end flow over real activation and validation wire bytes, proving every hand-off the
    scope enumerates in a single coherent lifecycle across three Agent boots.
    """

    async def _scenario() -> None:
        sim = harness.sim
        sim.register_key(KEY_1)

        # --- Boot 1: first activation, then a revocation the running monitor detects -----------
        harness.write_key(KEY_1)
        boot1 = harness.boot(clock=T0, sleeper=_RevokingSleeper(sim, new_key=KEY_2))
        await boot1.supervisor.startup()

        assert boot1.supervisor.startup_branch is StartupBranch.FIRST_ACTIVATION
        assert boot1.supervisor.activation_result.outcome is ActivationOutcome.FIRST_ACTIVATION
        assert sim.activate_count == 1  # exactly one activation request
        assert boot1.component.start_count == 1  # operational component started
        assert boot1.supervisor.is_monitor_running is True
        assert not harness.paths.activation_key_file.exists()  # key deleted only after persistence

        operational = harness.repo.load()
        assert operational is not None
        assert operational.operational_state is OperationalState.OPERATIONAL
        assert operational.shared_secret is not None
        assert operational.shared_secret.get_secret_value() == sim.issued_secrets[0]

        # The running monitor: Valid → (revocation happens during the wait) → confirmed 401 → lock.
        await boot1.supervisor._monitor_task
        assert sim.validate_count >= 2  # detection took at least one interval, never instantaneous
        assert sim.activate_count == 1  # detecting a revocation performs no activation

        locked = harness.repo.load()
        assert locked is not None
        assert locked.operational_state is OperationalState.REACTIVATION_REQUIRED
        assert locked.shared_secret is None  # local secret cleared to NULL
        assert locked.device_id == DEVICE_ID  # DeviceId preserved
        assert locked.activated_at == operational.activated_at  # timestamps preserved
        assert locked.last_activated_at == operational.last_activated_at
        assert boot1.component.stop_count == 1  # operational component stopped
        assert boot1.supervisor.is_monitor_running is False  # monitor terminated
        assert boot1.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        await boot1.supervisor.shutdown()

        # --- Boot 2: restart while locked, no key → Branch C (stays locked, contacts nobody) ----
        activate_before = sim.activate_count
        validate_before = sim.validate_count
        boot2 = harness.boot(clock=T1, sleeper=_BlockingSleeper())
        await boot2.supervisor.startup()

        assert boot2.supervisor.startup_branch is StartupBranch.LOCKED_REACTIVATION_REQUIRED
        assert boot2.supervisor.state is OperationalState.REACTIVATION_REQUIRED
        assert sim.activate_count == activate_before  # no activation request
        assert sim.validate_count == validate_before  # no validation request (no NULL secret sent)
        assert boot2.component.start_count == 0  # no operational components
        assert boot2.supervisor.is_monitor_running is False  # no monitor while locked
        # The control process stays alive: startup returned normally rather than raising.
        await boot2.supervisor.shutdown()

        # --- Boot 3: operator provisions the new key and restarts → manual reactivation ---------
        harness.write_key(KEY_2)
        boot3 = harness.boot(clock=T2, sleeper=_BlockingSleeper())
        await boot3.supervisor.startup()

        assert boot3.supervisor.startup_branch is StartupBranch.REACTIVATION
        assert boot3.supervisor.activation_result.outcome is ActivationOutcome.REACTIVATION
        assert sim.activate_count == activate_before + 1  # exactly one activation request

        reactivated = harness.repo.load()
        assert reactivated is not None
        assert reactivated.device_id == DEVICE_ID  # same DeviceId
        assert reactivated.activated_at == operational.activated_at  # ActivatedAt preserved
        assert reactivated.last_activated_at == T2  # LastActivatedAt advanced
        assert reactivated.operational_state is OperationalState.OPERATIONAL  # lock cleared
        assert reactivated.shared_secret is not None
        assert reactivated.shared_secret.get_secret_value() == sim.issued_secrets[-1]  # rotated
        assert reactivated.shared_secret.get_secret_value() != sim.issued_secrets[0]
        assert not harness.paths.activation_key_file.exists()  # key deleted after persistence
        assert boot3.component.start_count == 1  # operational components resume
        assert boot3.supervisor.is_monitor_running is True  # monitor resumes
        await boot3.supervisor.shutdown()

    _run(harness, _scenario)


# --- 4. Indeterminate validation outcomes never lock (real client, real bytes) -----------------


@pytest.mark.parametrize("validation_mode", ["server", "timeout", "malformed"])
def test_indeterminate_validation_never_locks(harness: _Harness, validation_mode: str) -> None:
    """A real 503 / timeout / malformed validation response is Indeterminate, never a lock.

    The real T-57 client classifies the real bytes; the real T-59 monitor keeps polling without
    clearing the secret or locking — Backend unavailability must never be read as revocation (§9).
    """

    async def _scenario() -> None:
        sim = harness.sim
        sim.register_key(KEY_1)

        # Get to Operational first (one real activation), then make validation indeterminate.
        harness.write_key(KEY_1)
        boot = harness.boot(clock=T0, sleeper=_BlockingSleeper())
        await boot.supervisor.startup()
        await boot.supervisor.shutdown()  # stop the operational monitor; drive one directly next

        before = harness.repo.load()
        assert before is not None and before.operational_state is OperationalState.OPERATIONAL

        sim.validation_mode = validation_mode
        sleeper = _ScriptedSleeper(max_waits=3)  # let it poll a few times, then stop
        monitor = CredentialValidationMonitor(
            identity_repository=harness.repo,
            validation_client=harness.build_validation_client(),
            interval_seconds=30,
            sleeper=sleeper,  # type: ignore[arg-type]
        )

        with pytest.raises(_StopLoop):
            await monitor.run()

        # It retried each interval and never locked or cleared the secret.
        assert sleeper.delays == [30, 30, 30]
        after = harness.repo.load()
        assert after is not None
        assert after.operational_state is OperationalState.OPERATIONAL  # never locked
        assert after.shared_secret is not None  # secret not cleared
        assert after.shared_secret.get_secret_value() == before.shared_secret.get_secret_value()

    _run(harness, _scenario)


# --- 7. A reactivation whose returned DeviceId differs keeps the lock and the key file ----------


def test_reactivation_with_mismatched_device_id_stays_locked(harness: _Harness) -> None:
    """If the new key reactivates a *different* DeviceId, startup fails and nothing is changed.

    The lock and the operator-provided key file are both preserved so a corrected key can be tried;
    no operational component starts and no monitor runs.
    """

    async def _scenario() -> None:
        sim = harness.sim
        sim.register_key(KEY_1)

        # Activate, then revoke, so the Agent is locked awaiting a manual reactivation.
        harness.write_key(KEY_1)
        boot1 = harness.boot(clock=T0, sleeper=_RevokingSleeper(sim, new_key=KEY_2))
        await boot1.supervisor.startup()
        await boot1.supervisor._monitor_task
        await boot1.supervisor.shutdown()
        locked_before = harness.repo.load()
        assert locked_before is not None
        assert locked_before.operational_state is OperationalState.REACTIVATION_REQUIRED

        # The provisioned key resolves to a *different* device (a swapped/wrong key).
        sim.register_key(KEY_3, device_id=OTHER_DEVICE_ID)
        harness.write_key(KEY_3)
        boot2 = harness.boot(clock=T2, sleeper=_BlockingSleeper())

        with pytest.raises(DeviceIdentityMismatchError):
            await boot2.supervisor.startup()

        after = harness.repo.load()
        assert after is not None
        assert after.device_id == DEVICE_ID  # unchanged
        assert after.operational_state is OperationalState.REACTIVATION_REQUIRED  # still locked
        assert after.shared_secret is None  # still cleared
        assert harness.paths.activation_key_file.exists()  # key file kept for a corrected retry
        assert boot2.component.start_count == 0  # nothing operational started
        assert boot2.supervisor.is_monitor_running is False

    _run(harness, _scenario)


# --- 8. No key or secret ever appears in logs across the whole lifecycle -----------------------


def test_no_key_or_secret_in_logs_across_lifecycle(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    async def _scenario() -> None:
        sim = harness.sim
        sim.register_key(KEY_1)
        harness.write_key(KEY_1)

        with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
            boot1 = harness.boot(clock=T0, sleeper=_RevokingSleeper(sim, new_key=KEY_2))
            await boot1.supervisor.startup()
            await boot1.supervisor._monitor_task
            await boot1.supervisor.shutdown()

            harness.write_key(KEY_2)
            boot3 = harness.boot(clock=T2, sleeper=_BlockingSleeper())
            await boot3.supervisor.startup()
            await boot3.supervisor.shutdown()

        rendered = caplog.text
        rendered += "\n".join(str(record.__dict__) for record in caplog.records)
        for forbidden in (KEY_1, KEY_2, *sim.issued_secrets):
            assert forbidden not in rendered

    _run(harness, _scenario)


def _run(harness: _Harness, scenario: object) -> None:
    """Run an async scenario and always close every transport client afterwards."""

    async def _wrapped() -> None:
        try:
            await scenario()  # type: ignore[operator]
        finally:
            await harness.aclose()

    asyncio.run(_wrapped())
