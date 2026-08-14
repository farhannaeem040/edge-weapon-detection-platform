"""Unit tests for ``DetectionIngestHandler`` (IP-07 T-86, FS-05 §4/§8; ADR-005).

Two layers, per the repository's platform-test convention (item 16 of the T-86 task brief):

* **Portable** tests exercise framing (:meth:`DetectionIngestHandler._read_message` against a fake
  ``StreamReader``) and the validation -> cooldown -> persistence pipeline
  (:meth:`DetectionIngestHandler._process` against a real, ``tmp_path``-backed
  ``DetectionEventRepository`` — SQLite needs no platform-specific socket support) directly, with no
  real socket at all. These run everywhere, including this suite's default Windows dev environment.
* **Real-socket** tests open an actual ``AF_UNIX`` listener and a real client connection end-to-end.
  They are marked with :data:`requires_unix_sockets` and skip on any platform without
  ``asyncio.start_unix_server`` (Windows) — never gating the whole file, only the tests that
  genuinely need a filesystem-backed Unix domain socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket as socket_module
import stat
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from weapon_detection_agent.config.paths import modes_enforceable, resolve_paths
from weapon_detection_agent.detection.cooldown import DetectionCooldownTracker
from weapon_detection_agent.detection.errors import (
    DetectionIngestHandlerAlreadyRunningError,
    DetectionRuntimeDirectoryMissingError,
    DetectionSocketPathConflictError,
)
from weapon_detection_agent.detection.ingest_handler import (
    SOCKET_FILE_MODE,
    DetectionIngestHandler,
    _FrameRejected,
)
from weapon_detection_agent.detection.protocol import (
    FRAME_KIND_DETECTION,
    FRAME_LENGTH_BYTEORDER,
    FRAME_LENGTH_PREFIX_BYTES,
    MAX_FRAME_BYTES,
    encode_frame,
)
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository

requires_unix_sockets = pytest.mark.skipif(
    not hasattr(asyncio, "start_unix_server"),
    reason="asyncio Unix domain sockets are unavailable on this platform",
)
requires_posix_modes = pytest.mark.skipif(
    not modes_enforceable(),
    reason="POSIX permission modes are not enforceable on this platform",
)

CLASS_NAMES = {0: "gun", 1: "knife"}
DEVICE_ID = "device-11111111-2222-3333-4444-555555555555"
CAMERA_ID = "camera1"
FIXED_NOW = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)


# --- Shared fakes/helpers ------------------------------------------------------------------------


class _FixedClock:
    """An Agent UTC clock stand-in that only advances when a test tells it to."""

    def __init__(self, value: datetime = FIXED_NOW) -> None:
        self._value = value

    def __call__(self) -> datetime:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value = self._value + timedelta(seconds=seconds)


class _FakeMonotonicClock:
    """A monotonic-clock stand-in for ``DetectionCooldownTracker`` (mirrors
    test_detection_cooldown's FakeClock)."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _SequentialEventIdFactory:
    """A deterministic ``event_id_factory`` producing distinct, predictable UUIDs."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def __call__(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class _FailingOnceRepository:
    """Wraps a real ``DetectionEventRepository``; its first ``insert`` raises, the rest delegate."""

    def __init__(self, real: DetectionEventRepository) -> None:
        self._real = real
        self.call_count = 0

    def insert(self, event: object) -> None:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("simulated persistence failure")
        self._real.insert(event)  # type: ignore[arg-type]

    def list_recent(self, limit: int) -> list[object]:
        return self._real.list_recent(limit)


class _FakeStreamReader:
    """A minimal stand-in for ``asyncio.StreamReader.readexactly``, fed from byte chunks.

    Each call consumes exactly ``n`` bytes from an internal buffer replenished chunk-by-chunk from
    ``chunks`` — simulating data arriving over several separate socket reads (a length prefix or
    payload split across writes). Raises ``asyncio.IncompleteReadError`` once the chunks are
    exhausted with fewer than ``n`` bytes remaining, mirroring a real closed connection.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._buffer = bytearray()

    async def readexactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            if not self._chunks:
                partial = bytes(self._buffer)
                self._buffer.clear()
                raise asyncio.IncompleteReadError(partial=partial, expected=n)
            self._buffer.extend(self._chunks.pop(0))
        result = bytes(self._buffer[:n])
        del self._buffer[:n]
        return result


def _ready_db(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file


def _handler(
    *,
    socket_path: Path,
    repository: DetectionEventRepository,
    device_id: str = DEVICE_ID,
    camera_id: str = CAMERA_ID,
    camera_id_resolver: Callable[[int], UUID | None] | None = None,
    class_names: Mapping[int, str] = CLASS_NAMES,
    min_confidence: float = 0.5,
    queue_capacity: int = 10,
    cooldown_tracker: DetectionCooldownTracker | None = None,
    clock: Callable[[], datetime] | None = None,
    event_id_factory: Callable[[], UUID] | None = None,
) -> DetectionIngestHandler:
    return DetectionIngestHandler(
        socket_path=socket_path,
        queue_capacity=queue_capacity,
        device_id_provider=lambda: device_id,
        camera_id=camera_id,
        camera_id_resolver=camera_id_resolver,
        class_names=class_names,
        min_confidence=min_confidence,
        cooldown_tracker=cooldown_tracker
        or DetectionCooldownTracker(cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()),
        repository=repository,
        clock=clock or _FixedClock(),
        event_id_factory=event_id_factory or _SequentialEventIdFactory(),
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "class_id": 0,
        "confidence": 0.91,
        "source_id": 0,
        "frame_number": 12345,
        "frame_width": 640,
        "frame_height": 640,
        "bbox_left": 210.0,
        "bbox_top": 130.0,
        "bbox_width": 95.0,
        "bbox_height": 70.0,
    }
    payload.update(overrides)
    return payload


def _frame_bytes(payload_obj: dict[str, Any]) -> bytes:
    return encode_frame(json.dumps(payload_obj).encode("utf-8"))


def _split(data: bytes, *cut_points: int) -> list[bytes]:
    points = [0, *cut_points, len(data)]
    return [data[a:b] for a, b in zip(points[:-1], points[1:], strict=True)]


# ==================================================================================================
# Constructor
# ==================================================================================================


def test_name_is_static(tmp_path: Path) -> None:
    handler = _handler(
        socket_path=tmp_path / "detection.sock",
        repository=DetectionEventRepository(_ready_db(tmp_path)),
    )
    assert handler.name == "detection-ingest"


def test_queue_capacity_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
            queue_capacity=0,
        )


# ==================================================================================================
# Framing (portable — FakeStreamReader, no real socket)
# ==================================================================================================


def test_one_valid_length_prefixed_message(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        frame = _frame_bytes(_valid_payload())
        reader = _FakeStreamReader([frame])

        message = await handler._read_message(reader)  # type: ignore[arg-type]

        assert message == _valid_payload()

    asyncio.run(_scenario())


def test_prefix_split_across_several_chunks(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        frame = _frame_bytes(_valid_payload())
        # Split within the 4-byte length prefix itself.
        reader = _FakeStreamReader(_split(frame, 1, 3))

        message = await handler._read_message(reader)  # type: ignore[arg-type]

        assert message == _valid_payload()

    asyncio.run(_scenario())


def test_payload_split_across_several_chunks(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        frame = _frame_bytes(_valid_payload())
        # Prefix delivered whole; the payload itself arrives in three pieces.
        reader = _FakeStreamReader(
            _split(frame, FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_PREFIX_BYTES + 5)
        )

        message = await handler._read_message(reader)  # type: ignore[arg-type]

        assert message == _valid_payload()

    asyncio.run(_scenario())


def test_multiple_messages_over_one_reader(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        first = _frame_bytes(_valid_payload(frame_number=1))
        second = _frame_bytes(_valid_payload(frame_number=2))
        reader = _FakeStreamReader([first + second])

        message_one = await handler._read_message(reader)  # type: ignore[arg-type]
        message_two = await handler._read_message(reader)  # type: ignore[arg-type]

        assert message_one is not None and message_one["frame_number"] == 1
        assert message_two is not None and message_two["frame_number"] == 2

    asyncio.run(_scenario())


def test_clean_eof_between_messages_returns_none(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([])

        message = await handler._read_message(reader)  # type: ignore[arg-type]

        assert message is None

    asyncio.run(_scenario())


def test_disconnect_during_prefix_raises_incomplete_read(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([b"\x00\x00"])  # 2 of 4 prefix bytes, then EOF

        with pytest.raises(asyncio.IncompleteReadError):
            await handler._read_message(reader)  # type: ignore[arg-type]

    asyncio.run(_scenario())


def test_disconnect_during_payload_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        frame = _frame_bytes(_valid_payload())
        truncated = frame[: FRAME_LENGTH_PREFIX_BYTES + 3]  # prefix + a few payload bytes only
        reader = _FakeStreamReader([truncated])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "incomplete_payload"

    asyncio.run(_scenario())


def test_zero_length_frame_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([encode_frame(b"")])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "zero_length_frame"

    asyncio.run(_scenario())


def test_oversized_frame_is_rejected_before_reading_payload(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        oversized_header = FRAME_KIND_DETECTION.to_bytes(1, FRAME_LENGTH_BYTEORDER) + (
            MAX_FRAME_BYTES + 1
        ).to_bytes(FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        # No payload bytes supplied at all — proves rejection happens from the header alone, never
        # attempting to read (MAX_FRAME_BYTES + 1) bytes that were never sent.
        reader = _FakeStreamReader([oversized_header])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "oversized_frame"

    asyncio.run(_scenario())


def test_malformed_utf8_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([encode_frame(b"\xff\xfe\x00\x01")])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "invalid_utf8"

    asyncio.run(_scenario())


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([encode_frame(b"{not valid json")])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "invalid_json"

    asyncio.run(_scenario())


def test_non_object_json_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([encode_frame(b"[1, 2, 3]")])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "invalid_json_shape"

    asyncio.run(_scenario())


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        reader = _FakeStreamReader([_frame_bytes(_valid_payload(schema_version=99))])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "unsupported_schema_version"

    asyncio.run(_scenario())


def test_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        payload = _valid_payload()
        del payload["schema_version"]
        reader = _FakeStreamReader([_frame_bytes(payload)])

        with pytest.raises(_FrameRejected) as excinfo:
            await handler._read_message(reader)  # type: ignore[arg-type]
        assert excinfo.value.reason == "unsupported_schema_version"

    asyncio.run(_scenario())


# ==================================================================================================
# Processing: validation -> cooldown -> persistence (portable — real SQLite, no real socket)
# ==================================================================================================


def test_valid_gun_persists_one_pending_event(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(socket_path=tmp_path / "detection.sock", repository=repo)

        await handler._process(_valid_payload(class_id=0))

        (event,) = repo.list_recent(10)
        assert event.class_name == "gun"

    asyncio.run(_scenario())


def test_valid_knife_persists_one_pending_event(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(socket_path=tmp_path / "detection.sock", repository=repo)

        await handler._process(_valid_payload(class_id=1))

        (event,) = repo.list_recent(10)
        assert event.class_name == "knife"

    asyncio.run(_scenario())


def test_low_confidence_detection_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, min_confidence=0.5
        )

        await handler._process(_valid_payload(confidence=0.1))

        assert repo.list_recent(10) == []

    asyncio.run(_scenario())


def test_unknown_class_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(socket_path=tmp_path / "detection.sock", repository=repo)

        await handler._process(_valid_payload(class_id=99))

        assert repo.list_recent(10) == []

    asyncio.run(_scenario())


def test_malformed_bbox_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(socket_path=tmp_path / "detection.sock", repository=repo)

        await handler._process(_valid_payload(bbox_left=-5.0))

        assert repo.list_recent(10) == []

    asyncio.run(_scenario())


def test_agent_supplies_identity_never_the_wire(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=repo,
            device_id=DEVICE_ID,
            camera_id=CAMERA_ID,
        )
        forged = _valid_payload(
            event_id="11111111-1111-1111-1111-111111111111",
            device_id="attacker-device",
            camera_id="attacker-camera",
            class_name="hacked",
            detected_at_utc="1999-01-01T00:00:00Z",
            created_at_utc="1999-01-01T00:00:00Z",
            delivery_status="delivered",
        )

        await handler._process(forged)

        (event,) = repo.list_recent(10)
        assert event.device_id == DEVICE_ID
        assert event.camera_id == CAMERA_ID
        assert event.class_name == "gun"
        assert str(event.event_id) != "11111111-1111-1111-1111-111111111111"
        assert event.detected_at_utc == FIXED_NOW

    asyncio.run(_scenario())


def test_duplicate_frames_inside_cooldown_produce_one_row(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        mono_clock = _FakeMonotonicClock()
        tracker = DetectionCooldownTracker(cooldown_seconds=5.0, monotonic_clock=mono_clock)
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, cooldown_tracker=tracker
        )

        await handler._process(_valid_payload())
        await handler._process(_valid_payload())

        assert len(repo.list_recent(10)) == 1

    asyncio.run(_scenario())


def test_event_after_cooldown_produces_another_row(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        mono_clock = _FakeMonotonicClock()
        tracker = DetectionCooldownTracker(cooldown_seconds=5.0, monotonic_clock=mono_clock)
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, cooldown_tracker=tracker
        )

        await handler._process(_valid_payload())
        mono_clock.advance(5.0)
        await handler._process(_valid_payload())

        assert len(repo.list_recent(10)) == 2

    asyncio.run(_scenario())


def test_gun_and_knife_cooldowns_are_independent(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        mono_clock = _FakeMonotonicClock()
        tracker = DetectionCooldownTracker(cooldown_seconds=5.0, monotonic_clock=mono_clock)
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, cooldown_tracker=tracker
        )

        await handler._process(_valid_payload(class_id=0))
        await handler._process(_valid_payload(class_id=1))

        assert len(repo.list_recent(10)) == 2

    asyncio.run(_scenario())


def test_different_cameras_are_independent(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        shared_tracker = DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler_a = _handler(
            socket_path=tmp_path / "a.sock",
            repository=repo,
            camera_id="camera1",
            cooldown_tracker=shared_tracker,
            event_id_factory=_SequentialEventIdFactory(start=1),
        )
        handler_b = _handler(
            socket_path=tmp_path / "b.sock",
            repository=repo,
            camera_id="camera2",
            cooldown_tracker=shared_tracker,
            event_id_factory=_SequentialEventIdFactory(start=1000),
        )

        await handler_a._process(_valid_payload())
        await handler_b._process(_valid_payload())

        assert len(repo.list_recent(10)) == 2

    asyncio.run(_scenario())


def test_different_devices_are_independent(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        shared_tracker = DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler_a = _handler(
            socket_path=tmp_path / "a.sock",
            repository=repo,
            device_id="device-a",
            cooldown_tracker=shared_tracker,
            event_id_factory=_SequentialEventIdFactory(start=1),
        )
        handler_b = _handler(
            socket_path=tmp_path / "b.sock",
            repository=repo,
            device_id="device-b",
            cooldown_tracker=shared_tracker,
            event_id_factory=_SequentialEventIdFactory(start=1000),
        )

        await handler_a._process(_valid_payload())
        await handler_b._process(_valid_payload())

        assert len(repo.list_recent(10)) == 2

    asyncio.run(_scenario())


def test_suppressed_events_are_not_persisted(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        tracker = DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, cooldown_tracker=tracker
        )

        await handler._process(_valid_payload())
        assert len(repo.list_recent(10)) == 1

        await handler._process(_valid_payload())  # suppressed
        assert len(repo.list_recent(10)) == 1  # unchanged

    asyncio.run(_scenario())


def test_persistence_failure_does_not_kill_processing_and_later_messages_succeed(
    tmp_path: Path,
) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        real_repo = DetectionEventRepository(db)
        failing_repo = _FailingOnceRepository(real_repo)
        tracker = DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=failing_repo,  # type: ignore[arg-type]
            cooldown_tracker=tracker,
        )

        # First call: persistence raises. Must not propagate, must not crash the handler.
        await handler._process(_valid_payload())
        assert real_repo.list_recent(10) == []

        # Second call: a later, genuinely new message is still processed normally.
        await handler._process(_valid_payload())
        assert len(real_repo.list_recent(10)) == 1

    asyncio.run(_scenario())


def test_persistence_failure_does_not_consume_the_cooldown_window(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        real_repo = DetectionEventRepository(db)
        failing_repo = _FailingOnceRepository(real_repo)
        mono_clock = _FakeMonotonicClock()
        tracker = DetectionCooldownTracker(cooldown_seconds=5.0, monotonic_clock=mono_clock)
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=failing_repo,  # type: ignore[arg-type]
            cooldown_tracker=tracker,
        )

        # A failed persist must not start a phantom cooldown window.
        await handler._process(_valid_payload())
        assert real_repo.list_recent(10) == []

        mono_clock.advance(0.001)  # well within what would have been a 5s window
        await handler._process(_valid_payload())

        assert len(real_repo.list_recent(10)) == 1

    asyncio.run(_scenario())


def test_duplicate_event_id_repository_error_is_handled_safely(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        # Zero-second cooldown always ACCEPTs, and a fixed event_id_factory forces a genuine
        # EventId-uniqueness collision on the second insert — the defensive path IP-07 T-85's
        # repository exists for.
        tracker = DetectionCooldownTracker(
            cooldown_seconds=0.0, monotonic_clock=_FakeMonotonicClock()
        )
        fixed_id = UUID(int=42)
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=repo,
            cooldown_tracker=tracker,
            event_id_factory=lambda: fixed_id,
        )

        await handler._process(_valid_payload())
        await handler._process(_valid_payload())  # would collide on EventId

        assert len(repo.list_recent(10)) == 1  # the original row only, never overwritten

    asyncio.run(_scenario())


# ==================================================================================================
# Queue / backpressure (portable — manual queue assignment, no real socket needed)
# ==================================================================================================


def test_enqueue_drops_newest_when_queue_is_full(tmp_path: Path) -> None:
    handler = _handler(
        socket_path=tmp_path / "detection.sock",
        repository=DetectionEventRepository(_ready_db(tmp_path)),
        queue_capacity=2,
    )
    handler._queue = asyncio.Queue(maxsize=2)

    handler._enqueue(_valid_payload(frame_number=1))
    handler._enqueue(_valid_payload(frame_number=2))
    handler._enqueue(_valid_payload(frame_number=3))  # dropped: queue full

    assert handler._queue.qsize() == 2
    assert handler._dropped_count == 1
    first, second = handler._queue.get_nowait(), handler._queue.get_nowait()
    assert first["frame_number"] == 1
    assert second["frame_number"] == 2  # the newest (3) was dropped, not an older item


def test_enqueue_before_start_is_a_safe_no_op(tmp_path: Path) -> None:
    handler = _handler(
        socket_path=tmp_path / "detection.sock",
        repository=DetectionEventRepository(_ready_db(tmp_path)),
    )
    # self._queue is None until start(); enqueueing must not raise.
    handler._enqueue(_valid_payload())


# ==================================================================================================
# Real Unix domain socket tests
# ==================================================================================================


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition not met within the timeout")
        await asyncio.sleep(0.01)


async def _open_client(socket_path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_unix_connection(path=str(socket_path))


@requires_unix_sockets
def test_start_creates_the_socket_file(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()
        try:
            assert socket_path.exists()
            assert stat.S_ISSOCK(os.lstat(socket_path).st_mode)
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
@requires_posix_modes
def test_socket_mode_is_restrictive(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()
        try:
            assert stat.S_IMODE(os.lstat(socket_path).st_mode) == SOCKET_FILE_MODE
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stop_removes_only_the_socket(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        unrelated = tmp_path / "unrelated.txt"
        unrelated.write_text("keep me")
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()
        await handler.stop()

        assert not socket_path.exists()
        assert unrelated.exists()
        assert unrelated.read_text() == "keep me"

    asyncio.run(_scenario())


@requires_unix_sockets
def test_duplicate_start_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()
        try:
            with pytest.raises(DetectionIngestHandlerAlreadyRunningError):
                await handler.start()
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stop_is_idempotent(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()
        await handler.stop()
        await handler.stop()  # second call is a safe no-op

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stop_without_start_is_a_safe_no_op(tmp_path: Path) -> None:
    async def _scenario() -> None:
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=DetectionEventRepository(_ready_db(tmp_path)),
        )
        await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stale_socket_is_safely_removed(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        stale = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        stale.bind(str(socket_path))
        stale.close()  # leaves the socket file behind, as a crashed prior process would

        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        await handler.start()  # must not raise; the stale socket is replaced
        try:
            assert socket_path.exists()
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_regular_file_at_socket_path_is_not_deleted(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        socket_path.write_text("not a socket")
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        with pytest.raises(DetectionSocketPathConflictError):
            await handler.start()

        assert socket_path.read_text() == "not a socket"

    asyncio.run(_scenario())


@requires_unix_sockets
def test_directory_at_socket_path_is_not_removed(tmp_path: Path) -> None:
    """IP-07 T-90 item 7: a directory sitting at the socket path is rejected exactly like a
    regular file — never destructively removed to make way for the socket."""

    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        socket_path.mkdir()
        (socket_path / "marker.txt").write_text("proof this directory survives", encoding="utf-8")
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        with pytest.raises(DetectionSocketPathConflictError):
            await handler.start()

        assert socket_path.is_dir()
        assert (socket_path / "marker.txt").read_text(
            encoding="utf-8"
        ) == "proof this directory survives"

    asyncio.run(_scenario())


@requires_unix_sockets
@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires elevated privileges on Windows"
)
def test_symlink_at_socket_path_is_not_followed_or_removed(tmp_path: Path) -> None:
    """A symlink at the socket path — even one pointing at a real file elsewhere — is rejected via
    ``lstat`` (never ``stat``, which would follow it) exactly like any other non-socket type; the
    symlink itself is left in place, and its target is never touched."""

    async def _scenario() -> None:
        target = tmp_path / "elsewhere.txt"
        target.write_text("target contents must survive untouched", encoding="utf-8")
        socket_path = tmp_path / "detection.sock"
        socket_path.symlink_to(target)

        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )

        with pytest.raises(DetectionSocketPathConflictError):
            await handler.start()

        assert socket_path.is_symlink()
        assert target.read_text(encoding="utf-8") == "target contents must survive untouched"

    asyncio.run(_scenario())


def test_missing_runtime_directory_raises(tmp_path: Path) -> None:
    # _ensure_socket_path_ready is checked before the repository/queue/server are ever touched, so a
    # real repository is unnecessary here — the socket path's parent simply does not exist.
    handler = _handler(
        socket_path=tmp_path / "does-not-exist" / "detection.sock",
        repository=DetectionEventRepository(_ready_db(tmp_path)),
    )

    async def _scenario() -> None:
        with pytest.raises(DetectionRuntimeDirectoryMissingError):
            await handler.start()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_end_to_end_single_valid_message_is_persisted(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        repo = DetectionEventRepository(_ready_db(tmp_path))
        handler = _handler(socket_path=socket_path, repository=repo)

        await handler.start()
        try:
            reader, writer = await _open_client(socket_path)
            writer.write(_frame_bytes(_valid_payload()))
            await writer.drain()

            await _wait_until(lambda: len(repo.list_recent(10)) == 1)

            writer.close()
            await writer.wait_closed()
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_multiple_messages_on_one_connection_end_to_end(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        repo = DetectionEventRepository(_ready_db(tmp_path))
        tracker = DetectionCooldownTracker(
            cooldown_seconds=0.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler = _handler(socket_path=socket_path, repository=repo, cooldown_tracker=tracker)

        await handler.start()
        try:
            reader, writer = await _open_client(socket_path)
            writer.write(_frame_bytes(_valid_payload(class_id=0)))
            writer.write(_frame_bytes(_valid_payload(class_id=1)))
            await writer.drain()

            await _wait_until(lambda: len(repo.list_recent(10)) == 2)

            writer.close()
            await writer.wait_closed()
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_malformed_client_does_not_affect_server_or_other_clients(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        repo = DetectionEventRepository(_ready_db(tmp_path))
        handler = _handler(socket_path=socket_path, repository=repo)

        await handler.start()
        try:
            # Client A sends an oversized frame and is disconnected.
            _reader_a, writer_a = await _open_client(socket_path)
            oversized_prefix = (MAX_FRAME_BYTES + 1).to_bytes(
                FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER
            )
            writer_a.write(oversized_prefix)
            await writer_a.drain()
            with suppress(Exception):
                writer_a.close()
                await writer_a.wait_closed()

            # Client B connects afterward and succeeds normally — the server is still operational.
            _reader_b, writer_b = await _open_client(socket_path)
            writer_b.write(_frame_bytes(_valid_payload()))
            await writer_b.drain()

            await _wait_until(lambda: len(repo.list_recent(10)) == 1)

            writer_b.close()
            await writer_b.wait_closed()
        finally:
            await handler.stop()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stop_closes_active_connection_cleanly(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        repo = DetectionEventRepository(_ready_db(tmp_path))
        handler = _handler(socket_path=socket_path, repository=repo)

        await handler.start()
        reader, writer = await _open_client(socket_path)
        # No message sent — the connection is left open (blocked on the length prefix) when stop()
        # runs, proving a blocked read is cancelled/closed cleanly rather than hanging stop().

        await asyncio.wait_for(handler.stop(), timeout=5.0)

        # The client observes the server side closing the connection.
        data = await reader.read()
        assert data == b""
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_stop_returns_within_a_bounded_time(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        handler = _handler(
            socket_path=socket_path, repository=DetectionEventRepository(_ready_db(tmp_path))
        )
        await handler.start()
        _reader, writer = await _open_client(socket_path)

        await asyncio.wait_for(handler.stop(), timeout=5.0)

        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    asyncio.run(_scenario())


@requires_unix_sockets
def test_no_orphan_tasks_remain_after_stop(tmp_path: Path) -> None:
    async def _scenario() -> None:
        socket_path = tmp_path / "detection.sock"
        repo = DetectionEventRepository(_ready_db(tmp_path))
        handler = _handler(socket_path=socket_path, repository=repo)

        before = asyncio.current_task()
        await handler.start()
        reader, writer = await _open_client(socket_path)
        writer.write(_frame_bytes(_valid_payload()))
        await writer.drain()
        await _wait_until(lambda: len(repo.list_recent(10)) == 1)
        writer.close()
        await writer.wait_closed()

        await handler.stop()

        remaining = {
            task
            for task in asyncio.all_tasks()
            if task is not before and not task.done() and "detection-ingest" in (task.get_name())
        }
        assert remaining == set()

    asyncio.run(_scenario())


# The accept-registration race _ACCEPT_DRAIN_YIELDS exists to close (see ingest_handler.py): a
# client's connect() can complete one event-loop tick before asyncio finishes registering that
# connection with this handler. A single pass rarely lands in that narrow scheduling window, so
# this repeats the connect-then-immediately-stop scenario many times to make the race exercise
# itself deterministically rather than by chance.
_STRESS_ITERATIONS = 200


@requires_unix_sockets
def test_connect_then_immediate_stop_never_leaks_across_many_iterations(tmp_path: Path) -> None:
    async def _one_iteration(repo: DetectionEventRepository, socket_path: Path, index: int) -> None:
        tracker = DetectionCooldownTracker(
            cooldown_seconds=5.0, monotonic_clock=_FakeMonotonicClock()
        )
        handler = _handler(socket_path=socket_path, repository=repo, cooldown_tracker=tracker)
        tasks_before = {task for task in asyncio.all_tasks() if not task.done()}

        await handler.start()
        reader, writer = await _open_client(socket_path)

        await asyncio.wait_for(handler.stop(), timeout=5.0)

        data = await asyncio.wait_for(reader.read(), timeout=5.0)
        assert data == b"", f"iteration {index}: client never observed EOF"
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

        assert not socket_path.exists(), f"iteration {index}: socket file not removed"

        leaked = {
            task
            for task in asyncio.all_tasks()
            if task not in tasks_before
            and not task.done()
            and "detection-ingest" in (task.get_name())
        }
        assert leaked == set(), f"iteration {index}: leaked task(s) {leaked}"

    async def _scenario() -> None:
        repo = DetectionEventRepository(_ready_db(tmp_path))
        socket_path = tmp_path / "stress.sock"
        for index in range(_STRESS_ITERATIONS):
            await asyncio.wait_for(_one_iteration(repo, socket_path, index), timeout=10.0)

    asyncio.run(_scenario())


# --- camera_id_resolver (FS-11 §9, IP-13 T-238) --------------------------------------------------


def test_camera_id_resolver_used_when_provided(tmp_path: Path) -> None:
    resolved_camera_id = uuid4()

    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=repo,
            camera_id_resolver=lambda source_id: resolved_camera_id if source_id == 0 else None,
        )

        await handler._process(_valid_payload(source_id=0))

        rows = repo.list_recent(10)
        assert len(rows) == 1
        assert rows[0].camera_id == str(resolved_camera_id)

    asyncio.run(_scenario())


def test_camera_id_resolver_unresolved_source_id_is_rejected_not_persisted(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=repo,
            camera_id_resolver=lambda source_id: None,  # every source_id is unknown
        )

        await handler._process(_valid_payload(source_id=0))

        assert repo.list_recent(10) == []

    asyncio.run(_scenario())


def test_camera_id_resolver_not_provided_uses_static_camera_id(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        handler = _handler(
            socket_path=tmp_path / "detection.sock", repository=repo, camera_id=CAMERA_ID
        )

        await handler._process(_valid_payload(source_id=0))

        rows = repo.list_recent(10)
        assert len(rows) == 1
        assert rows[0].camera_id == CAMERA_ID

    asyncio.run(_scenario())


def test_camera_id_resolver_malformed_source_id_is_rejected(tmp_path: Path) -> None:
    async def _scenario() -> None:
        db = _ready_db(tmp_path)
        repo = DetectionEventRepository(db)
        called = False

        def _resolver(source_id: int) -> UUID | None:
            nonlocal called
            called = True
            return uuid4()

        handler = _handler(
            socket_path=tmp_path / "detection.sock",
            repository=repo,
            camera_id_resolver=_resolver,
        )

        payload = _valid_payload()
        payload["source_id"] = "not-an-int"
        await handler._process(payload)

        assert called is False
        assert repo.list_recent(10) == []

    asyncio.run(_scenario())
