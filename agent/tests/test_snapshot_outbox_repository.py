"""Unit tests for ``SnapshotOutboxRepository`` (IP-10 T-142, FS-08 §6/§7).

Mirrors ``test_detection_event_repository.py``'s structure: a temporary root, provisioned and
schema-initialized, then the repository constructed with the explicit database path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.errors import (
    SnapshotOutboxAlertConflictError,
    SnapshotOutboxAlreadyExistsError,
)
from weapon_detection_agent.persistence.snapshot_models import CaptureStatus, UploadStatus
from weapon_detection_agent.persistence.snapshot_outbox_repository import SnapshotOutboxRepository

EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
OTHER_EVENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"
CAPTURED_AT = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
ALERT_ID = "alert-0001"
OTHER_ALERT_ID = "alert-0002"


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _insert_detection_event(db: Path, event_id: UUID = EVENT_ID) -> None:
    """A SnapshotOutbox row's EventId REFERENCES an active DetectionEvent row (FS-08 §6)."""
    event = DetectionEvent(
        event_id=event_id,
        device_id=DEVICE_ID,
        camera_id="camera1",
        source_id=0,
        class_id=0,
        class_name="gun",
        confidence=0.91,
        frame_number=1,
        detected_at_utc=CAPTURED_AT,
        frame_width=640,
        frame_height=640,
        bbox_left=10.0,
        bbox_top=10.0,
        bbox_width=50.0,
        bbox_height=50.0,
    )
    DetectionEventRepository(db).insert(event)


def _repo(db: Path) -> SnapshotOutboxRepository:
    return SnapshotOutboxRepository(db, clock=lambda: CAPTURED_AT)


def _create(
    repo: SnapshotOutboxRepository,
    db: Path,
    *,
    event_id: UUID = EVENT_ID,
    insert_event: bool = True,
) -> None:
    if insert_event:
        _insert_detection_event(db, event_id)
    repo.create_captured(
        event_id=event_id,
        local_path=Path(f"/opt/weapon-detection/snapshots/{event_id}.jpg"),
        content_type="image/jpeg",
        size_bytes=1024,
        sha256="a" * 64,
    )


# --- create_captured -------------------------------------------------------------------------


def test_create_captured_round_trips(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    record = repo.get(EVENT_ID)

    assert record is not None
    assert record.capture_status is CaptureStatus.CAPTURED
    assert record.upload_status is UploadStatus.PENDING
    assert record.backend_alert_id is None
    assert record.sha256 == "a" * 64
    assert record.size_bytes == 1024
    assert record.captured_at_utc == CAPTURED_AT


def test_create_captured_rejects_duplicate_event_id(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    with pytest.raises(SnapshotOutboxAlreadyExistsError):
        _create(repo, db, insert_event=False)


def test_create_captured_never_overwrites_existing_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    with pytest.raises(SnapshotOutboxAlreadyExistsError):
        repo.create_captured(
            event_id=EVENT_ID,
            local_path=Path("/tmp/different.jpg"),
            content_type="image/jpeg",
            size_bytes=999,
            sha256="b" * 64,
        )

    record = repo.get(EVENT_ID)
    assert record is not None
    assert record.size_bytes == 1024
    assert record.sha256 == "a" * 64


# --- associate_alert_id -----------------------------------------------------------------------


def test_associate_alert_id_sets_it_once(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    applied = repo.associate_alert_id(EVENT_ID, ALERT_ID)

    assert applied is True
    assert repo.get(EVENT_ID).backend_alert_id == ALERT_ID  # type: ignore[union-attr]


def test_associate_alert_id_is_idempotent_for_the_same_alert_id(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)

    applied_again = repo.associate_alert_id(EVENT_ID, ALERT_ID)

    assert applied_again is True
    assert repo.get(EVENT_ID).backend_alert_id == ALERT_ID  # type: ignore[union-attr]


def test_associate_alert_id_rejects_a_conflicting_value(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)

    with pytest.raises(SnapshotOutboxAlertConflictError):
        repo.associate_alert_id(EVENT_ID, OTHER_ALERT_ID)

    # The original association is never silently overwritten.
    assert repo.get(EVENT_ID).backend_alert_id == ALERT_ID  # type: ignore[union-attr]


def test_associate_alert_id_no_op_when_no_row_exists_yet(tmp_path: Path) -> None:
    """Metadata-first ordering (FS-08 §7): the AlertId arrives before any capture has happened."""
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _insert_detection_event(db, EVENT_ID)

    applied = repo.associate_alert_id(EVENT_ID, ALERT_ID)

    assert applied is False
    assert repo.get(EVENT_ID) is None


# --- list_upload_ready -------------------------------------------------------------------------


def test_list_upload_ready_requires_captured_pending_and_alert_id(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db, event_id=EVENT_ID)
    _create(repo, db, event_id=OTHER_EVENT_ID)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)
    # OTHER_EVENT_ID never gets an AlertId — capture-first, alert not yet synced (FS-08 §7).

    ready = repo.list_upload_ready(10)

    assert [r.event_id for r in ready] == [EVENT_ID]


def test_list_upload_ready_excludes_already_uploaded_rows(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)
    repo.mark_uploaded(EVENT_ID, CAPTURED_AT)

    assert repo.list_upload_ready(10) == []


def test_list_upload_ready_excludes_capture_failed_rows(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)
    repo.mark_capture_failed(EVENT_ID)

    assert repo.list_upload_ready(10) == []


def test_list_upload_ready_orders_oldest_captured_first(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    older_id = UUID(int=1)
    newer_id = UUID(int=2)
    repo_older = SnapshotOutboxRepository(
        db, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    repo_newer = SnapshotOutboxRepository(
        db, clock=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    )
    _insert_detection_event(db, older_id)
    _insert_detection_event(db, newer_id)
    repo_newer.create_captured(
        event_id=newer_id,
        local_path=Path("/tmp/newer.jpg"),
        content_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
    )
    repo_older.create_captured(
        event_id=older_id,
        local_path=Path("/tmp/older.jpg"),
        content_type="image/jpeg",
        size_bytes=10,
        sha256="b" * 64,
    )
    repo_older.associate_alert_id(older_id, "alert-older")
    repo_older.associate_alert_id(newer_id, "alert-newer")

    ready = repo_older.list_upload_ready(10)

    assert [r.event_id for r in ready] == [older_id, newer_id]


def test_list_upload_ready_rejects_non_positive_limit(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    with pytest.raises(ValueError):
        repo.list_upload_ready(0)


# --- mark_uploaded ---------------------------------------------------------------------------


def test_mark_uploaded_sets_status_and_timestamp(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    uploaded_at = datetime(2026, 7, 28, 13, 0, 0, tzinfo=timezone.utc)

    updated = repo.mark_uploaded(EVENT_ID, uploaded_at)

    assert updated is True
    record = repo.get(EVENT_ID)
    assert record.upload_status is UploadStatus.UPLOADED  # type: ignore[union-attr]
    assert record.uploaded_at_utc == uploaded_at  # type: ignore[union-attr]


def test_mark_uploaded_is_idempotent(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.mark_uploaded(EVENT_ID, CAPTURED_AT)

    second = repo.mark_uploaded(EVENT_ID, datetime(2027, 1, 1, tzinfo=timezone.utc))

    assert second is False


def test_mark_uploaded_unknown_event_id_returns_false(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    assert repo.mark_uploaded(EVENT_ID, CAPTURED_AT) is False


# --- record_attempt_failure ---------------------------------------------------------------------


def test_record_attempt_failure_increments_count_and_leaves_pending(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    attempt_at = datetime(2026, 7, 28, 14, 0, 0, tzinfo=timezone.utc)

    repo.record_attempt_failure(EVENT_ID, attempt_at_utc=attempt_at, error_category="timeout")

    record = repo.get(EVENT_ID)
    assert record.attempt_count == 1  # type: ignore[union-attr]
    assert record.last_error_category == "timeout"  # type: ignore[union-attr]
    assert record.last_attempt_at_utc == attempt_at  # type: ignore[union-attr]
    assert record.upload_status is UploadStatus.PENDING  # type: ignore[union-attr]


def test_record_attempt_failure_accumulates_across_calls(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    repo.record_attempt_failure(EVENT_ID, attempt_at_utc=CAPTURED_AT, error_category="timeout")
    repo.record_attempt_failure(EVENT_ID, attempt_at_utc=CAPTURED_AT, error_category="conflict")

    assert repo.get(EVENT_ID).attempt_count == 2  # type: ignore[union-attr]


# --- mark_capture_failed (T-148 reconciliation) -------------------------------------------------


def test_mark_capture_failed_transitions_captured_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    updated = repo.mark_capture_failed(EVENT_ID)

    assert updated is True
    assert repo.get(EVENT_ID).capture_status is CaptureStatus.CAPTURE_FAILED  # type: ignore[union-attr]


def test_mark_capture_failed_never_deletes_the_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    repo.mark_capture_failed(EVENT_ID)

    assert repo.get(EVENT_ID) is not None


def test_mark_capture_failed_unknown_event_id_returns_false(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    assert repo.mark_capture_failed(EVENT_ID) is False


# --- cancel_for_quota (FS-09 §10, IP-11 T-178) --------------------------------------------------


def test_cancel_for_quota_transitions_pending_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    updated = repo.cancel_for_quota(EVENT_ID)

    assert updated is True
    record = repo.get(EVENT_ID)
    assert record.upload_status is UploadStatus.SUPPRESSED_BY_QUOTA  # type: ignore[union-attr]


def test_cancel_for_quota_never_deletes_the_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    repo.cancel_for_quota(EVENT_ID)

    assert repo.get(EVENT_ID) is not None


def test_cancel_for_quota_never_touches_capture_status(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    repo.cancel_for_quota(EVENT_ID)

    assert repo.get(EVENT_ID).capture_status is CaptureStatus.CAPTURED  # type: ignore[union-attr]


def test_cancel_for_quota_unknown_event_id_returns_false(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    assert repo.cancel_for_quota(EVENT_ID) is False


def test_cancel_for_quota_never_re_marks_an_already_uploaded_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)
    repo.mark_uploaded(EVENT_ID, CAPTURED_AT)

    updated = repo.cancel_for_quota(EVENT_ID)

    assert updated is False
    assert repo.get(EVENT_ID).upload_status is UploadStatus.UPLOADED  # type: ignore[union-attr]


def test_cancel_for_quota_is_idempotent(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)

    first = repo.cancel_for_quota(EVENT_ID)
    second = repo.cancel_for_quota(EVENT_ID)

    assert first is True
    assert second is False
    assert repo.get(EVENT_ID).upload_status is UploadStatus.SUPPRESSED_BY_QUOTA  # type: ignore[union-attr]


def test_cancel_for_quota_never_uploaded_row_is_never_upload_ready(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db)
    repo.associate_alert_id(EVENT_ID, ALERT_ID)

    repo.cancel_for_quota(EVENT_ID)

    assert repo.list_upload_ready(10) == []


# --- list_all_event_ids -------------------------------------------------------------------------


def test_list_all_event_ids_returns_every_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = _repo(db)
    _create(repo, db, event_id=EVENT_ID)
    _create(repo, db, event_id=OTHER_EVENT_ID)

    assert set(repo.list_all_event_ids()) == {EVENT_ID, OTHER_EVENT_ID}
