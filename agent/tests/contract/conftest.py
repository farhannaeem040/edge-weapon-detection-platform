"""Fixtures for the real-Backend contract suite (IP-02 T-40, §16.3).

One Backend process and one throwaway database are shared by the whole module: launching the real
application and migrating a SQL Server database costs seconds, and each test provisions its own
Branch through the real API, so the tests remain independent without paying that cost repeatedly.

Every resource is torn down in a ``finally``-equivalent (fixture teardown), including on failure.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from support.backend_process import (
    DEFAULT_SQL_SERVER,
    SQL_SERVER_VAR,
    AdminApiClient,
    BackendHost,
    ContractEnvironmentError,
    ThrowawayDatabase,
    apply_migrations,
    build_backend,
    contract_skip_reason,
    new_throwaway_database,
    require_tooling,
)


@pytest.fixture(scope="session")
def sql_server() -> str:
    """The SQL Server instance hosting the throwaway database (never a credential)."""
    return os.environ.get(SQL_SERVER_VAR) or DEFAULT_SQL_SERVER


@pytest.fixture(scope="session")
def throwaway_database(sql_server: str) -> Iterator[ThrowawayDatabase]:
    """Create a uniquely named database, migrate it, and drop it afterwards.

    A skip is decided *before* anything is created. Once the suite is enabled, any failure is a
    hard error — an explicitly requested contract run never degrades into a silent pass.
    """
    reason = contract_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    require_tooling()

    database = new_throwaway_database(sql_server)
    database.create()
    try:
        build_backend()
        apply_migrations(database.connection_string)
        yield database
    finally:
        try:
            database.drop()
        except ContractEnvironmentError as error:  # pragma: no cover - cleanup diagnostics
            # Reported loudly rather than swallowed: a leaked database is a real problem.
            pytest.fail(f"throwaway database cleanup failed: {error}")


@pytest.fixture(scope="session")
def backend(
    throwaway_database: ThrowawayDatabase, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[BackendHost]:
    """The actual Backend application on a loopback port, stopped unconditionally afterwards."""
    log_directory: Path = tmp_path_factory.mktemp("backend-logs")
    host = BackendHost(throwaway_database.connection_string, log_directory)
    try:
        host.start()
        yield host
    finally:
        host.stop()
        # Diagnostics are consumed during the run; the raw log never outlives it.
        host.log_path.unlink(missing_ok=True)


@pytest.fixture
def admin(backend: BackendHost) -> Iterator[AdminApiClient]:
    """A logged-in Admin API client against the running Backend."""
    with AdminApiClient(backend.base_url) as client:
        client.login()
        yield client
