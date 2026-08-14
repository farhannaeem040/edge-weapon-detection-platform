"""Unit tests for snapshot validation / atomic spool write / startup reconciliation (IP-10 T-140/
T-148, FS-08 §5).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from weapon_detection_agent.config.paths import modes_enforceable, resolve_paths
from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.detection.snapshot_capture import (
    SnapshotValidationError,
    compute_sha256,
    parse_jpeg_dimensions,
    reconcile_snapshot_spool,
    spool_usage_bytes,
    validate_snapshot_bytes,
    write_snapshot_atomic,
)
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.snapshot_outbox_repository import SnapshotOutboxRepository

EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"


def _minimal_jpeg(width: int = 64, height: int = 32) -> bytes:
    """Build a byte-minimal but structurally valid baseline JPEG (SOI, SOF0, EOI)."""
    soi = b"\xff\xd8"
    # SOF0 (0xC0): length=17 (2+1+2+2+1+3), precision=8, height, width, 1 component
    sof_body = (
        bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([1, 1, 0, 0])
    )
    sof = b"\xff\xc0" + (2 + len(sof_body)).to_bytes(2, "big") + sof_body
    eoi = b"\xff\xd9"
    return soi + sof + eoi


# --- parse_jpeg_dimensions / validate_snapshot_bytes -------------------------------------------


def test_parse_jpeg_dimensions_reads_sof0() -> None:
    assert parse_jpeg_dimensions(_minimal_jpeg(640, 480)) == (640, 480)


def test_parse_jpeg_dimensions_rejects_missing_magic() -> None:
    assert parse_jpeg_dimensions(b"not-a-jpeg") is None


def test_parse_jpeg_dimensions_rejects_truncated_data() -> None:
    assert parse_jpeg_dimensions(b"\xff\xd8\xff") is None


def test_validate_snapshot_bytes_accepts_valid_jpeg() -> None:
    width, height = validate_snapshot_bytes(_minimal_jpeg(100, 50), max_file_bytes=1_000_000)
    assert (width, height) == (100, 50)


def test_validate_snapshot_bytes_rejects_empty_payload() -> None:
    with pytest.raises(SnapshotValidationError) as excinfo:
        validate_snapshot_bytes(b"", max_file_bytes=1_000_000)
    assert excinfo.value.reason == "empty_payload"


def test_validate_snapshot_bytes_rejects_oversized_file() -> None:
    with pytest.raises(SnapshotValidationError) as excinfo:
        validate_snapshot_bytes(_minimal_jpeg(), max_file_bytes=4)
    assert excinfo.value.reason == "oversized_file"


def test_validate_snapshot_bytes_rejects_missing_magic() -> None:
    with pytest.raises(SnapshotValidationError) as excinfo:
        validate_snapshot_bytes(b"not-a-jpeg-at-all", max_file_bytes=1_000_000)
    assert excinfo.value.reason == "invalid_jpeg_magic"


def test_validate_snapshot_bytes_rejects_undecodable_dimensions() -> None:
    with pytest.raises(SnapshotValidationError) as excinfo:
        validate_snapshot_bytes(b"\xff\xd8\xff\xd9", max_file_bytes=1_000_000)
    assert excinfo.value.reason == "undecodable_dimensions"


def test_compute_sha256_matches_hashlib() -> None:
    data = b"some jpeg bytes"
    assert compute_sha256(data) == hashlib.sha256(data).hexdigest()


def test_compute_sha256_never_trusts_a_wire_supplied_value() -> None:
    # The function only ever recomputes from the bytes it is given — there is no parameter through
    # which a caller could inject a pre-trusted hash.
    import inspect

    assert list(inspect.signature(compute_sha256).parameters) == ["data"]


# --- write_snapshot_atomic ---------------------------------------------------------------------


def test_write_snapshot_atomic_creates_the_final_file(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    data = _minimal_jpeg()

    final_path = write_snapshot_atomic(spool, EVENT_ID, data)

    assert final_path == spool / f"{EVENT_ID}.jpg"
    assert final_path.read_bytes() == data


def test_write_snapshot_atomic_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    write_snapshot_atomic(spool, EVENT_ID, _minimal_jpeg())

    remaining = list(spool.iterdir())
    assert remaining == [spool / f"{EVENT_ID}.jpg"]


@pytest.mark.skipif(not modes_enforceable(), reason="POSIX modes not enforceable on this platform")
def test_write_snapshot_atomic_applies_directory_and_file_modes(tmp_path: Path) -> None:
    import stat

    spool = tmp_path / "spool"
    final_path = write_snapshot_atomic(spool, EVENT_ID, _minimal_jpeg())

    assert stat.S_IMODE(spool.stat().st_mode) == 0o750
    assert stat.S_IMODE(final_path.stat().st_mode) == 0o640


# --- spool_usage_bytes -------------------------------------------------------------------------


def test_spool_usage_bytes_sums_jpg_files(tmp_path: Path) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "a.jpg").write_bytes(b"1234")
    (spool / "b.jpg").write_bytes(b"12345678")
    (spool / "ignored.txt").write_bytes(b"xxxxxxxxxxxxxxxxxxxxxx")

    assert spool_usage_bytes(spool) == 12


def test_spool_usage_bytes_returns_zero_for_missing_directory(tmp_path: Path) -> None:
    assert spool_usage_bytes(tmp_path / "does-not-exist") == 0


# --- reconcile_snapshot_spool (T-148) -----------------------------------------------------------


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _insert_detection_event(db: Path, event_id: UUID) -> None:
    DetectionEventRepository(db).insert(
        DetectionEvent(
            event_id=event_id,
            device_id=DEVICE_ID,
            camera_id="camera1",
            source_id=0,
            class_id=0,
            class_name="gun",
            confidence=0.9,
            frame_number=1,
            detected_at_utc=datetime(2026, 7, 28, tzinfo=timezone.utc),
            frame_width=640,
            frame_height=480,
            bbox_left=0.0,
            bbox_top=0.0,
            bbox_width=10.0,
            bbox_height=10.0,
        )
    )


def test_reconcile_marks_capture_failed_when_file_is_missing(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _insert_detection_event(db, EVENT_ID)
    repo = SnapshotOutboxRepository(db)
    spool = tmp_path / "spool"
    repo.create_captured(
        event_id=EVENT_ID,
        local_path=spool / f"{EVENT_ID}.jpg",  # never actually written
        content_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
    )

    reconcile_snapshot_spool(repo, spool)

    from weapon_detection_agent.persistence.snapshot_models import CaptureStatus

    assert repo.get(EVENT_ID).capture_status is CaptureStatus.CAPTURE_FAILED  # type: ignore[union-attr]


def test_reconcile_leaves_a_row_with_an_existing_file_untouched(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    _insert_detection_event(db, EVENT_ID)
    repo = SnapshotOutboxRepository(db)
    spool = tmp_path / "spool"
    local_path = write_snapshot_atomic(spool, EVENT_ID, _minimal_jpeg())
    repo.create_captured(
        event_id=EVENT_ID,
        local_path=local_path,
        content_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
    )

    reconcile_snapshot_spool(repo, spool)

    from weapon_detection_agent.persistence.snapshot_models import CaptureStatus

    assert repo.get(EVENT_ID).capture_status is CaptureStatus.CAPTURED  # type: ignore[union-attr]
    assert local_path.exists()


def test_reconcile_removes_an_orphan_file_with_no_matching_row(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = SnapshotOutboxRepository(db)
    spool = tmp_path / "spool"
    orphan_id = UUID(int=99)
    orphan_path = write_snapshot_atomic(spool, orphan_id, _minimal_jpeg())

    reconcile_snapshot_spool(repo, spool)

    assert not orphan_path.exists()


def test_reconcile_never_raises_when_spool_directory_is_absent(tmp_path: Path) -> None:
    db = _ready_db(tmp_path)
    repo = SnapshotOutboxRepository(db)

    reconcile_snapshot_spool(repo, tmp_path / "never-created")  # must not raise
