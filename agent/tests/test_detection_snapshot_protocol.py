"""Unit tests for IP-10's additions to ``DetectionIngestHandler`` (T-139/T-140, FS-08 §4/§5):
acknowledgement generation for schema_version 2 messages, and snapshot-frame capture.

Portable only (no real Unix socket) — mirrors ``test_detection_ingest_handler.py``'s "Portable"
layer, exercising ``_process``/``_process_snapshot`` directly against a fake writer and a real,
``tmp_path``-backed repository stack.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.detection.cooldown import DetectionCooldownTracker
from weapon_detection_agent.detection.ingest_handler import DetectionIngestHandler
from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.detection.protocol import (
    FRAME_KIND_ACKNOWLEDGEMENT,
    decode_frame_header,
)
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.snapshot_outbox_repository import SnapshotOutboxRepository


def _insert_detection_event(db: Path, event_id: UUID) -> None:
    """A SnapshotOutbox row's EventId REFERENCES an active DetectionEvent row (FS-08 §6) — these
    tests capture a snapshot for an EventId that (in production) was already accepted/persisted."""
    DetectionEventRepository(db).insert(
        DetectionEvent(
            event_id=event_id,
            device_id=DEVICE_ID,
            camera_id=CAMERA_ID,
            source_id=0,
            class_id=0,
            class_name="gun",
            confidence=0.9,
            frame_number=1,
            detected_at_utc=FIXED_NOW,
            frame_width=640,
            frame_height=480,
            bbox_left=0.0,
            bbox_top=0.0,
            bbox_width=10.0,
            bbox_height=10.0,
        )
    )


CLASS_NAMES = {0: "gun", 1: "knife"}
DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"
CAMERA_ID = "camera1"
FIXED_NOW = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)


class _FakeMonotonicClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now


class _SequentialEventIdFactory:
    def __init__(self, start: int = 1) -> None:
        self._next = start

    def __call__(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class _FakeWriter:
    """A minimal stand-in for ``asyncio.StreamWriter`` — captures every ``write()`` call."""

    def __init__(self, *, closing: bool = False) -> None:
        self.frames: list[bytes] = []
        self._closing = closing

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    def is_closing(self) -> bool:
        return self._closing

    def decoded_acks(self) -> list[dict[str, Any]]:
        acks = []
        for frame in self.frames:
            kind, length = decode_frame_header(frame[:5])
            assert kind == FRAME_KIND_ACKNOWLEDGEMENT
            acks.append(json.loads(frame[5 : 5 + length]))
        return acks


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _handler(
    tmp_path: Path,
    *,
    snapshot_capture_enabled: bool = False,
    snapshot_max_file_bytes: int = 5_242_880,
    snapshot_max_spool_bytes: int = 1_073_741_824,
) -> DetectionIngestHandler:
    db = _ready_db(tmp_path)
    snapshot_repository = SnapshotOutboxRepository(db) if snapshot_capture_enabled else None
    return DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=snapshot_capture_enabled,
        snapshot_repository=snapshot_repository,
        snapshot_spool_path=tmp_path / "snapshots",
        snapshot_max_file_bytes=snapshot_max_file_bytes,
        snapshot_max_spool_bytes=snapshot_max_spool_bytes,
    )


def _v2_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": 2,
        "messageId": "b7e2aaaa-0000-0000-0000-000000000000",
        "sourceId": 0,
        "frameNumber": 12345,
        "classId": 0,
        "confidence": 0.91,
        "boundingBox": {"left": 10.0, "top": 10.0, "width": 20.0, "height": 20.0},
    }
    payload.update(overrides)
    return payload


def _minimal_jpeg(width: int = 64, height: int = 32) -> bytes:
    soi = b"\xff\xd8"
    sof_body = (
        bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([1, 1, 0, 0])
    )
    sof = b"\xff\xc0" + (2 + len(sof_body)).to_bytes(2, "big") + sof_body
    eoi = b"\xff\xd9"
    return soi + sof + eoi


# --- v2 acknowledgement generation (T-139) -------------------------------------------------------


def test_v2_accepted_detection_sends_accepted_ack(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    writer = _FakeWriter()

    asyncio.run(handler._process(_v2_payload(), writer))  # type: ignore[arg-type]

    (ack,) = writer.decoded_acks()
    assert ack["messageId"] == "b7e2aaaa-0000-0000-0000-000000000000"
    assert ack["outcome"] == "accepted"
    assert "eventId" in ack
    assert ack["snapshotRequired"] is False  # capture disabled by default


def test_v2_ack_snapshot_required_reflects_capture_enabled(tmp_path: Path) -> None:
    handler = _handler(tmp_path, snapshot_capture_enabled=True)
    writer = _FakeWriter()

    asyncio.run(handler._process(_v2_payload(), writer))  # type: ignore[arg-type]

    (ack,) = writer.decoded_acks()
    assert ack["snapshotRequired"] is True


def test_v2_rejected_detection_sends_rejected_ack_with_error_code(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    writer = _FakeWriter()

    asyncio.run(handler._process(_v2_payload(classId=99), writer))  # type: ignore[arg-type]

    (ack,) = writer.decoded_acks()
    assert ack["outcome"] == "rejected"
    assert ack["errorCode"] == "unknown_class"
    assert "eventId" not in ack


def test_v2_suppressed_detection_sends_suppressed_ack(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    writer = _FakeWriter()

    asyncio.run(handler._process(_v2_payload(), writer))  # type: ignore[arg-type]
    writer.frames.clear()
    asyncio.run(handler._process(_v2_payload(), writer))  # type: ignore[arg-type]  # within cooldown

    (ack,) = writer.decoded_acks()
    assert ack["outcome"] == "suppressed"
    assert ack["snapshotRequired"] is False


def test_v1_message_never_gets_an_acknowledgement(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    writer = _FakeWriter()
    v1_payload = {
        "schema_version": 1,
        "class_id": 0,
        "confidence": 0.91,
        "source_id": 0,
        "frame_number": 1,
        "frame_width": 640,
        "frame_height": 640,
        "bbox_left": 10.0,
        "bbox_top": 10.0,
        "bbox_width": 20.0,
        "bbox_height": 20.0,
    }

    asyncio.run(handler._process(v1_payload, writer))  # type: ignore[arg-type]

    assert writer.frames == []


def test_no_writer_never_raises_and_never_acknowledges(tmp_path: Path) -> None:
    """Mirrors the pre-FS-08 direct-call test convention (`_process(payload)`, no writer)."""
    handler = _handler(tmp_path)

    asyncio.run(handler._process(_v2_payload()))  # no writer supplied at all


def test_ack_never_sent_on_a_closing_writer(tmp_path: Path) -> None:
    handler = _handler(tmp_path)
    writer = _FakeWriter(closing=True)

    asyncio.run(handler._process(_v2_payload(), writer))  # type: ignore[arg-type]

    assert writer.frames == []


def test_unknown_message_id_never_crashes_processing() -> None:
    # The Agent only ever *sends* acknowledgements; it has no path that *receives* one naming an
    # unknown messageId (that is exclusively the Bridge's problem, FS-08 §4.4) — nothing to assert
    # against here beyond "this file's other tests all construct/parse acks without incident."
    pass


# --- Snapshot frame capture (T-140) ---------------------------------------------------------------


def test_snapshot_capture_creates_file_and_outbox_row(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem

    db = _ready_db(tmp_path)
    handler = DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=True,
        snapshot_repository=SnapshotOutboxRepository(db),
        snapshot_spool_path=tmp_path / "snapshots",
    )

    event_id = UUID(int=1)
    _insert_detection_event(db, event_id)
    data = _minimal_jpeg()
    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=data))

    final_path = tmp_path / "snapshots" / f"{event_id}.jpg"
    assert final_path.read_bytes() == data

    record = SnapshotOutboxRepository(db).get(event_id)
    assert record is not None
    assert record.local_path == final_path
    assert record.size_bytes == len(data)


def test_snapshot_capture_persists_recomputed_sha256_not_wire_supplied(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem
    from weapon_detection_agent.persistence.database import open_connection

    db = _ready_db(tmp_path)
    handler = DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=True,
        snapshot_repository=SnapshotOutboxRepository(db),
        snapshot_spool_path=tmp_path / "snapshots",
    )
    event_id = UUID(int=2)
    _insert_detection_event(db, event_id)
    data = _minimal_jpeg()
    expected_sha = hashlib.sha256(data).hexdigest()

    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=data))

    with open_connection(db) as connection:
        row = connection.execute(
            "SELECT Sha256 FROM SnapshotOutbox WHERE EventId = ?", (str(event_id),)
        ).fetchone()
    assert row["Sha256"] == expected_sha


def test_snapshot_capture_rejects_invalid_jpeg_without_creating_a_row(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem
    from weapon_detection_agent.persistence.database import open_connection

    db = _ready_db(tmp_path)
    handler = DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=True,
        snapshot_repository=SnapshotOutboxRepository(db),
        snapshot_spool_path=tmp_path / "snapshots",
    )
    event_id = UUID(int=3)

    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=b"not-a-jpeg"))

    with open_connection(db) as connection:
        count = connection.execute("SELECT COUNT(*) AS c FROM SnapshotOutbox").fetchone()["c"]
    assert count == 0
    assert not (tmp_path / "snapshots" / f"{event_id}.jpg").exists()


def test_snapshot_capture_disabled_drops_frame_silently(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem

    handler = _handler(tmp_path, snapshot_capture_enabled=False)
    event_id = UUID(int=4)

    # Must not raise even though no spool path/repository is configured.
    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=_minimal_jpeg()))

    assert not (tmp_path / "snapshots" / f"{event_id}.jpg").exists()


def test_snapshot_capture_malformed_event_id_is_dropped_safely(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem

    handler = _handler(tmp_path, snapshot_capture_enabled=True)

    handler._process_snapshot(_SnapshotQueueItem(event_id="not-a-uuid", data=_minimal_jpeg()))
    # No exception is the assertion; nothing was written for a garbage id.


def test_spool_quota_skips_capture_without_affecting_detection(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem
    from weapon_detection_agent.persistence.database import open_connection

    db = _ready_db(tmp_path)
    handler = DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=True,
        snapshot_repository=SnapshotOutboxRepository(db),
        snapshot_spool_path=tmp_path / "snapshots",
        snapshot_max_spool_bytes=1,  # any real snapshot immediately exceeds this
    )
    event_id = UUID(int=5)

    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=_minimal_jpeg()))

    assert not (tmp_path / "snapshots" / f"{event_id}.jpg").exists()
    with open_connection(db) as connection:
        count = connection.execute("SELECT COUNT(*) AS c FROM SnapshotOutbox").fetchone()["c"]
    assert count == 0

    # Detection processing itself remains entirely unaffected by a full spool.
    asyncio.run(handler._process(_v2_payload()))
    assert len(DetectionEventRepository(db).list_recent(10)) == 1


def test_duplicate_snapshot_capture_is_idempotent_no_second_row(tmp_path: Path) -> None:
    from weapon_detection_agent.detection.ingest_handler import _SnapshotQueueItem
    from weapon_detection_agent.persistence.database import open_connection

    db = _ready_db(tmp_path)
    handler = DetectionIngestHandler(
        socket_path=tmp_path / "detection.sock",
        queue_capacity=10,
        device_id_provider=lambda: DEVICE_ID,
        camera_id=CAMERA_ID,
        class_names=CLASS_NAMES,
        min_confidence=0.5,
        cooldown_tracker=DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        ),
        repository=DetectionEventRepository(db),
        clock=lambda: FIXED_NOW,
        event_id_factory=_SequentialEventIdFactory(),
        snapshot_capture_enabled=True,
        snapshot_repository=SnapshotOutboxRepository(db),
        snapshot_spool_path=tmp_path / "snapshots",
    )
    event_id = UUID(int=6)
    _insert_detection_event(db, event_id)
    data = _minimal_jpeg()

    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=data))
    handler._process_snapshot(_SnapshotQueueItem(event_id=str(event_id), data=data))  # duplicate

    with open_connection(db) as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS c FROM SnapshotOutbox WHERE EventId = ?", (str(event_id),)
        ).fetchone()["c"]
    assert count == 1
