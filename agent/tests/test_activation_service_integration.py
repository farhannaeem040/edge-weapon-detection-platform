"""Integration-style test for the activation service (IP-02 T-38, §20).

Wires the real T-34 paths, T-35 schema, and T-36 repository over a temporary SQLite database against
a **fake** Backend client (no network — real contract integration is T-40). Drives the full
first-activation → restart-no-op → reactivation sequence and asserts the on-disk effects. No secret
value is printed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from pydantic import SecretStr

from weapon_detection_agent.activation.key_resolver import ActivationKeyResolver
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import ActivationOutcome, ActivationService
from weapon_detection_agent.config.paths import AgentPaths, resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository

DEVICE_ID = "device-integration-001"
SECRET_1 = "integration-secret-one-ZZZ"
SECRET_2 = "integration-secret-two-ZZZ"
BRANCH_ID = "branch-integration-001"
T_FIRST = datetime(2026, 1, 1, tzinfo=timezone.utc)
T_REACTIVATE = datetime(2026, 6, 1, tzinfo=timezone.utc)


class FakeBackendClient:
    def __init__(self, result: ActivationResult) -> None:
        self._result = result
        self.calls = 0

    async def activate(self, activation_key: SecretStr) -> ActivationResult:
        self.calls += 1
        return self._result


def _service(paths: AgentPaths, backend: object, clock: object) -> ActivationService:
    return ActivationService(
        backend_client=backend,  # type: ignore[arg-type]
        identity_repository=DeviceIdentityRepository(paths.database_file),
        key_resolver=ActivationKeyResolver(
            environment_key=None, key_file_path=paths.activation_key_file
        ),
        clock=clock,  # type: ignore[arg-type]
    )


def _identity_count(paths: AgentPaths) -> int:
    with open_connection(paths.database_file) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()
    return int(count)


def _seed_config(paths: AgentPaths) -> tuple[str, str]:
    updated = T_FIRST.isoformat()
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


def test_full_activation_lifecycle(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    config_before = _seed_config(paths)

    # 1. First activation from a file key.
    paths.activation_key_file.write_text("first-key", encoding="utf-8")
    first_backend = FakeBackendClient(
        ActivationResult(
            device_id=DEVICE_ID, shared_secret=SecretStr(SECRET_1), branch_id=BRANCH_ID
        )
    )
    first = asyncio.run(_service(paths, first_backend, lambda: T_FIRST).activate())

    assert first.outcome is ActivationOutcome.FIRST_ACTIVATION
    assert first_backend.calls == 1
    repo = DeviceIdentityRepository(paths.database_file)
    stored = repo.load()
    assert stored is not None and stored.device_id == DEVICE_ID
    # 2. File key removed after successful storage.
    assert not paths.activation_key_file.exists()

    # 3. Second run without a new key → no Backend call, no change (normal restart).
    restart_backend = FakeBackendClient(
        ActivationResult(
            device_id=DEVICE_ID, shared_secret=SecretStr(SECRET_2), branch_id=BRANCH_ID
        )
    )
    restart = asyncio.run(_service(paths, restart_backend, lambda: T_REACTIVATE).activate())
    assert restart.outcome is ActivationOutcome.ALREADY_ACTIVATED
    assert restart_backend.calls == 0

    # 4. A newly provisioned regeneration key triggers reactivation.
    paths.activation_key_file.write_text("second-key", encoding="utf-8")
    react_backend = FakeBackendClient(
        ActivationResult(
            device_id=DEVICE_ID, shared_secret=SecretStr(SECRET_2), branch_id=BRANCH_ID
        )
    )
    react = asyncio.run(_service(paths, react_backend, lambda: T_REACTIVATE).activate())

    # 5-6. Matching Device ID → only the secret and LastActivatedAt change; ActivatedAt preserved.
    assert react.outcome is ActivationOutcome.REACTIVATION
    assert react_backend.calls == 1
    reactivated = repo.load()
    assert reactivated is not None
    assert reactivated.device_id == DEVICE_ID
    assert reactivated.activated_at == T_FIRST
    assert reactivated.last_activated_at == T_REACTIVATE
    assert reactivated.shared_secret.get_secret_value() == SECRET_2

    # 7. ConfigCache untouched throughout.
    assert _config_row(paths) == config_before
    # 8. Exactly one DeviceIdentity row.
    assert _identity_count(paths) == 1
    # 9. No secret printed anywhere in this test.
