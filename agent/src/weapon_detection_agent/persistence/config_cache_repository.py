"""Repository for the Agent's cached configuration (IP-02 T-36, §16.1; ARCH-001 §13.2, §16.3;
FS-11 §6, IP-13 T-232).

This reads and writes the ``ConfigCache`` singleton row — the last synchronized Device Camera
configuration, used both for offline startup (FS-11 §5, DeviceConfigurationCoordinator) and, in
IP-02 T-36's original milestone, as an intentionally unpopulated table (no writer existed until this
feature). ``save`` is FS-11's first writer; nothing before it ever wrote this table.

An absent cache is a normal state (first-ever activation, or a factory reset) and ``load`` returns
``None`` — never an error (IP-02 §16.1; OI-2). The stored ``ConfigJson`` is returned as raw text:
this repository does not parse or interpret it — that is
:mod:`weapon_detection_agent.configuration.validation`'s job — and its contents are never logged or
placed in an error.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.errors import InvalidConfigCacheStateError
from weapon_detection_agent.persistence.models import (
    CachedConfiguration,
    parse_iso_utc,
    to_iso_utc,
)

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.config_cache")

ConnectionOpener = Callable[[], AbstractContextManager[sqlite3.Connection]]

_SELECT = "SELECT ConfigJson, UpdatedAt FROM ConfigCache"

# SingletonGuard=1 is the table's only permitted key (schema.py CHECK constraint) — an upsert on it
# is the entire "at most one cached configuration" contract, enforced by SQLite itself.
_UPSERT = """
INSERT INTO ConfigCache (SingletonGuard, ConfigJson, UpdatedAt)
VALUES (1, :config_json, :updated_at)
ON CONFLICT (SingletonGuard) DO UPDATE SET
    ConfigJson = excluded.ConfigJson,
    UpdatedAt = excluded.UpdatedAt
"""


class ConfigCacheRepository:
    """Load the single ``ConfigCache`` row (or ``None`` when none exists).

    Construct with the database path or an explicit ``connection_factory``; exactly one must be
    given. A fresh, short-lived connection is used per operation — no connection is retained.
    """

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        connection_factory: ConnectionOpener | None = None,
    ) -> None:
        if (database_path is None) == (connection_factory is None):
            raise ValueError("provide exactly one of database_path or connection_factory")
        if connection_factory is not None:
            self._open: ConnectionOpener = connection_factory
        else:
            resolved = Path(database_path)  # type: ignore[arg-type]
            self._open = lambda: open_connection(resolved)

    def load(self) -> CachedConfiguration | None:
        """Return the cached configuration, or ``None`` when none is stored (OI-2: the normal case).

        Raises :class:`InvalidConfigCacheStateError` for an impossible state — more than one row, or
        a stored timestamp that will not parse. The configuration content is never logged.
        """
        with self._open() as connection:
            rows = connection.execute(_SELECT).fetchall()

        if not rows:
            _LOGGER.info("config_cache_missing")
            return None
        if len(rows) > 1:
            raise InvalidConfigCacheStateError("multiple config cache rows found")

        row = rows[0]
        try:
            updated_at = parse_iso_utc(row["UpdatedAt"])
        except ValueError as exc:
            raise InvalidConfigCacheStateError(
                "stored config cache has an invalid timestamp"
            ) from exc

        _LOGGER.info("config_cache_loaded")
        return CachedConfiguration(config_json=row["ConfigJson"], updated_at=updated_at)

    def save(self, config_json: str, *, updated_at: datetime) -> None:
        """Persist ``config_json`` as the new (and only) cached configuration (FS-11 §6/§8).

        An upsert against the singleton row — replaces whatever was cached before, atomically,
        inside one transaction. Never called with an unvalidated payload: the caller
        (:mod:`weapon_detection_agent.configuration.coordinator`) validates a configuration before
        ever reaching this method, so this repository has no validation of its own to perform.
        ``config_json`` is never logged.
        """
        with self._open() as connection, transaction(connection):
            connection.execute(
                _UPSERT,
                {"config_json": config_json, "updated_at": to_iso_utc(updated_at)},
            )

        _LOGGER.info("config_cache_saved")
