"""Repository for the Agent's persisted Detection Events (IP-07 T-85, FS-05 §7; IP-08 T-104, FS-06
§7.1; IP-02 T-36 pattern).

This is the only code that writes or reads the ``DetectionEvent`` table. :meth:`~
DetectionEventRepository.insert` always writes ``DeliveryStatus = 'pending'`` — it makes no delivery
decision and contacts no Backend. :meth:`~DetectionEventRepository.list_pending` /
:meth:`~DetectionEventRepository.mark_delivered_many` (IP-08 T-104, FS-06 §7.1) are the only methods
that ever transition a row to ``'delivered'``; they still make no delivery *decision* and perform no
HTTP call themselves — the outbox worker (FS-06 §7.4) owns deciding what to send and reading the
Backend's response, this repository only executes the two SQL operations that decision needs.
:meth:`~DetectionEventRepository.list_recent` remains the separate FS-05 §8 diagnostics read path,
unchanged by this addition.

Boundaries kept, mirroring :class:`~weapon_detection_agent.persistence.device_identity_repository.
DeviceIdentityRepository`/:class:`~weapon_detection_agent.persistence.config_cache_repository.
ConfigCacheRepository`:

* No Backend contact, ``pyds``/DeepStream import, or socket code.
* No directory creation and no schema initialization — the caller initializes the schema (T-85)
  first; this repository only operates on an already-initialized database.
* No module-level connection and no import-time I/O — a connection is opened only inside a call.
* No stored value appears in a log beyond the ``event_id``/``class_name``, which are not secrets
  (mirrors the ``device_id`` precedent, IP-02 §15).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.errors import (
    DetectionEventAlreadyExistsError,
    InvalidDetectionEventStateError,
)
from weapon_detection_agent.persistence.models import parse_iso_utc, to_iso_utc

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.detection_event")

ConnectionOpener = Callable[[], AbstractContextManager[sqlite3.Connection]]

# The only DeliveryStatus this task writes. The schema CHECK also permits 'delivered' (the future
# outbox/backend-delivery feature's terminal state), but no code here ever writes it.
_PENDING_DELIVERY_STATUS = "pending"

# The exact substring sqlite3 puts in an IntegrityError raised by the EventId PRIMARY KEY constraint
# ("UNIQUE constraint failed: DetectionEvent.EventId"). Matched narrowly so only *this* violation is
# translated to DetectionEventAlreadyExistsError — any other IntegrityError (e.g. a future NOT NULL/
# CHECK violation) propagates as-is rather than being misreported as a duplicate.
_EVENT_ID_UNIQUENESS_VIOLATION = "DetectionEvent.EventId"

_INSERT = (
    "INSERT INTO DetectionEvent "
    "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber, "
    "DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight, "
    "DeliveryStatus, CreatedAtUtc) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_RECENT = (
    "SELECT EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber, "
    "DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight "
    "FROM DetectionEvent ORDER BY CreatedAtUtc DESC LIMIT ?"
)
# Includes CreatedAtUtc (unlike _SELECT_RECENT) because the outbox worker/BackendSyncClient (FS-06
# §4.1/§7.3) needs it on every event it sends; _row_to_event populates DetectionEvent.created_at_utc
# only when the queried row actually carries the column, so _SELECT_RECENT's own mapping is
# unchanged (IP-08 T-104).
_SELECT_PENDING = (
    "SELECT EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, FrameNumber, "
    "DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, BboxWidth, BboxHeight, "
    "CreatedAtUtc FROM DetectionEvent WHERE DeliveryStatus = 'pending' "
    "ORDER BY DetectedAtUtc ASC, EventId ASC LIMIT ?"
)
_DELIVERED_STATUS = "delivered"
# FS-09 §9, IP-11 T-177: the terminal status for a Backend-suppressed detection (sync outcome
# `quota_exceeded`) — never resent, never mapped to 'delivered'.
_SUPPRESSED_BY_QUOTA_STATUS = "suppressed_by_quota"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DetectionEventRepository:
    """Persist and read accepted ``DetectionEvent`` rows.

    Construct with the database path (the common case) or an explicit ``connection_factory``;
    exactly one must be given — the same constructor shape as ``DeviceIdentityRepository``. A fresh,
    short-lived connection is used per operation; there is no retained connection. ``clock`` is an
    injectable UTC-datetime source (default :func:`datetime.now` in UTC) supplying ``CreatedAtUtc``
    — the same DI seam pattern already used by
    :class:`~weapon_detection_agent.detection.cooldown.DetectionCooldownTracker`'s
    ``monotonic_clock`` and
    :func:`~weapon_detection_agent.detection.validation.validate_detection`'s ``event_id_factory``.
    """

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        connection_factory: ConnectionOpener | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if (database_path is None) == (connection_factory is None):
            raise ValueError("provide exactly one of database_path or connection_factory")
        if connection_factory is not None:
            self._open: ConnectionOpener = connection_factory
        else:
            resolved = Path(database_path)  # type: ignore[arg-type]
            self._open = lambda: open_connection(resolved)
        self._clock = clock

    def insert(self, event: DetectionEvent) -> None:
        """Persist ``event`` with ``DeliveryStatus = 'pending'``, in one transaction.

        Rejects the write with :class:`DetectionEventAlreadyExistsError` if ``event.event_id``
        already has a stored row — this repository never overwrites an already-persisted event
        (IP-07 T-85). The ``EventId`` primary key is the authoritative duplicate guard: a cheap
        pre-check SELECT rejects the common case without touching the table, but the INSERT itself
        is what actually enforces uniqueness, so its own ``sqlite3.IntegrityError`` is caught and,
        only when it is specifically the ``EventId`` uniqueness violation, translated to
        :class:`DetectionEventAlreadyExistsError` — closing the race the pre-check alone cannot
        (two concurrent inserts of the same id). Any other ``IntegrityError`` propagates unchanged.
        Never ``INSERT OR REPLACE``/``INSERT OR IGNORE`` — a failed write leaves the original row
        untouched.
        """
        created_at_utc = self._clock()
        if created_at_utc.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")

        with self._open() as connection:
            with transaction(connection):
                existing = connection.execute(
                    "SELECT 1 FROM DetectionEvent WHERE EventId = ?", (str(event.event_id),)
                ).fetchone()
                if existing is not None:
                    raise DetectionEventAlreadyExistsError(
                        "a detection event with this event_id is already stored"
                    )
                try:
                    connection.execute(
                        _INSERT,
                        (
                            str(event.event_id),
                            event.device_id,
                            event.camera_id,
                            event.source_id,
                            event.class_id,
                            event.class_name,
                            event.confidence,
                            event.frame_number,
                            to_iso_utc(event.detected_at_utc),
                            event.frame_width,
                            event.frame_height,
                            event.bbox_left,
                            event.bbox_top,
                            event.bbox_width,
                            event.bbox_height,
                            _PENDING_DELIVERY_STATUS,
                            to_iso_utc(created_at_utc),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    if _EVENT_ID_UNIQUENESS_VIOLATION in str(exc):
                        raise DetectionEventAlreadyExistsError(
                            "a detection event with this event_id is already stored"
                        ) from exc
                    raise

        _LOGGER.info(
            "detection_event_persisted",
            extra={"event_id": str(event.event_id), "class_name": event.class_name},
        )

    def list_recent(self, limit: int) -> list[DetectionEvent]:
        """Return up to ``limit`` most-recently-created events, newest first (FS-05 §8).

        Read-only and non-mutating; exists to support diagnostics and verifying persistence (IP-07
        T-85), not the future delivery worker. Raises :class:`InvalidDetectionEventStateError` for a
        row that cannot be reconstructed — an unparseable timestamp or a malformed stored
        ``event_id``.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")

        with self._open() as connection:
            rows = connection.execute(_SELECT_RECENT, (limit,)).fetchall()

        return [self._row_to_event(row) for row in rows]

    def list_pending(self, limit: int) -> list[DetectionEvent]:
        """Return up to ``limit`` not-yet-delivered events, oldest detection first (FS-06 §7.1).

        Ordered by ``DetectedAtUtc`` then ``EventId`` (a deterministic tiebreak for equal
        timestamps) — the field the outbox worker drains oldest-first, reusing the
        ``(DeliveryStatus, CreatedAtUtc)`` index IP-07 built anticipating this query. Never returns
        an already-``delivered`` row. Raises :class:`ValueError` for a non-positive ``limit`` rather
        than silently returning everything or nothing. Each returned event's ``created_at_utc`` is
        populated from the stored row (unlike :meth:`list_recent`), since the Backend sync payload
        requires it (FS-06 §4.1).
        """
        if limit <= 0:
            raise ValueError("limit must be positive")

        with self._open() as connection:
            rows = connection.execute(_SELECT_PENDING, (limit,)).fetchall()

        return [self._row_to_event(row) for row in rows]

    def mark_delivered_many(self, event_ids: Sequence[UUID], delivered_at_utc: datetime) -> int:
        """Mark every currently-``pending`` id in ``event_ids`` ``'delivered'``, in one transaction.

        Returns the number of rows actually updated — never raises on a mismatch against
        ``len(event_ids)``; comparing the two and deciding what that means is the caller's job
        (FS-06 §7.1). Never touches a row that is already ``'delivered'`` (so a duplicate call is
        safe and idempotent), never ``INSERT OR REPLACE``s, and never deletes. An empty
        ``event_ids`` is a no-op that returns 0 without opening a connection.
        """
        if delivered_at_utc.tzinfo is None:
            raise ValueError("delivered_at_utc must be timezone-aware")

        ids = [str(event_id) for event_id in event_ids]
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        sql = (
            "UPDATE DetectionEvent SET DeliveryStatus = ?, DeliveredAtUtc = ? "
            f"WHERE EventId IN ({placeholders}) AND DeliveryStatus = 'pending'"
        )
        params: list[object] = [_DELIVERED_STATUS, to_iso_utc(delivered_at_utc), *ids]

        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(sql, params)
                updated = cursor.rowcount

        return updated

    def mark_suppressed_by_quota_many(
        self, event_ids: Sequence[UUID], finalized_at_utc: datetime
    ) -> int:
        """Mark every currently-``pending`` id in ``event_ids`` ``'suppressed_by_quota'``, in one
        transaction (FS-09 §9).

        Structurally identical to :meth:`mark_delivered_many`: returns the number of rows actually
        updated, never touches a row that is already terminal (``'delivered'`` or
        ``'suppressed_by_quota'``) so a duplicate call is safe and idempotent, never ``INSERT OR
        REPLACE``s, and never deletes. The original event row and all its metadata are preserved —
        only ``DeliveryStatus``/``FinalizedAtUtc`` change. An empty ``event_ids`` is a no-op that
        returns 0 without opening a connection. A ``quota_exceeded`` outcome is never mapped to
        ``'delivered'`` — this is the only method that ever writes ``'suppressed_by_quota'``.
        """
        if finalized_at_utc.tzinfo is None:
            raise ValueError("finalized_at_utc must be timezone-aware")

        ids = [str(event_id) for event_id in event_ids]
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        sql = (
            "UPDATE DetectionEvent SET DeliveryStatus = ?, FinalizedAtUtc = ? "
            f"WHERE EventId IN ({placeholders}) AND DeliveryStatus = 'pending'"
        )
        params: list[object] = [_SUPPRESSED_BY_QUOTA_STATUS, to_iso_utc(finalized_at_utc), *ids]

        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(sql, params)
                updated = cursor.rowcount

        return updated

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DetectionEvent:
        try:
            event_id = UUID(row["EventId"])
        except ValueError as exc:
            raise InvalidDetectionEventStateError(
                "stored detection event has an invalid event_id"
            ) from exc
        try:
            detected_at_utc = parse_iso_utc(row["DetectedAtUtc"])
        except ValueError as exc:
            raise InvalidDetectionEventStateError(
                "stored detection event has an invalid timestamp"
            ) from exc

        created_at_utc: datetime | None = None
        if "CreatedAtUtc" in row.keys():
            try:
                created_at_utc = parse_iso_utc(row["CreatedAtUtc"])
            except ValueError as exc:
                raise InvalidDetectionEventStateError(
                    "stored detection event has an invalid timestamp"
                ) from exc

        try:
            return DetectionEvent(
                event_id=event_id,
                device_id=row["DeviceId"],
                camera_id=row["CameraId"],
                source_id=row["SourceId"],
                class_id=row["ClassId"],
                class_name=row["ClassName"],
                confidence=row["Confidence"],
                frame_number=row["FrameNumber"],
                detected_at_utc=detected_at_utc,
                frame_width=row["FrameWidth"],
                frame_height=row["FrameHeight"],
                bbox_left=row["BboxLeft"],
                bbox_top=row["BboxTop"],
                bbox_width=row["BboxWidth"],
                bbox_height=row["BboxHeight"],
                created_at_utc=created_at_utc,
            )
        except ValueError as exc:
            raise InvalidDetectionEventStateError(
                "stored detection event failed domain validation"
            ) from exc
