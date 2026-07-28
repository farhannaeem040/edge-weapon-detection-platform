"""Unit tests for the Detection Event repository (IP-07 T-85, FS-05 §7).

Every test resolves a temporary root (``tmp_path``), provisions it, initializes the schema, then
constructs the repository with the explicit database path — never ``/opt``. Mirrors the structure of
``test_device_identity_repository.py``.
"""

from __future__ import annotations

import builtins
import importlib
import socket
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.database import connect, open_connection
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.errors import (
    DetectionEventAlreadyExistsError,
    InvalidDetectionEventStateError,
)

EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
OTHER_EVENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"
DETECTED_AT = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)
FIXED_CREATED_AT = datetime(2026, 7, 24, 18, 30, 1, tzinfo=timezone.utc)


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _event(
    *,
    event_id: UUID = EVENT_ID,
    device_id: str = DEVICE_ID,
    camera_id: str = "camera1",
    confidence: float = 0.91,
    detected_at_utc: datetime = DETECTED_AT,
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        device_id=device_id,
        camera_id=camera_id,
        source_id=0,
        class_id=0,
        class_name="gun",
        confidence=confidence,
        frame_number=12345,
        detected_at_utc=detected_at_utc,
        frame_width=640,
        frame_height=640,
        bbox_left=210.0,
        bbox_top=130.0,
        bbox_width=95.0,
        bbox_height=70.0,
    )


def _repo(db: Path) -> DetectionEventRepository:
    return DetectionEventRepository(db, clock=lambda: FIXED_CREATED_AT)


# --- Constructor conventions ---------------------------------------------------------------------


def test_requires_exactly_one_of_database_path_or_connection_factory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DetectionEventRepository()

    db = _ready_db(tmp_path)
    with pytest.raises(ValueError):
        DetectionEventRepository(db, connection_factory=lambda: open_connection(db))


# --- Insert / round-trip ---------------------------------------------------------------------


def test_insert_succeeds(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))

    repo.insert(_event())


def test_all_fields_round_trip_exactly(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    event = _event()

    repo.insert(event)
    (loaded,) = repo.list_recent(10)

    assert loaded == event


def test_timestamps_return_as_timezone_aware_utc(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    repo.insert(_event())

    (loaded,) = repo.list_recent(10)

    assert loaded.detected_at_utc.tzinfo is not None
    assert loaded.detected_at_utc == DETECTED_AT


def test_pending_delivery_status_is_persisted(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _repo(db).insert(_event())

    with open_connection(db) as connection:
        row = connection.execute(
            "SELECT DeliveryStatus FROM DetectionEvent WHERE EventId = ?", (str(EVENT_ID),)
        ).fetchone()

    assert row["DeliveryStatus"] == "pending"


def test_created_at_utc_uses_injected_clock(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _repo(db).insert(_event())

    with open_connection(db) as connection:
        row = connection.execute(
            "SELECT CreatedAtUtc FROM DetectionEvent WHERE EventId = ?", (str(EVENT_ID),)
        ).fetchone()

    assert row["CreatedAtUtc"] == FIXED_CREATED_AT.isoformat()


def test_multiple_events_can_be_stored(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    repo.insert(_event(event_id=EVENT_ID))
    repo.insert(_event(event_id=OTHER_EVENT_ID, camera_id="camera2"))

    loaded = repo.list_recent(10)

    assert {e.event_id for e in loaded} == {EVENT_ID, OTHER_EVENT_ID}


def test_list_recent_orders_newest_first_and_respects_limit(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    clock_values = iter(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 1, 3, tzinfo=timezone.utc),
        ]
    )
    repo = DetectionEventRepository(db, clock=lambda: next(clock_values))
    repo.insert(_event(event_id=UUID(int=1)))
    repo.insert(_event(event_id=UUID(int=2)))
    repo.insert(_event(event_id=UUID(int=3)))

    loaded = repo.list_recent(2)

    assert [e.event_id for e in loaded] == [UUID(int=3), UUID(int=2)]


def test_list_recent_rejects_non_positive_limit(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))

    with pytest.raises(ValueError):
        repo.list_recent(0)


# --- Duplicate event_id -----------------------------------------------------------------------


def test_duplicate_event_id_is_rejected(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    repo.insert(_event())

    with pytest.raises(DetectionEventAlreadyExistsError):
        repo.insert(_event(confidence=0.5))


def test_duplicate_event_id_does_not_overwrite_original_row(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    repo.insert(_event(confidence=0.91))

    with pytest.raises(DetectionEventAlreadyExistsError):
        repo.insert(_event(confidence=0.5, camera_id="camera2"))

    (loaded,) = repo.list_recent(10)
    assert loaded.confidence == 0.91
    assert loaded.camera_id == "camera1"


class _PreCheckBlindConnection:
    """Wraps a real connection but makes the duplicate pre-check SELECT always report "no row".

    Simulates the pre-check/INSERT race the repository's exception-translation path exists to
    close: the PRIMARY KEY constraint is still real and still enforced by SQLite, but the
    repository can no longer rely on its own pre-check to catch the duplicate — proving the INSERT's
    own ``sqlite3.IntegrityError`` is what actually guards uniqueness.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        if str(sql).strip().startswith("SELECT 1 FROM DetectionEvent WHERE EventId"):
            return self._real.execute("SELECT 1 FROM DetectionEvent WHERE 0")
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._real.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@contextmanager
def _pre_check_blind_opener(path: Path) -> Iterator[_PreCheckBlindConnection]:
    real = connect(path)
    try:
        yield _PreCheckBlindConnection(real)
    finally:
        real.close()


def test_primary_key_constraint_is_the_authoritative_duplicate_guard(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _repo(db).insert(_event())

    racy_repo = DetectionEventRepository(
        connection_factory=lambda: _pre_check_blind_opener(db), clock=lambda: FIXED_CREATED_AT
    )

    # The blinded pre-check reports no existing row, yet the INSERT's own IntegrityError (from the
    # EventId PRIMARY KEY) must still be translated to the typed error.
    with pytest.raises(DetectionEventAlreadyExistsError):
        racy_repo.insert(_event(confidence=0.5))

    (loaded,) = _repo(db).list_recent(10)
    assert loaded.confidence == 0.91  # the original row survives untouched


def test_unrelated_integrity_error_is_not_mistaken_for_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weapon_detection_agent.persistence.detection_event_repository as repo_module

    # A DeliveryStatus outside the CHECK's permitted set triggers a real IntegrityError that has
    # nothing to do with EventId uniqueness; it must propagate as sqlite3.IntegrityError, never be
    # misreported as DetectionEventAlreadyExistsError.
    monkeypatch.setattr(repo_module, "_PENDING_DELIVERY_STATUS", "not-a-permitted-status")
    repo = _repo(_ready_db(tmp_path))

    with pytest.raises(sqlite3.IntegrityError):
        repo.insert(_event())


# --- Rollback ----------------------------------------------------------------------------------


class _CommitFailingConnection:
    """Wraps a real connection but raises on COMMIT, proving a failed insert rolls back."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        if str(sql).strip().upper().startswith("COMMIT"):
            raise sqlite3.OperationalError("injected commit failure")
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        self._real.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@contextmanager
def _commit_failing_opener(path: Path) -> Iterator[_CommitFailingConnection]:
    real = connect(path)
    try:
        yield _CommitFailingConnection(real)
    finally:
        real.close()


def test_failed_insert_rolls_back(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    failing_repo = DetectionEventRepository(
        connection_factory=lambda: _commit_failing_opener(db), clock=lambda: FIXED_CREATED_AT
    )

    with pytest.raises(sqlite3.OperationalError):
        failing_repo.insert(_event())

    with open_connection(db) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM DetectionEvent").fetchone()
    assert count == 0


def test_repository_usable_after_handled_duplicate_error(tmp_path: Path) -> None:
    repo = _repo(_ready_db(tmp_path))
    repo.insert(_event())

    with pytest.raises(DetectionEventAlreadyExistsError):
        repo.insert(_event())

    # After the handled error the database is still usable for further reads/writes.
    repo.insert(_event(event_id=OTHER_EVENT_ID))
    assert len(repo.list_recent(10)) == 2


# --- Invalid stored state ------------------------------------------------------------------------


def test_malformed_stored_timestamp_is_rejected_on_read(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    with open_connection(db) as connection:
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, CreatedAtUtc) "
            "VALUES (?, 'dev', 'cam', 0, 0, 'gun', 0.9, 1, 'not-a-timestamp', 640, 480, "
            "0, 0, 10, 10, 'not-a-timestamp')",
            (str(EVENT_ID),),
        )

    with pytest.raises(InvalidDetectionEventStateError):
        _repo(db).list_recent(10)


def test_malformed_stored_event_id_is_rejected_on_read(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    with open_connection(db) as connection:
        connection.execute(
            "INSERT INTO DetectionEvent "
            "(EventId, DeviceId, CameraId, SourceId, ClassId, ClassName, Confidence, "
            "FrameNumber, DetectedAtUtc, FrameWidth, FrameHeight, BboxLeft, BboxTop, "
            "BboxWidth, BboxHeight, CreatedAtUtc) "
            "VALUES ('not-a-uuid', 'dev', 'cam', 0, 0, 'gun', 0.9, 1, ?, 640, 480, "
            "0, 0, 10, 10, ?)",
            (DETECTED_AT.isoformat(), FIXED_CREATED_AT.isoformat()),
        )

    with pytest.raises(InvalidDetectionEventStateError):
        _repo(db).list_recent(10)


# --- Import performs no I/O -----------------------------------------------------------------------


def test_repository_import_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing the repository must not perform I/O")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    name = "weapon_detection_agent.persistence.detection_event_repository"
    saved = sys.modules.pop(name, None)
    try:
        importlib.import_module(name)
    finally:
        if saved is not None:
            sys.modules[name] = saved
