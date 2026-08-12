"""Snapshot upload worker (IP-10 T-145, FS-08 §11).

``SnapshotUploadWorker`` implements the existing ``OperationalComponent`` protocol (IP-05 T-60)
unchanged, mirroring :class:`~weapon_detection_agent.sync.worker.DetectionEventSyncWorker`'s drain
loop / backoff / two-layer-kill-switch / bounded cancel-then-await shutdown technique exactly.

**Loop (FS-08 §11).** One row at a time from :meth:`~weapon_detection_agent.persistence.
snapshot_outbox_repository.SnapshotOutboxRepository.list_upload_ready`: verify the local file
still exists and its current size/SHA-256 still match the row's recorded metadata (a local-storage
problem never blocks a later row — it is recorded and the loop moves on); upload via
:class:`~weapon_detection_agent.snapshot.client.SnapshotUploadClient`; mark the row ``uploaded``
only after an ``accepted``/``duplicate`` response, then delete the local JPEG only after that
SQLite commit succeeds. A network/5xx/401/409 failure is recorded, the row stays ``pending``, and
the whole iteration backs off before the next attempt — identical posture to
``DetectionEventSyncWorker``'s own FS-06 §6.1/§9 rules (never rotates credentials, never
locks/reactivates on a 401, a 409 conflict is a named integrity issue, never retried in a tight
loop).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from weapon_detection_agent.persistence.models import OperationalState
from weapon_detection_agent.persistence.snapshot_models import SnapshotOutboxRecord
from weapon_detection_agent.persistence.snapshot_outbox_repository import SnapshotOutboxRepository
from weapon_detection_agent.snapshot.client import SnapshotUploadClient
from weapon_detection_agent.snapshot.models import (
    SnapshotUploadFailure,
    SnapshotUploadFailureReason,
)

if TYPE_CHECKING:
    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )

_LOGGER = logging.getLogger("weapon_detection_agent.snapshot.worker")

Sleeper = Callable[[float], Awaitable[None]]
JitterSource = Callable[[], float]

_LOGGED_EVENT_ID_PREFIX_LENGTH = 8

# The read chunk size used to recompute a local file's SHA-256 before upload (FS-08 §11's "verify
# ... size/SHA-256 match" step) — bounded so an unexpectedly huge/corrupt file never causes an
# unbounded single read into memory.
_HASH_CHUNK_BYTES = 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_file_hash_and_size(path: object) -> tuple[str, int] | None:
    """Return ``(sha256_hex, size_bytes)`` for the file, or ``None`` if it is missing."""
    p = Path(path)  # type: ignore[arg-type]
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    size = 0
    with p.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class SnapshotUploadWorker:
    """Drains upload-ready ``SnapshotOutbox`` rows to the Backend (FS-08 §11)."""

    def __init__(
        self,
        *,
        repository: SnapshotOutboxRepository,
        identity_repository: DeviceIdentityRepository,
        client: SnapshotUploadClient,
        batch_size: int,
        idle_interval_seconds: float,
        initial_backoff_seconds: float,
        max_backoff_seconds: float,
        sleeper: Sleeper = asyncio.sleep,
        clock: Callable[[], datetime] = _utc_now,
        jitter: JitterSource = random.random,
        file_hasher: Callable[[object], tuple[str, int] | None] = _local_file_hash_and_size,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if idle_interval_seconds <= 0:
            raise ValueError("idle_interval_seconds must be positive")
        if initial_backoff_seconds <= 0:
            raise ValueError("initial_backoff_seconds must be positive")
        if max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= initial_backoff_seconds")

        self._repository = repository
        self._identity_repository = identity_repository
        self._client = client
        self._batch_size = batch_size
        self._idle_interval = idle_interval_seconds
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleeper
        self._clock = clock
        self._jitter = jitter
        self._hash_file = file_hasher

        self._task: asyncio.Task[None] | None = None
        self._backoff_attempt = 0

    @property
    def name(self) -> str:
        return "snapshot-upload"

    async def start(self) -> None:
        if self._task is not None:
            return
        self._backoff_attempt = 0
        self._task = asyncio.create_task(self._run(), name="snapshot-upload")
        _LOGGER.info("snapshot_upload_worker_started", extra={"component": self.name})

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._client.aclose()
        _LOGGER.info("snapshot_upload_worker_stopped", extra={"component": self.name})

    async def _run(self) -> None:
        while True:
            try:
                idle = await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    "snapshot_upload_worker_iteration_failed", extra={"component": self.name}
                )
                await self._sleep_backoff()
                continue

            if idle:
                await self._sleep(self._idle_interval)

    async def _drain_once(self) -> bool:
        identity = self._identity_repository.load()
        if identity is None or identity.operational_state is not OperationalState.OPERATIONAL:
            return True

        pending = self._repository.list_upload_ready(self._batch_size)
        if not pending:
            return True

        assert identity.shared_secret is not None  # noqa: S101

        for record in pending:
            outcome = await self._upload_one(
                record, device_id=identity.device_id, shared_secret=identity.shared_secret
            )
            if not outcome:
                await self._sleep_backoff()
                return False

        self._reset_backoff()
        return len(pending) < self._batch_size

    async def _upload_one(
        self, record: SnapshotOutboxRecord, *, device_id: str, shared_secret: object
    ) -> bool:
        """Upload one row. Returns ``True`` to keep draining, ``False`` to back off and stop."""
        local_check = self._hash_file(record.local_path)
        if local_check is None:
            self._repository.record_attempt_failure(
                record.event_id,
                attempt_at_utc=self._clock(),
                error_category=SnapshotUploadFailureReason.LOCAL_FILE_MISSING.value,
            )
            return True  # a local-storage problem never blocks a later row (FS-08 §11)

        actual_sha256, actual_size = local_check
        if actual_sha256 != record.sha256 or actual_size != record.size_bytes:
            self._repository.record_attempt_failure(
                record.event_id,
                attempt_at_utc=self._clock(),
                error_category=SnapshotUploadFailureReason.LOCAL_FILE_MODIFIED.value,
            )
            return True

        assert record.backend_alert_id is not None  # noqa: S101 -- list_upload_ready guarantees this

        result = await self._client.upload(
            device_id=device_id,
            shared_secret=shared_secret,  # type: ignore[arg-type]
            alert_id=record.backend_alert_id,
            event_id=record.event_id,
            local_path=record.local_path,
            content_type=record.content_type,
            sha256=record.sha256,
        )

        if isinstance(result, SnapshotUploadFailure):
            self._repository.record_attempt_failure(
                record.event_id, attempt_at_utc=self._clock(), error_category=result.reason.value
            )
            return False

        self._repository.mark_uploaded(record.event_id, self._clock())
        with contextlib.suppress(FileNotFoundError):
            record.local_path.unlink()
        _LOGGER.info(
            "snapshot_upload_completed",
            extra={
                "component": self.name,
                "event_id": str(record.event_id)[:_LOGGED_EVENT_ID_PREFIX_LENGTH],
                "outcome": result.outcome.value,
            },
        )
        return True

    def _reset_backoff(self) -> None:
        self._backoff_attempt = 0

    async def _sleep_backoff(self) -> None:
        delay = min(self._initial_backoff * (2**self._backoff_attempt), self._max_backoff)
        self._backoff_attempt += 1
        jittered = delay * (0.5 + self._jitter() * 0.5)
        await self._sleep(jittered)


def default_snapshot_components_factory(
    settings: AgentSettings,
    paths: AgentPaths,
    identity_repository: DeviceIdentityRepository,
) -> tuple[SnapshotUploadWorker, ...]:
    """Build the real ``SnapshotUploadWorker`` from Agent-owned dependencies (IP-10 T-147).

    Mirrors ``default_sync_components_factory``'s kill-switch discipline: returns ``()`` when
    ``settings.snapshot_upload_enabled`` is ``False`` (the shipped default, FS-08 §13) — no worker,
    repository, or client is constructed, and no persisted identity is read.
    """
    if not settings.snapshot_upload_enabled:
        return ()

    client = SnapshotUploadClient(
        settings.backend_base_url, timeout_seconds=settings.http_timeout_seconds
    )

    return (
        SnapshotUploadWorker(
            repository=SnapshotOutboxRepository(paths.database_file),
            identity_repository=identity_repository,
            client=client,
            batch_size=settings.snapshot_upload_batch_size,
            idle_interval_seconds=settings.snapshot_upload_interval_seconds,
            initial_backoff_seconds=settings.snapshot_upload_initial_backoff_seconds,
            max_backoff_seconds=settings.snapshot_upload_max_backoff_seconds,
        ),
    )
