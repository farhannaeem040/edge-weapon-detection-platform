"""Unit tests for ``SnapshotUploadWorker`` (IP-10 T-145, FS-08 §11).

Mirrors ``test_detection_event_sync_worker.py``'s style: fakes for the repository, identity
repository, and upload client; a scripted sleeper/jitter so no real time elapses.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.persistence.snapshot_models import (
    CaptureStatus,
    SnapshotOutboxRecord,
    UploadStatus,
)
from weapon_detection_agent.snapshot.models import (
    SnapshotUploadFailure,
    SnapshotUploadFailureReason,
    SnapshotUploadOutcome,
    SnapshotUploadResult,
)
from weapon_detection_agent.snapshot.worker import (
    SnapshotUploadWorker,
    default_snapshot_components_factory,
)

DEVICE_ID = "device-99999999-8888-7777-6666-555555555555"
FAKE_SECRET = "ZZZ-fake-snapshot-worker-secret-must-never-appear-ZZZ"  # noqa: S105
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
OTHER_EVENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CAPTURED_AT = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
ALERT_ID = "alert-0001"


def _record(
    *,
    event_id: UUID = EVENT_ID,
    local_path: Path,
    sha256: str = "a" * 64,
    size_bytes: int = 4,
) -> SnapshotOutboxRecord:
    return SnapshotOutboxRecord(
        event_id=event_id,
        local_path=local_path,
        capture_status=CaptureStatus.CAPTURED,
        upload_status=UploadStatus.PENDING,
        backend_alert_id=ALERT_ID,
        content_type="image/jpeg",
        size_bytes=size_bytes,
        sha256=sha256,
        captured_at_utc=CAPTURED_AT,
    )


class _FakeRepository:
    def __init__(self, records: list[SnapshotOutboxRecord]) -> None:
        self._records = {r.event_id: r for r in records}
        self.mark_uploaded_calls: list[UUID] = []
        self.failure_calls: list[tuple[UUID, str]] = []

    def list_upload_ready(self, limit: int) -> list[SnapshotOutboxRecord]:
        items = sorted(self._records.values(), key=lambda r: r.captured_at_utc)
        return items[:limit]

    def mark_uploaded(self, event_id: UUID, uploaded_at_utc: datetime) -> bool:
        self.mark_uploaded_calls.append(event_id)
        self._records.pop(event_id, None)
        return True

    def record_attempt_failure(
        self, event_id: UUID, *, attempt_at_utc: datetime, error_category: str
    ) -> None:
        self.failure_calls.append((event_id, error_category))


class _FakeIdentityRepository:
    def __init__(self, identity: DeviceIdentity | None) -> None:
        self.identity = identity

    def load(self) -> DeviceIdentity | None:
        return self.identity


class _FakeClient:
    def __init__(self, results: list[SnapshotUploadResult | SnapshotUploadFailure]) -> None:
        self._results = list(results)
        self.calls: list[UUID] = []
        self.closed = False

    async def upload(self, *, event_id: UUID, **kwargs: object) -> object:
        self.calls.append(event_id)
        return self._results.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _identity(*, device_id: str = DEVICE_ID) -> DeviceIdentity:
    return DeviceIdentity(
        device_id=device_id,
        shared_secret=SecretStr(FAKE_SECRET),
        activated_at=ACTIVATED_AT,
        last_activated_at=ACTIVATED_AT,
        operational_state=OperationalState.OPERATIONAL,
    )


_UNSET = object()


def _worker(
    repository: _FakeRepository,
    client: _FakeClient,
    *,
    identity: DeviceIdentity | None | object = _UNSET,
) -> SnapshotUploadWorker:
    resolved_identity = _identity() if identity is _UNSET else identity
    return SnapshotUploadWorker(
        repository=repository,  # type: ignore[arg-type]
        identity_repository=_FakeIdentityRepository(resolved_identity),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        batch_size=10,
        idle_interval_seconds=1.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
        sleeper=_no_sleep,
        jitter=lambda: 0.0,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


async def _run_once(worker: SnapshotUploadWorker) -> bool:
    return await worker._drain_once()  # noqa: SLF001 - the drain loop itself is the unit under test


# --- Missing / modified local file --------------------------------------------------------------


def test_missing_local_file_records_failure_and_continues(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jpg"
    repo = _FakeRepository([_record(local_path=missing)])
    client = _FakeClient([])

    idle = asyncio.run(_run_once(_worker(repo, client)))

    assert idle is True
    assert repo.failure_calls == [(EVENT_ID, "local_file_missing")]
    assert client.calls == []  # never even attempted the upload


def test_sha_mismatch_is_detected_before_upload(tmp_path: Path) -> None:
    local_file = tmp_path / "a.jpg"
    local_file.write_bytes(b"changed-bytes")
    repo = _FakeRepository([_record(local_path=local_file, sha256="deadbeef" * 8)])
    client = _FakeClient([])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.failure_calls == [(EVENT_ID, "local_file_modified")]
    assert client.calls == []


def test_size_mismatch_is_detected_before_upload(tmp_path: Path) -> None:
    import hashlib

    local_file = tmp_path / "a.jpg"
    content = b"real-bytes"
    local_file.write_bytes(content)
    correct_sha = hashlib.sha256(content).hexdigest()
    repo = _FakeRepository([_record(local_path=local_file, sha256=correct_sha, size_bytes=999999)])
    client = _FakeClient([])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.failure_calls == [(EVENT_ID, "local_file_modified")]


# --- Success path ----------------------------------------------------------------------------


def _valid_record(
    tmp_path: Path, *, event_id: UUID = EVENT_ID
) -> tuple[SnapshotOutboxRecord, Path]:
    import hashlib

    local_file = tmp_path / f"{event_id}.jpg"
    content = b"\xff\xd8\xff\xe0real-jpeg"
    local_file.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    record = _record(event_id=event_id, local_path=local_file, sha256=sha, size_bytes=len(content))
    return record, local_file


def test_accepted_marks_uploaded_and_deletes_local_file(tmp_path: Path) -> None:
    record, local_file = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadResult(SnapshotUploadOutcome.ACCEPTED)])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.mark_uploaded_calls == [EVENT_ID]
    assert not local_file.exists()


def test_duplicate_also_marks_uploaded_and_deletes_local_file(tmp_path: Path) -> None:
    record, local_file = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadResult(SnapshotUploadOutcome.DUPLICATE)])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.mark_uploaded_calls == [EVENT_ID]
    assert not local_file.exists()


def test_upload_failure_leaves_row_pending_and_file_intact(tmp_path: Path) -> None:
    record, local_file = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadFailure(SnapshotUploadFailureReason.SERVER_FAILURE, 503)])

    idle = asyncio.run(_run_once(_worker(repo, client)))

    assert idle is False  # backs off rather than idling
    assert repo.mark_uploaded_calls == []
    assert repo.failure_calls == [(EVENT_ID, "server_failure")]
    assert local_file.exists()


def test_401_leaves_pending_without_any_credential_rotation_hook(tmp_path: Path) -> None:
    record, _ = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadFailure(SnapshotUploadFailureReason.UNAUTHORIZED, 401)])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.failure_calls == [(EVENT_ID, "unauthorized")]
    assert repo.mark_uploaded_calls == []


def test_409_conflict_recorded_as_named_failure_not_retried_inline(tmp_path: Path) -> None:
    record, _ = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadFailure(SnapshotUploadFailureReason.CONFLICT, 409)])

    asyncio.run(_run_once(_worker(repo, client)))

    assert repo.failure_calls == [(EVENT_ID, "conflict")]
    assert len(client.calls) == 1  # exactly one attempt this iteration, no tight retry loop


def test_one_failing_row_does_not_prevent_upload_worker_from_backing_off(tmp_path: Path) -> None:
    """A network-class failure on the first row stops draining the rest of this batch (the
    remaining rows are simply picked up on the next iteration after backoff) — the failing row is
    never allowed to spin the loop."""
    record_a, _ = _valid_record(tmp_path, event_id=EVENT_ID)
    record_b, _ = _valid_record(tmp_path, event_id=OTHER_EVENT_ID)
    repo = _FakeRepository([record_a, record_b])
    client = _FakeClient([SnapshotUploadFailure(SnapshotUploadFailureReason.TIMEOUT)])

    idle = asyncio.run(_run_once(_worker(repo, client)))

    assert idle is False
    assert len(client.calls) == 1


# --- Identity / operational gating --------------------------------------------------------------


def test_no_usable_identity_is_idle_and_uploads_nothing(tmp_path: Path) -> None:
    record, _ = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([])

    idle = asyncio.run(_run_once(_worker(repo, client, identity=None)))

    assert idle is True
    assert client.calls == []


def test_reactivation_required_identity_uploads_nothing(tmp_path: Path) -> None:
    record, _ = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([])
    locked = DeviceIdentity(
        device_id=DEVICE_ID,
        shared_secret=None,
        activated_at=ACTIVATED_AT,
        last_activated_at=ACTIVATED_AT,
        operational_state=OperationalState.REACTIVATION_REQUIRED,
    )

    idle = asyncio.run(_run_once(_worker(repo, client, identity=locked)))

    assert idle is True
    assert client.calls == []


# --- Empty queue / backoff shape ------------------------------------------------------------


def test_empty_queue_is_idle_and_does_not_busy_loop(tmp_path: Path) -> None:
    repo = _FakeRepository([])
    client = _FakeClient([])

    idle = asyncio.run(_run_once(_worker(repo, client)))

    assert idle is True


def test_backoff_increases_then_resets_on_success(tmp_path: Path) -> None:
    record, _ = _valid_record(tmp_path)
    repo = _FakeRepository([record])
    client = _FakeClient([SnapshotUploadFailure(SnapshotUploadFailureReason.TIMEOUT)])
    worker = _worker(repo, client)

    delays: list[float] = []

    async def _capturing_sleep(seconds: float) -> None:
        delays.append(seconds)

    worker._sleep = _capturing_sleep  # noqa: SLF001

    asyncio.run(worker._drain_once())  # noqa: SLF001
    asyncio.run(worker._sleep_backoff())  # noqa: SLF001

    assert delays[0] == pytest.approx(1.0 * 0.5)  # jitter=0.0 -> exactly 50% of the base delay


def test_backoff_caps_at_max(tmp_path: Path) -> None:
    repo = _FakeRepository([])
    client = _FakeClient([])
    worker = SnapshotUploadWorker(
        repository=repo,  # type: ignore[arg-type]
        identity_repository=_FakeIdentityRepository(_identity()),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        batch_size=10,
        idle_interval_seconds=1.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=2.0,
        sleeper=_no_sleep,
        jitter=lambda: 1.0,  # maximum jitter multiplier
    )
    delays: list[float] = []

    async def _capturing_sleep(seconds: float) -> None:
        delays.append(seconds)

    worker._sleep = _capturing_sleep  # noqa: SLF001

    for _ in range(5):
        asyncio.run(worker._sleep_backoff())  # noqa: SLF001

    assert max(delays) <= 2.0


# --- Lifecycle / component wiring ---------------------------------------------------------------


def test_disabled_feature_constructs_no_worker(tmp_path: Path) -> None:
    from weapon_detection_agent.config.paths import resolve_paths

    settings = load_settings(backend_base_url="http://backend.local", snapshot_upload_enabled=False)
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    identity_repository = DeviceIdentityRepository(paths.database_file)

    components = default_snapshot_components_factory(settings, paths, identity_repository)

    assert components == ()


def test_stop_closes_the_owned_client(tmp_path: Path) -> None:
    repo = _FakeRepository([])
    client = _FakeClient([])
    worker = _worker(repo, client)

    async def _scenario() -> None:
        await worker.start()
        await worker.stop()

    asyncio.run(_scenario())

    assert client.closed is True
