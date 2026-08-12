"""Repository for the Agent's persisted ``SnapshotOutbox`` rows (IP-10 T-142, FS-08 §6/§7).

This is the only code that writes or reads the ``SnapshotOutbox`` table. Mirrors
:class:`~weapon_detection_agent.persistence.detection_event_repository.DetectionEventRepository`'s
constructor shape, connection-per-call discipline, and no-Backend-contact/no-schema-init boundaries.

Never deletes a pending row silently (FS-08/IP-10 task brief) — the only mutations are: create the
initial ``captured`` row, associate a ``BackendAlertId``, mark ``uploaded``, record an attempt
(bounded increment + redacted error category), and mark ``capture_failed`` (startup reconciliation
only, T-148). No method ever writes JPEG bytes; ``local_path`` is a filesystem reference only
(ADR-004/ADR-011).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from weapon_detection_agent.persistence.database import open_connection, transaction
from weapon_detection_agent.persistence.errors import (
    SnapshotOutboxAlertConflictError,
    SnapshotOutboxAlreadyExistsError,
)
from weapon_detection_agent.persistence.models import parse_iso_utc, to_iso_utc
from weapon_detection_agent.persistence.snapshot_models import (
    CaptureStatus,
    SnapshotOutboxRecord,
    UploadStatus,
)

_LOGGER = logging.getLogger("weapon_detection_agent.persistence.snapshot_outbox")

ConnectionOpener = Callable[[], AbstractContextManager[sqlite3.Connection]]

# The exact substring sqlite3 puts in an IntegrityError raised by the EventId PRIMARY KEY
# constraint, matched narrowly the same way DetectionEventRepository matches its own uniqueness
# violation.
_EVENT_ID_UNIQUENESS_VIOLATION = "SnapshotOutbox.EventId"

_COLUMNS = (
    "EventId, LocalPath, CaptureStatus, UploadStatus, BackendAlertId, ContentType, SizeBytes, "
    "Sha256, CapturedAtUtc, UploadedAtUtc, AttemptCount, LastAttemptAtUtc, LastErrorCategory"
)
_INSERT_CAPTURED = (
    f"INSERT INTO SnapshotOutbox ({_COLUMNS}) VALUES (?, ?, 'captured', 'pending', NULL, ?, ?, "
    "?, ?, NULL, 0, NULL, NULL)"
)
_SELECT_ONE = f"SELECT {_COLUMNS} FROM SnapshotOutbox WHERE EventId = ?"
_SELECT_UPLOAD_READY = (
    f"SELECT {_COLUMNS} FROM SnapshotOutbox "
    "WHERE CaptureStatus = 'captured' AND UploadStatus = 'pending' AND BackendAlertId IS NOT NULL "
    "ORDER BY CapturedAtUtc ASC, EventId ASC LIMIT ?"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SnapshotOutboxRepository:
    """Persist and read ``SnapshotOutbox`` rows (FS-08 §6, IP-10 T-142).

    Construct with the database path (the common case) or an explicit ``connection_factory`` —
    exactly one must be given, the same constructor shape as ``DetectionEventRepository``.
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

    def create_captured(
        self,
        *,
        event_id: UUID,
        local_path: Path,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> SnapshotOutboxRecord:
        """Insert a new ``captured``/``pending`` row for ``event_id``, in one transaction.

        Raises :class:`SnapshotOutboxAlreadyExistsError` if a row already exists for this
        ``event_id`` (FS-08 §5: one file per accepted ``EventId``) — the ``EventId`` PRIMARY KEY is
        the authoritative guard, closing the same pre-check/insert race
        ``DetectionEventRepository.insert`` already closes. Never overwrites an existing row.
        """
        captured_at_utc = self._clock()
        if captured_at_utc.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")

        with self._open() as connection:
            with transaction(connection):
                existing = connection.execute(
                    "SELECT 1 FROM SnapshotOutbox WHERE EventId = ?", (str(event_id),)
                ).fetchone()
                if existing is not None:
                    raise SnapshotOutboxAlreadyExistsError(
                        "a snapshot outbox row already exists for this event_id"
                    )
                try:
                    connection.execute(
                        _INSERT_CAPTURED,
                        (
                            str(event_id),
                            str(local_path),
                            content_type,
                            size_bytes,
                            sha256,
                            to_iso_utc(captured_at_utc),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    if _EVENT_ID_UNIQUENESS_VIOLATION in str(exc):
                        raise SnapshotOutboxAlreadyExistsError(
                            "a snapshot outbox row already exists for this event_id"
                        ) from exc
                    raise

        _LOGGER.info(
            "snapshot_outbox_captured", extra={"event_id": str(event_id), "size_bytes": size_bytes}
        )
        record = self.get(event_id)
        assert record is not None  # noqa: S101 -- just inserted, in the same logical operation
        return record

    def associate_alert_id(self, event_id: UUID, alert_id: str) -> bool:
        """Set ``BackendAlertId`` for ``event_id`` if a row exists and it is not already set.

        Idempotent (FS-08 §7): a call naming the *same* ``alert_id`` already stored is a no-op that
        returns ``True``. A call naming a *different* ``alert_id`` than the one already stored
        raises :class:`SnapshotOutboxAlertConflictError` — never silently overwritten (the same
        discipline FS-06 §5.2's conflict handling uses). Returns ``False`` (a no-op, not an error)
        when no ``SnapshotOutbox`` row exists yet for ``event_id`` — capture may not have happened
        yet (FS-08 §7's metadata-first ordering); the caller (the sync worker's additive side
        effect) never buffers the AlertId itself since the Backend's accepted/duplicate response
        is idempotent by ``EventId`` and can simply be re-derived later.
        """
        with self._open() as connection:
            with transaction(connection):
                row = connection.execute(
                    "SELECT BackendAlertId FROM SnapshotOutbox WHERE EventId = ?",
                    (str(event_id),),
                ).fetchone()
                if row is None:
                    return False

                existing_alert_id = row["BackendAlertId"]
                if existing_alert_id == alert_id:
                    return True
                if existing_alert_id is not None:
                    raise SnapshotOutboxAlertConflictError(
                        "snapshot outbox row already has a different BackendAlertId"
                    )

                connection.execute(
                    "UPDATE SnapshotOutbox SET BackendAlertId = ? WHERE EventId = ?",
                    (alert_id, str(event_id)),
                )

        _LOGGER.info("snapshot_outbox_alert_associated", extra={"event_id": str(event_id)[:8]})
        return True

    def list_upload_ready(self, limit: int) -> list[SnapshotOutboxRecord]:
        """Return up to ``limit`` upload-ready rows, oldest-captured first (FS-08 §6/§11).

        A row is upload-ready when ``CaptureStatus='captured'``, ``UploadStatus='pending'``, and
        ``BackendAlertId IS NOT NULL`` — exactly the query FS-08 §6 names.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")

        with self._open() as connection:
            rows = connection.execute(_SELECT_UPLOAD_READY, (limit,)).fetchall()

        return [self._row_to_record(row) for row in rows]

    def mark_uploaded(self, event_id: UUID, uploaded_at_utc: datetime) -> bool:
        """Mark ``event_id``'s row ``uploaded``, only if currently ``pending``. Idempotent.

        Returns whether a row was actually updated. Never touches ``CaptureStatus`` or
        ``BackendAlertId``.
        """
        if uploaded_at_utc.tzinfo is None:
            raise ValueError("uploaded_at_utc must be timezone-aware")

        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE SnapshotOutbox SET UploadStatus = 'uploaded', UploadedAtUtc = ? "
                    "WHERE EventId = ? AND UploadStatus = 'pending'",
                    (to_iso_utc(uploaded_at_utc), str(event_id)),
                )
                updated = cursor.rowcount

        if updated:
            _LOGGER.info("snapshot_outbox_uploaded", extra={"event_id": str(event_id)[:8]})
        return bool(updated)

    def record_attempt_failure(
        self, event_id: UUID, *, attempt_at_utc: datetime, error_category: str
    ) -> None:
        """Increment ``AttemptCount`` and record ``LastAttemptAtUtc``/``LastErrorCategory``.

        Never transitions ``UploadStatus`` — a failed attempt leaves the row ``pending`` for the
        worker's own backoff/retry to pick up again (FS-08 §11, mirrors FS-06 §9's whole-batch
        failure posture). ``error_category`` is a safe diagnostic category only, never raw response
        content (mirrors :class:`~weapon_detection_agent.sync.models.SyncFailureReason`).
        """
        if attempt_at_utc.tzinfo is None:
            raise ValueError("attempt_at_utc must be timezone-aware")

        with self._open() as connection:
            with transaction(connection):
                connection.execute(
                    "UPDATE SnapshotOutbox SET AttemptCount = AttemptCount + 1, "
                    "LastAttemptAtUtc = ?, LastErrorCategory = ? WHERE EventId = ?",
                    (to_iso_utc(attempt_at_utc), error_category, str(event_id)),
                )

        _LOGGER.warning(
            "snapshot_outbox_attempt_failed",
            extra={"event_id": str(event_id)[:8], "reason": error_category},
        )

    def mark_capture_failed(self, event_id: UUID) -> bool:
        """Mark an existing ``captured`` row ``capture_failed`` (startup reconciliation, T-148).

        Used only when the local JPEG referenced by an existing row is missing at startup. Never
        deletes the row — the DetectionEvent metadata sync path is entirely unaffected, and a
        `capture_failed` row simply never becomes upload-ready again (`list_upload_ready` only
        selects `CaptureStatus='captured'`).
        """
        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE SnapshotOutbox SET CaptureStatus = 'capture_failed' "
                    "WHERE EventId = ? AND CaptureStatus = 'captured'",
                    (str(event_id),),
                )
                updated = cursor.rowcount

        if updated:
            _LOGGER.warning(
                "snapshot_outbox_capture_marked_failed", extra={"event_id": str(event_id)[:8]}
            )
        return bool(updated)

    def cancel_for_quota(self, event_id: UUID) -> bool:
        """Mark an existing ``pending``-upload row ``suppressed_by_quota`` (FS-09 §10, IP-11 T-178).

        Called for every quota-suppressed ``EventId`` that has a ``SnapshotOutbox`` row — capture
        may have already completed by the time the Backend's ``quota_exceeded`` acknowledgement
        arrives (FS-09 §10's correlation race). Returns whether a row was actually found and
        updated; ``False`` (a no-op, not an error) when no row exists yet — mirrors
        :meth:`associate_alert_id`'s "capture may not have happened yet" no-op, since a row created
        *after* this call finds its parent ``DetectionEvent`` already ``suppressed_by_quota`` and
        must not be scheduled for upload by the capture path itself. Never touches
        ``CaptureStatus``, ``BackendAlertId``, or a row already ``uploaded`` — this is a terminal
        state transition, not a capture-failure report, so it never uses
        :meth:`mark_capture_failed`'s column.
        """
        with self._open() as connection:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE SnapshotOutbox SET UploadStatus = 'suppressed_by_quota' "
                    "WHERE EventId = ? AND UploadStatus = 'pending'",
                    (str(event_id),),
                )
                updated = cursor.rowcount

        if updated:
            _LOGGER.info(
                "snapshot_outbox_suppressed_by_quota", extra={"event_id": str(event_id)[:8]}
            )
        return bool(updated)

    def get(self, event_id: UUID) -> SnapshotOutboxRecord | None:
        """Return the row for ``event_id``, or ``None``. Read-only, non-mutating."""
        with self._open() as connection:
            row = connection.execute(_SELECT_ONE, (str(event_id),)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_all_event_ids(self) -> Sequence[UUID]:
        """Return every stored ``EventId`` (startup reconciliation, T-148, orphan-file detection).

        Bounded by construction: the number of pending/captured snapshot rows is small (FS-08's own
        low-volume framing), so this is a single unbounded scan used only once at startup, never on
        a hot path.
        """
        with self._open() as connection:
            rows = connection.execute("SELECT EventId FROM SnapshotOutbox").fetchall()
        return [UUID(row["EventId"]) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SnapshotOutboxRecord:
        return SnapshotOutboxRecord(
            event_id=UUID(row["EventId"]),
            local_path=Path(row["LocalPath"]),
            capture_status=CaptureStatus(row["CaptureStatus"]),
            upload_status=UploadStatus(row["UploadStatus"]),
            backend_alert_id=row["BackendAlertId"],
            content_type=row["ContentType"],
            size_bytes=row["SizeBytes"],
            sha256=row["Sha256"],
            captured_at_utc=parse_iso_utc(row["CapturedAtUtc"]),
            uploaded_at_utc=(
                parse_iso_utc(row["UploadedAtUtc"]) if row["UploadedAtUtc"] is not None else None
            ),
            attempt_count=row["AttemptCount"],
            last_attempt_at_utc=(
                parse_iso_utc(row["LastAttemptAtUtc"])
                if row["LastAttemptAtUtc"] is not None
                else None
            ),
            last_error_category=row["LastErrorCategory"],
        )
