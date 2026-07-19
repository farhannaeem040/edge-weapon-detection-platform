"""Repository for the Agent's cached configuration (IP-02 T-36, §16.1; ARCH-001 §13.2, §16.3).

This reads the ``ConfigCache`` singleton row — the last synchronized configuration used for offline
startup. It is **load-only by design**: IP-02 T-36 excludes a ``ConfigCache`` writer (OI-2), because
this milestone has no configuration source to populate it (the activation response carries no
configuration). Nothing here writes, clears, refreshes, or decides the freshness of the cache; the
configuration feature that populates it, and the startup workflow that consumes it (T-39), own that.

An absent cache is the normal state in this milestone and returns ``None`` — never an error (IP-02
§16.1; OI-2). The stored ``ConfigJson`` is returned as raw text: no configuration schema exists yet
(OI-2), so it is not parsed or interpreted, and its contents are never logged or placed in an error.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from weapon_detection_agent.persistence.database import open_connection
from weapon_detection_agent.persistence.errors import InvalidConfigCacheStateError
from weapon_detection_agent.persistence.models import CachedConfiguration, parse_iso_utc

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.config_cache")

ConnectionOpener = Callable[[], AbstractContextManager[sqlite3.Connection]]

_SELECT = "SELECT ConfigJson, UpdatedAt FROM ConfigCache"


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
