"""Unit tests for the Config Cache repository (IP-02 T-36, §16.1; OI-2).

The Config Cache repository is **load-only** — IP-02 excludes a writer under OI-2 — so these tests
seed a row directly with controlled SQL and then verify ``load()``. There are deliberately no
replace/clear/refresh tests: those operations are not part of T-36. Every test uses a temporary root
(``tmp_path``); none touches ``/opt`` or the network.
"""

from __future__ import annotations

import builtins
import importlib
import logging
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.errors import InvalidConfigCacheStateError

# A recognisable sentinel standing in for configuration content; it must never reach a log or error.
FAKE_CONFIG_JSON = '{"marker": "ZZZ-config-content-must-never-appear-ZZZ"}'
UPDATED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _seed_config(db: Path, *, config_json: str, updated_at: str) -> None:
    """Insert one ConfigCache row directly — the repository itself has no writer (OI-2)."""
    with open_connection(db) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
                (config_json, updated_at),
            )


# --- 1-5. Absent / present load ----------------------------------------------------------------


def test_absent_cache_returns_none(tmp_path: Path) -> None:
    repo = ConfigCacheRepository(_ready_db(tmp_path))

    assert repo.load() is None


def test_seeded_row_loads(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    loaded = ConfigCacheRepository(db).load()

    assert loaded is not None


def test_config_json_returned_exactly(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    loaded = ConfigCacheRepository(db).load()

    assert loaded is not None
    # Raw text is preserved verbatim — no parsing or reserialization (OI-2).
    assert loaded.config_json == FAKE_CONFIG_JSON


def test_updated_at_loads_as_utc_aware(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    loaded = ConfigCacheRepository(db).load()

    assert loaded is not None
    assert loaded.updated_at == UPDATED_AT
    assert loaded.updated_at.tzinfo is not None
    assert loaded.updated_at.utcoffset() == timezone.utc.utcoffset(None)


def test_exactly_one_row_and_singleton_guard(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    with open_connection(db) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM ConfigCache").fetchone()
        (guard,) = connection.execute("SELECT SingletonGuard FROM ConfigCache").fetchone()

    assert count == 1
    assert guard == 1


# --- 6-7. Invalid states rejected --------------------------------------------------------------


def test_malformed_timestamp_is_rejected(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at="not-a-timestamp")

    with pytest.raises(InvalidConfigCacheStateError):
        ConfigCacheRepository(db).load()


def test_schema_prevents_a_second_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    # The >1-row state the repository guards against is itself prevented by the schema singleton.
    with open_connection(db) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt) VALUES (1, ?, ?)",
                ("{}", UPDATED_AT.isoformat()),
            )


# --- 8-9. Config content never leaks -----------------------------------------------------------


def test_config_content_absent_from_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())

    with caplog.at_level(logging.DEBUG, logger="weapon_detection_agent"):
        ConfigCacheRepository(db).load()

    assert FAKE_CONFIG_JSON not in caplog.text
    assert "ZZZ-config-content-must-never-appear-ZZZ" not in caplog.text


def test_config_content_absent_from_exceptions(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at="not-a-timestamp")

    with pytest.raises(InvalidConfigCacheStateError) as excinfo:
        ConfigCacheRepository(db).load()

    assert "ZZZ-config-content-must-never-appear-ZZZ" not in str(excinfo.value)


# --- 10-12. Load is non-mutating and creates nothing -------------------------------------------


def test_repeated_loads_are_equivalent_and_non_mutating(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json=FAKE_CONFIG_JSON, updated_at=UPDATED_AT.isoformat())
    repo = ConfigCacheRepository(db)

    first = repo.load()
    second = repo.load()

    assert first == second
    # The stored row is unchanged by loading.
    with open_connection(db) as connection:
        row = connection.execute("SELECT ConfigJson, UpdatedAt FROM ConfigCache").fetchone()
    assert row["ConfigJson"] == FAKE_CONFIG_JSON
    assert row["UpdatedAt"] == UPDATED_AT.isoformat()


def test_load_creates_no_unrelated_files_or_directories(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    _seed_config(paths.database_file, config_json="{}", updated_at=UPDATED_AT.isoformat())

    ConfigCacheRepository(paths.database_file).load()

    assert sorted(c.name for c in paths.root.iterdir()) == ["config", "database", "logs"]
    assert [c.name for c in paths.database_dir.iterdir()] == ["agent.db"]


# --- 13. No network I/O ------------------------------------------------------------------------


def test_load_performs_no_network_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ready_db(tmp_path)
    _seed_config(db, config_json="{}", updated_at=UPDATED_AT.isoformat())

    def _forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("config cache load must not open a socket")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    assert ConfigCacheRepository(db).load() is not None


# --- 14. Importing the repository performs no filesystem I/O -----------------------------------


def test_repository_import_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing the repository must not perform I/O")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    name = "weapon_detection_agent.persistence.config_cache_repository"
    saved = sys.modules.pop(name, None)
    try:
        importlib.import_module(name)
    finally:
        if saved is not None:
            sys.modules[name] = saved
