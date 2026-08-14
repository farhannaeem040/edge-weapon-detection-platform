"""Detection event Backend sync worker (IP-08 T-106/T-108, FS-06 §7.4/§8/§9).

``DetectionEventSyncWorker`` implements the existing ``OperationalComponent`` protocol (IP-05 T-60)
unchanged, the same way
:class:`~weapon_detection_agent.detection.ingest_handler.DetectionIngestHandler` does — no new
lifecycle abstraction. It is the last component started and the first stopped (FS-06 §8): Backend
availability never gates Agent/DeepStream startup.

**Loop (FS-06 §7.4).** One ``asyncio`` task, started by :meth:`~DetectionEventSyncWorker.start`:
drain :meth:`~weapon_detection_agent.persistence.detection_event_repository.
DetectionEventRepository.list_pending` in bounded batches, send each via
:class:`~weapon_detection_agent.sync.client.BackendSyncClient`, mark only Backend-acknowledged
``EventId``\\ s delivered. Never sends anything while the persisted ``DeviceIdentity`` is not
``Operational`` (the ``OperationalStateCoordinator`` already only starts/keeps-running this
component while ``Operational``, but the loop re-reads and re-checks identity on every iteration
anyway — a defense against the identity changing mid-run, e.g. a reactivation racing an in-flight
batch, FS-06 §7.4). A row whose ``device_id`` does not match the currently loaded identity is never
sent (left pending) and logged distinctly (FS-06 §4.3).

**Retry/backoff (FS-06 §9).** A whole-batch failure (network/timeout/5xx/401/malformed response)
leaves every row in that batch pending and backs off exponentially with bounded jitter, capped at
``max_backoff_seconds``; a success resets the backoff. A ``401`` is handled by
:class:`~weapon_detection_agent.sync.client.BackendSyncClient` exactly like a ``5xx`` — this worker
never locks or reactivates the Agent, and never calls anything that could (FS-06 §6.1).

**Shutdown.** :meth:`~DetectionEventSyncWorker.stop` cancels the task and awaits it — the same
bounded cancel-then-await technique
:meth:`~weapon_detection_agent.detection.ingest_handler.DetectionIngestHandler.stop` already uses —
so it returns promptly whether the task was sleeping or mid-HTTP-request. No exception raised inside
the loop body is ever allowed to kill the task; every iteration catches, logs, backs off, and
continues (a defensive backstop mirroring
:meth:`~weapon_detection_agent.detection.ingest_handler.DetectionIngestHandler._consume`'s own).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.models import OperationalState
from weapon_detection_agent.sync.client import BackendSyncClient
from weapon_detection_agent.sync.models import SyncBatchFailure, SyncBatchResult

if TYPE_CHECKING:
    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )

_LOGGER = logging.getLogger("weapon_detection_agent.sync.worker")

# An awaitable wait of ``delay`` seconds — the same injectable-wait seam as
# ``CredentialValidationMonitor.Sleeper``, so tests never depend on real elapsed time.
Sleeper = Callable[[float], Awaitable[None]]

# A source of a random value in [0.0, 1.0) used only to jitter the backoff delay. Defaults to
# `random.random`; tests inject a fixed value for determinism.
JitterSource = Callable[[], float]

# Truncate an EventId to a short, still-useful-for-correlation prefix before logging it (FS-06 §9's
# "a truncated EventId" safe-log-field). Never the full value.
_LOGGED_EVENT_ID_PREFIX_LENGTH = 8


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DetectionEventSyncWorker:
    """Drains pending ``DetectionEvent`` rows to the Backend (FS-06 §7.4).

    Construct with the T-104 repository, the T-58 identity repository (read-only here — this
    worker never writes identity state), the T-105 client (owned by this worker — closed by
    :meth:`stop` unless the injected client is externally owned), and the FS-06 §10 tuning values.
    ``sleeper`` and ``jitter`` are injectable seams for deterministic tests; leave them at their
    defaults in production.
    """

    def __init__(
        self,
        *,
        repository: DetectionEventRepository,
        identity_repository: DeviceIdentityRepository,
        client: BackendSyncClient,
        batch_size: int,
        idle_interval_seconds: float,
        initial_backoff_seconds: float,
        max_backoff_seconds: float,
        sleeper: Sleeper = asyncio.sleep,
        clock: Callable[[], datetime] = _utc_now,
        jitter: JitterSource = random.random,
        alert_id_sink: Callable[[UUID, str], None] | None = None,
        quota_suppression_sink: Callable[[UUID], None] | None = None,
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
        self._alert_id_sink = alert_id_sink
        self._quota_suppression_sink = quota_suppression_sink

        self._task: asyncio.Task[None] | None = None
        self._backoff_attempt = 0

    # --- OperationalComponent protocol (IP-05 T-60) ---------------------------------------------

    @property
    def name(self) -> str:
        """A safe, static component identifier — never derived from a row or payload."""
        return "detection-event-sync"

    async def start(self) -> None:
        """Launch the drain loop as one background task. Never blocks."""
        if self._task is not None:
            return
        self._backoff_attempt = 0
        self._task = asyncio.create_task(self._run(), name="detection-event-sync")
        _LOGGER.info("detection_sync_worker_started", extra={"component": self.name})

    async def stop(self) -> None:
        """Cancel the drain loop and await it, then close the owned client. Idempotent."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._client.aclose()
        _LOGGER.info("detection_sync_worker_stopped", extra={"component": self.name})

    # --- Drain loop (FS-06 §7.4/§9) --------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                idle = await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A backstop only: _drain_once already handles every expected failure internally
                # (identity gaps, batch failures). Nothing here may kill this task.
                _LOGGER.exception(
                    "detection_sync_worker_iteration_failed", extra={"component": self.name}
                )
                await self._sleep_backoff()
                continue

            if idle:
                await self._sleep(self._idle_interval)

    async def _drain_once(self) -> bool:
        """Run one iteration. Returns ``True`` when the caller should idle-sleep before the next."""
        identity = self._identity_repository.load()
        if identity is None or identity.operational_state is not OperationalState.OPERATIONAL:
            # No usable identity right now (e.g. a reactivation raced this loop) — nothing can be
            # sent this cycle; the OperationalStateCoordinator will stop this component shortly if
            # the Agent is genuinely locked, but this check is the defense-in-depth FS-06 §7.4 asks
            # for regardless of that external guarantee.
            return True

        pending = self._repository.list_pending(self._batch_size)
        if not pending:
            return True

        sendable = []
        mismatched = 0
        for event in pending:
            if event.device_id == identity.device_id:
                sendable.append(event)
            else:
                mismatched += 1
                _LOGGER.warning(
                    "detection_sync_identity_mismatch",
                    extra={
                        "component": self.name,
                        "event_id": str(event.event_id)[:_LOGGED_EVENT_ID_PREFIX_LENGTH],
                    },
                )

        if not sendable:
            # Every pending row in this batch belonged to a stale identity; waiting for the idle
            # interval avoids busy-looping on rows that cannot become sendable without a config/
            # identity change.
            return True

        # OPERATIONAL implies a usable secret (DeviceIdentity's own constructor invariant).
        assert identity.shared_secret is not None  # noqa: S101
        result = await self._client.send_batch(
            device_id=identity.device_id, shared_secret=identity.shared_secret, events=sendable
        )

        if isinstance(result, SyncBatchFailure):
            _LOGGER.warning(
                "detection_sync_batch_failed",
                extra={
                    "component": self.name,
                    "reason": result.reason.value,
                    "status": result.status_code,
                    "batch_size": len(sendable),
                },
            )
            await self._sleep_backoff()
            return False

        self._mark_delivered(result)
        self._mark_suppressed_by_quota(result)
        self._reset_backoff()
        _LOGGER.info(
            "detection_sync_batch_completed",
            extra={
                "component": self.name,
                "batch_size": len(sendable),
                "accepted": len(result.accepted),
                "duplicate": len(result.duplicate),
                "rejected": len(result.rejected),
                "quota_exceeded": len(result.quota_exceeded),
            },
        )

        # A batch containing a mismatched row can never fully drain (that row will keep coming back
        # from list_pending until the identity changes); treat it as idle rather than busy-looping.
        if mismatched:
            return True
        # A short (less-than-requested) batch means the outbox is drained for now; a full batch may
        # have more pending rows immediately behind it — keep draining without sleeping.
        return len(pending) < self._batch_size

    def _mark_delivered(self, result: SyncBatchResult) -> None:
        to_mark = result.accepted | result.duplicate
        if not to_mark:
            return
        delivered_at = self._clock()
        updated = self._repository.mark_delivered_many(list(to_mark), delivered_at)
        if updated != len(to_mark):
            _LOGGER.warning(
                "detection_sync_mark_delivered_count_mismatch",
                extra={
                    "component": self.name,
                    "expected": len(to_mark),
                    "actual": updated,
                },
            )
        self._associate_alert_ids(result)

    def _mark_suppressed_by_quota(self, result: SyncBatchResult) -> None:
        """Terminal-state handling for ``quota_exceeded`` outcomes (FS-09 §6/§9/§10).

        Marks every named ``EventId`` ``'suppressed_by_quota'`` — never ``'delivered'`` — and never
        calls :attr:`_alert_id_sink` for any of them (no ``alertId`` exists on the wire for a
        quota-suppressed event, FS-09 §5, so ``result.alert_ids`` never names one anyway). Cancels
        any associated snapshot outbox row via :attr:`_quota_suppression_sink` so it is never
        selected for upload (FS-09 §10) — an additive side effect with the same fire-and-forget,
        exception-swallowing discipline as :meth:`_associate_alert_ids`.
        """
        to_mark = list(result.quota_exceeded.keys())
        if not to_mark:
            return
        finalized_at = self._clock()
        updated = self._repository.mark_suppressed_by_quota_many(to_mark, finalized_at)
        if updated != len(to_mark):
            _LOGGER.warning(
                "detection_sync_mark_suppressed_by_quota_count_mismatch",
                extra={
                    "component": self.name,
                    "expected": len(to_mark),
                    "actual": updated,
                },
            )
        self._cancel_snapshots_for_quota(to_mark)

    def _cancel_snapshots_for_quota(self, event_ids: list[UUID]) -> None:
        if self._quota_suppression_sink is None:
            return
        for event_id in event_ids:
            try:
                self._quota_suppression_sink(event_id)
            except Exception:
                _LOGGER.exception(
                    "detection_sync_quota_suppression_cancel_failed",
                    extra={
                        "component": self.name,
                        "event_id": str(event_id)[:_LOGGED_EVENT_ID_PREFIX_LENGTH],
                    },
                )

    def _associate_alert_ids(self, result: SyncBatchResult) -> None:
        """Additive side effect only (IP-10 T-143, FS-08 §7/§11) — never changes this worker's own
        delivered-marking semantics above, and never allowed to break the sync loop: any exception
        from the sink (e.g. a genuine AlertId conflict) is logged and swallowed, not raised."""
        if self._alert_id_sink is None or not result.alert_ids:
            return
        for event_id, alert_id in result.alert_ids.items():
            try:
                self._alert_id_sink(event_id, alert_id)
            except Exception:
                _LOGGER.exception(
                    "detection_sync_alert_association_failed",
                    extra={
                        "component": self.name,
                        "event_id": str(event_id)[:_LOGGED_EVENT_ID_PREFIX_LENGTH],
                    },
                )

    # --- Backoff (FS-06 §9) -----------------------------------------------------------------------

    def _reset_backoff(self) -> None:
        self._backoff_attempt = 0

    async def _sleep_backoff(self) -> None:
        delay = min(self._initial_backoff * (2**self._backoff_attempt), self._max_backoff)
        self._backoff_attempt += 1
        # Bounded jitter: uniformly within [50%, 100%] of the computed delay, never zero and never
        # exceeding the computed cap.
        jittered = delay * (0.5 + self._jitter() * 0.5)
        await self._sleep(jittered)


def default_sync_components_factory(
    settings: AgentSettings,
    paths: AgentPaths,
    identity_repository: DeviceIdentityRepository,
) -> tuple[DetectionEventSyncWorker, ...]:
    """Build the real ``DetectionEventSyncWorker`` from Agent-owned dependencies (IP-08 T-108).

    Mirrors ``default_detection_components_factory``'s kill-switch discipline: returns ``()`` when
    ``settings.detection_sync_enabled`` is ``False`` (the shipped default, FS-06 §10) — no worker is
    constructed, no ``DetectionEventRepository``/``BackendSyncClient`` is built, and no persisted
    identity is read. Builds its own ``DetectionEventRepository``/``BackendSyncClient`` the same way
    ``default_detection_components_factory`` builds its own ``DetectionEventRepository`` — never
    shared/injected across factories, matching this codebase's existing composition-root style.
    """
    if not settings.detection_sync_enabled:
        return ()

    client = BackendSyncClient(
        settings.backend_base_url, timeout_seconds=settings.http_timeout_seconds
    )

    alert_id_sink: Callable[[UUID, str], None] | None = None
    quota_suppression_sink: Callable[[UUID], None] | None = None
    if settings.snapshot_capture_enabled:
        # Additive only (IP-10 T-143, FS-09 §10/IP-11 T-181): wires the Backend's per-event AlertId
        # into the snapshot outbox so upload can proceed once both the capture and the metadata-sync
        # sides of FS-08 §7's ordering-independence have happened, in whichever order — and,
        # symmetrically, cancels a snapshot row for a quota-suppressed event so it is never
        # uploaded. Never constructed when snapshot capture is disabled — a disabled feature builds
        # no extra repository/dependency.
        from weapon_detection_agent.persistence.snapshot_outbox_repository import (
            SnapshotOutboxRepository,
        )

        snapshot_repository = SnapshotOutboxRepository(paths.database_file)

        def alert_id_sink(event_id: UUID, alert_id: str) -> None:
            # associate_alert_id returns a bool (whether it applied); the sink's own contract is
            # fire-and-forget (any conflict it raises is caught by _associate_alert_ids above).
            snapshot_repository.associate_alert_id(event_id, alert_id)

        def quota_suppression_sink(event_id: UUID) -> None:
            # FS-09 §10: the local JPEG (if capture already completed) is removed only after the
            # terminal DB state commits — never before, and never leaving a dangling pending upload
            # retry. record is read before cancelling so its local_path is available afterward;
            # cancel_for_quota returns a bool (whether a row existed yet), and the sink's own
            # contract is fire-and-forget (any exception it raises is caught by
            # _cancel_snapshots_for_quota above).
            record = snapshot_repository.get(event_id)
            updated = snapshot_repository.cancel_for_quota(event_id)
            if updated and record is not None:
                with contextlib.suppress(FileNotFoundError):
                    record.local_path.unlink()

    return (
        DetectionEventSyncWorker(
            repository=DetectionEventRepository(paths.database_file),
            identity_repository=identity_repository,
            client=client,
            batch_size=settings.detection_sync_batch_size,
            idle_interval_seconds=settings.detection_sync_interval_seconds,
            initial_backoff_seconds=settings.detection_sync_initial_backoff_seconds,
            max_backoff_seconds=settings.detection_sync_max_backoff_seconds,
            alert_id_sink=alert_id_sink,
            quota_suppression_sink=quota_suppression_sink,
        ),
    )
