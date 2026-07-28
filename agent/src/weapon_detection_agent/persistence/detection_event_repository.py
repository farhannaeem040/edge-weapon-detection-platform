"""Repository for the Agent's persisted Detection Events (IP-07 T-85, FS-05 §7; IP-02 T-36 pattern).

This is the only code that writes or reads the ``DetectionEvent`` table. It offers exactly what
T-85 needs: an accepted :class:`~weapon_detection_agent.detection.models.DetectionEvent` is a
single-row, parameterized, transactional insert, and a :meth:`~DetectionEventRepository.list_recent`
read path for verifying persistence and the FS-05 §8 diagnostics query. It makes no delivery
decision, retry, or Backend call — ``DeliveryStatus`` is always written as ``'pending'`` here; this
repository has no method that ever writes ``'delivered'`` (a later outbox/backend-delivery feature
owns that transition, FS-05 §7/§10 — this task only reserves the column value the schema permits).

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
from collections.abc import Callable
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
            )
        except ValueError as exc:
            raise InvalidDetectionEventStateError(
                "stored detection event failed domain validation"
            ) from exc
