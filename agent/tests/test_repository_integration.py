"""Cross-repository integration tests (IP-02 T-36).

These exercise both repositories against one database to prove they are independent and leak-free.
Setup always: resolve paths (T-34) → provision → initialize schema (T-35) → construct repositories
with the explicit database path. Temporary roots only; never ``/opt``.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.errors import IdentityAlreadyExistsError
from weapon_detection_agent.persistence.models import DeviceIdentity

FAKE_SECRET_A = "ZZZ-fake-secret-A-ZZZ"
FAKE_SECRET_B = "ZZZ-fake-secret-B-ZZZ"
DEVICE_ID = "device-abcdefab-cdef-abcd-efab-cdefabcdefab"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 2, 2, tzinfo=timezone.utc)
CONFIG_JSON = '{"k": "v"}'


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _identity(secret: str = FAKE_SECRET_A) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=DEVICE_ID,
        shared_secret=SecretStr(secret),
        activated_at=T0,
        last_activated_at=T0,
    )


def _seed_config(db: Path) -> None:
    with open_connection(db) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
                (CONFIG_JSON, T0.isoformat()),
            )


def test_identity_and_config_cache_coexist(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())
    _seed_config(db)

    identity = DeviceIdentityRepository(db).load()
    config = ConfigCacheRepository(db).load()

    assert identity is not None and identity.device_id == DEVICE_ID
    assert config is not None and config.config_json == CONFIG_JSON


def test_loading_config_does_not_change_identity(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())
    _seed_config(db)

    ConfigCacheRepository(db).load()

    identity = DeviceIdentityRepository(db).load()
    assert identity is not None
    assert identity.device_id == DEVICE_ID
    assert identity.shared_secret.get_secret_value() == FAKE_SECRET_A


def test_replacing_secret_does_not_change_config(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())
    _seed_config(db)

    DeviceIdentityRepository(db).replace_shared_secret(
        shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=T1
    )

    config = ConfigCacheRepository(db).load()
    assert config is not None
    assert config.config_json == CONFIG_JSON
    assert config.updated_at == T0


def test_repositories_do_not_leak_connections(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())
    _seed_config(db)

    # Construct fresh repositories repeatedly and operate; if any left a connection (and thus a
    # transaction/lock) open, a subsequent exclusive write would fail. It does not.
    for _ in range(25):
        DeviceIdentityRepository(db).load()
        ConfigCacheRepository(db).load()

    with open_connection(db) as connection:
        with transaction(connection):
            connection.execute(
                "UPDATE ConfigCache SET UpdatedAt = ? WHERE SingletonGuard = 1", (T1.isoformat(),)
            )
    # The write succeeded, confirming no lingering connection blocked it.
    assert ConfigCacheRepository(db).load() is not None


def test_database_usable_after_handled_error(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = DeviceIdentityRepository(db)
    repo.store(_identity())

    with pytest.raises(IdentityAlreadyExistsError):
        repo.store(_identity(secret=FAKE_SECRET_B))

    # Both repositories keep working against the same database after the handled error.
    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=T1)
    identity = repo.load()
    assert identity is not None
    assert identity.shared_secret.get_secret_value() == FAKE_SECRET_B


def test_no_settings_or_global_env_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Repositories must work with no WDA_ environment at all — they take an explicit path.
    for name in (
        "WDA_BACKEND_BASE_URL",
        "WDA_ACTIVATION_KEY",
        "WDA_ROOT_PATH",
        "WDA_HTTP_TIMEOUT_SECONDS",
        "WDA_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    db = _ready_db(tmp_path)
    DeviceIdentityRepository(db).store(_identity())
    assert DeviceIdentityRepository(db).load() is not None


def test_operations_perform_no_network_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ready_db(tmp_path)

    def _forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("repository operations must not open a socket")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    repo = DeviceIdentityRepository(db)
    repo.store(_identity())
    repo.replace_shared_secret(shared_secret=SecretStr(FAKE_SECRET_B), last_activated_at=T1)
    assert repo.load() is not None
