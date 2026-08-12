"""Unit tests for the detection event Backend sync worker (IP-08 T-106/T-109, FS-06 §7.4/§8/§9).

Exercised with fakes for the repository, identity repository, and Backend client — no real SQLite,
network, or httpx. A deterministic scripted sleeper/jitter source means no real `asyncio.sleep`
elapses. Real ``DetectionEventRepository``/schema round-trip behavior (list_pending ordering, mark-
delivered semantics) is covered separately in ``test_detection_event_repository.py``; a smaller
number of tests here re-exercise the worker against the real repository to prove restart-resume and
the crash-after-server-commit duplicate-retry scenario end to end.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.paths import resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.persistence import initialize_database
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository
from weapon_detection_agent.persistence.models import DeviceIdentity, OperationalState
from weapon_detection_agent.sync.models import (
    SyncBatchFailure,
    SyncBatchResult,
    SyncEventQuotaInfo,
    SyncFailureReason,
)
from weapon_detection_agent.sync.worker import (
    DetectionEventSyncWorker,
    default_sync_components_factory,
)

DEVICE_ID = "device-99999999-8888-7777-6666-555555555555"
OTHER_DEVICE_ID = "device-00000000-0000-0000-0000-000000000000"
FAKE_SECRET = "ZZZ-fake-sync-worker-secret-must-never-appear-ZZZ"  # noqa: S105 - placeholder
ACTIVATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")
OTHER_EVENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


# --- Fakes ---------------------------------------------------------------------------------------


class _FakeRepository:
    """An in-memory stand-in for ``DetectionEventRepository`` (list_pending/mark_delivered_many)."""

    def __init__(self, events: list[DetectionEvent]) -> None:
        self._pending: dict[UUID, DetectionEvent] = {e.event_id: e for e in events}
        self.mark_delivered_calls: list[tuple[list[UUID], datetime]] = []
        self.mark_suppressed_by_quota_calls: list[tuple[list[UUID], datetime]] = []

    def list_pending(self, limit: int) -> list[DetectionEvent]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        items = sorted(self._pending.values(), key=lambda e: (e.detected_at_utc, e.event_id))
        return items[:limit]

    def mark_delivered_many(self, event_ids: list[UUID], delivered_at_utc: datetime) -> int:
        self.mark_delivered_calls.append((list(event_ids), delivered_at_utc))
        updated = 0
        for event_id in event_ids:
            if event_id in self._pending:
                del self._pending[event_id]
                updated += 1
        return updated

    def mark_suppressed_by_quota_many(
        self, event_ids: list[UUID], finalized_at_utc: datetime
    ) -> int:
        self.mark_suppressed_by_quota_calls.append((list(event_ids), finalized_at_utc))
        updated = 0
        for event_id in event_ids:
            if event_id in self._pending:
                del self._pending[event_id]
                updated += 1
        return updated


class _FakeIdentityRepository:
    def __init__(self, identity: DeviceIdentity | None) -> None:
        self.identity = identity

    def load(self) -> DeviceIdentity | None:
        return self.identity


class _FakeClient:
    def __init__(self, results: list[SyncBatchResult | SyncBatchFailure]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, SecretStr, list[DetectionEvent]]] = []
        self.closed = False

    async def send_batch(
        self, *, device_id: str, shared_secret: SecretStr, events: list[DetectionEvent]
    ) -> SyncBatchResult | SyncBatchFailure:
        self.calls.append((device_id, shared_secret, list(events)))
        return self._results.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _RaisingClient:
    """A client whose send_batch always raises — proves the worker never propagates it."""

    async def send_batch(self, **kwargs: object) -> SyncBatchResult | SyncBatchFailure:
        raise RuntimeError("boom")

    async def aclose(self) -> None:
        pass


class _FakeQuotaSuppressionSink:
    """Records every ``event_id`` passed to it (FS-09 §10, IP-11 T-181)."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[UUID] = []
        self._raises = raises

    def __call__(self, event_id: UUID) -> None:
        self.calls.append(event_id)
        if self._raises:
            raise RuntimeError("boom")


_QUOTA_INFO = SyncEventQuotaInfo(maximum=15, local_date="2026-07-29")


def _identity(
    *, device_id: str = DEVICE_ID, state: OperationalState = OperationalState.OPERATIONAL
) -> DeviceIdentity:
    secret = None if state is OperationalState.REACTIVATION_REQUIRED else SecretStr(FAKE_SECRET)
    return DeviceIdentity(
        device_id=device_id,
        shared_secret=secret,
        activated_at=ACTIVATED_AT,
        last_activated_at=ACTIVATED_AT,
        operational_state=state,
    )


def _event(
    *,
    event_id: UUID = EVENT_ID,
    device_id: str = DEVICE_ID,
    detected_at_utc: datetime = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc),
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        device_id=device_id,
        camera_id="camera1",
        source_id=0,
        class_id=0,
        class_name="gun",
        confidence=0.91,
        frame_number=1,
        detected_at_utc=detected_at_utc,
        frame_width=640,
        frame_height=640,
        bbox_left=0.0,
        bbox_top=0.0,
        bbox_width=10.0,
        bbox_height=10.0,
        created_at_utc=detected_at_utc,
    )


def _worker(
    *,
    repository: _FakeRepository,
    identity: DeviceIdentity | None,
    client: object,
    batch_size: int = 25,
    idle_interval_seconds: float = 1.0,
    initial_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
    sleeper: object = None,
    jitter: object = lambda: 0.0,
    alert_id_sink: object = None,
    quota_suppression_sink: object = None,
) -> DetectionEventSyncWorker:
    return DetectionEventSyncWorker(
        repository=repository,  # type: ignore[arg-type]
        identity_repository=_FakeIdentityRepository(identity),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        batch_size=batch_size,
        idle_interval_seconds=idle_interval_seconds,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        sleeper=sleeper if sleeper is not None else (lambda _delay: asyncio.sleep(0)),
        jitter=jitter,
        alert_id_sink=alert_id_sink,  # type: ignore[arg-type]
        quota_suppression_sink=quota_suppression_sink,  # type: ignore[arg-type]
    )


# --- Constructor validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"idle_interval_seconds": 0},
        {"initial_backoff_seconds": 0},
        {"max_backoff_seconds": 0.5},  # below initial_backoff_seconds default of 1.0
    ],
)
def test_constructor_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "repository": _FakeRepository([]),
        "identity_repository": _FakeIdentityRepository(_identity()),
        "client": _FakeClient([]),
        "batch_size": 25,
        "idle_interval_seconds": 1.0,
        "initial_backoff_seconds": 1.0,
        "max_backoff_seconds": 60.0,
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        DetectionEventSyncWorker(**base)  # type: ignore[arg-type]


# --- _drain_once: empty outbox -----------------------------------------------------------------


def test_empty_outbox_does_not_send_and_signals_idle() -> None:
    repo = _FakeRepository([])
    client = _FakeClient([])
    worker = _worker(repository=repo, identity=_identity(), client=client)

    idle = asyncio.run(worker._drain_once())

    assert idle is True
    assert client.calls == []


def test_no_identity_does_not_send_and_signals_idle() -> None:
    repo = _FakeRepository([_event()])
    client = _FakeClient([])
    worker = _worker(repository=repo, identity=None, client=client)

    idle = asyncio.run(worker._drain_once())

    assert idle is True
    assert client.calls == []


def test_reactivation_required_identity_does_not_send() -> None:
    repo = _FakeRepository([_event()])
    client = _FakeClient([])
    worker = _worker(
        repository=repo,
        identity=_identity(state=OperationalState.REACTIVATION_REQUIRED),
        client=client,
    )

    idle = asyncio.run(worker._drain_once())

    assert idle is True
    assert client.calls == []


# --- _drain_once: successful batch -----------------------------------------------------------


def test_successful_batch_marks_accepted_and_duplicate_delivered() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID), _event(event_id=OTHER_EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset({EVENT_ID}), duplicate=frozenset({OTHER_EVENT_ID}), rejected={}
            )
        ]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    (marked_ids, _delivered_at) = repo.mark_delivered_calls[0]
    assert set(marked_ids) == {EVENT_ID, OTHER_EVENT_ID}


def test_rejected_event_is_never_marked_delivered() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset(), duplicate=frozenset(), rejected={EVENT_ID: "BAD"})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    assert repo.mark_delivered_calls == []
    assert [e.event_id for e in repo.list_pending(10)] == [EVENT_ID]


# --- _drain_once: quota_exceeded (FS-09 §6/§9/§10, IP-11 T-181) --------------------------------


def test_quota_exceeded_event_is_marked_suppressed_by_quota_not_delivered() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    assert repo.mark_delivered_calls == []
    (marked_ids, _finalized_at) = repo.mark_suppressed_by_quota_calls[0]
    assert marked_ids == [EVENT_ID]
    assert repo.list_pending(10) == []


def test_quota_exceeded_event_never_calls_alert_id_sink() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
                alert_ids={},
            )
        ]
    )
    alert_id_sink_calls: list[tuple[UUID, str]] = []
    worker = _worker(
        repository=repo,
        identity=_identity(),
        client=client,
        alert_id_sink=lambda event_id, alert_id: alert_id_sink_calls.append((event_id, alert_id)),
    )

    asyncio.run(worker._drain_once())

    assert alert_id_sink_calls == []


def test_quota_exceeded_event_calls_quota_suppression_sink() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    sink = _FakeQuotaSuppressionSink()
    worker = _worker(
        repository=repo, identity=_identity(), client=client, quota_suppression_sink=sink
    )

    asyncio.run(worker._drain_once())

    assert sink.calls == [EVENT_ID]


def test_quota_suppression_sink_exception_is_swallowed() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    sink = _FakeQuotaSuppressionSink(raises=True)
    worker = _worker(
        repository=repo, identity=_identity(), client=client, quota_suppression_sink=sink
    )

    # Must not raise despite the sink's exception — the sync loop's own success (suppressed_by_quota
    # marking) is unaffected by the additive sink failing.
    asyncio.run(worker._drain_once())

    assert repo.list_pending(10) == []


def test_no_quota_suppression_sink_configured_is_a_no_op() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    assert repo.list_pending(10) == []


def test_mixed_batch_marks_accepted_duplicate_delivered_rejected_pending_and_quota_exceeded_suppressed() -> (  # noqa: E501
    None
):
    duplicate_id = uuid4()
    rejected_id = uuid4()
    quota_id = uuid4()
    repo = _FakeRepository(
        [
            _event(event_id=EVENT_ID),
            _event(event_id=duplicate_id),
            _event(event_id=rejected_id),
            _event(event_id=quota_id),
        ]
    )
    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset({EVENT_ID}),
                duplicate=frozenset({duplicate_id}),
                rejected={rejected_id: "X"},
                quota_exceeded={quota_id: _QUOTA_INFO},
            )
        ]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    delivered_ids = {eid for ids, _ in repo.mark_delivered_calls for eid in ids}
    suppressed_ids = {eid for ids, _ in repo.mark_suppressed_by_quota_calls for eid in ids}
    assert delivered_ids == {EVENT_ID, duplicate_id}
    assert suppressed_ids == {quota_id}
    assert [e.event_id for e in repo.list_pending(10)] == [rejected_id]


def test_partial_batch_marks_only_successful_ids() -> None:
    repo = _FakeRepository(
        [_event(event_id=EVENT_ID), _event(event_id=OTHER_EVENT_ID, device_id=DEVICE_ID)]
    )
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)

    asyncio.run(worker._drain_once())

    (marked_ids, _) = repo.mark_delivered_calls[0]
    assert marked_ids == [EVENT_ID]
    assert [e.event_id for e in repo.list_pending(10)] == [OTHER_EVENT_ID]


# --- _drain_once: identity mismatch ------------------------------------------------------------


def test_mismatched_device_id_row_is_never_sent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="weapon_detection_agent.sync.worker")
    repo = _FakeRepository([_event(event_id=EVENT_ID, device_id=OTHER_DEVICE_ID)])
    client = _FakeClient([])
    worker = _worker(repository=repo, identity=_identity(device_id=DEVICE_ID), client=client)

    idle = asyncio.run(worker._drain_once())

    assert idle is True
    assert client.calls == []
    assert [e.event_id for e in repo.list_pending(10)] == [EVENT_ID]
    assert any(r.message == "detection_sync_identity_mismatch" for r in caplog.records)


def test_matching_rows_still_sent_alongside_mismatched_rows() -> None:
    repo = _FakeRepository(
        [
            _event(event_id=EVENT_ID, device_id=DEVICE_ID),
            _event(event_id=OTHER_EVENT_ID, device_id=OTHER_DEVICE_ID),
        ]
    )
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(device_id=DEVICE_ID), client=client)

    asyncio.run(worker._drain_once())

    (_, _, sent_events) = client.calls[0]
    assert [e.event_id for e in sent_events] == [EVENT_ID]


# --- _drain_once: whole-batch failure ------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        SyncFailureReason.TIMEOUT,
        SyncFailureReason.TRANSPORT_FAILURE,
        SyncFailureReason.SERVER_FAILURE,
        SyncFailureReason.UNAUTHORIZED,
        SyncFailureReason.INVALID_RESPONSE,
    ],
)
def test_whole_batch_failure_leaves_every_row_pending(reason: SyncFailureReason) -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID), _event(event_id=OTHER_EVENT_ID)])
    client = _FakeClient([SyncBatchFailure(reason)])
    worker = _worker(repository=repo, identity=_identity(), client=client)

    idle = asyncio.run(worker._drain_once())

    assert idle is False
    assert repo.mark_delivered_calls == []
    assert {e.event_id for e in repo.list_pending(10)} == {EVENT_ID, OTHER_EVENT_ID}


def test_401_failure_never_touches_identity_repository_state() -> None:
    # The worker's identity repository is read-only here: nothing in the worker calls a mutating
    # method on it, so a 401 cannot lock/reactivate the Agent through this path (FS-06 §6.1).
    repo = _FakeRepository([_event()])
    identity_repo = _FakeIdentityRepository(_identity())
    client = _FakeClient([SyncBatchFailure(SyncFailureReason.UNAUTHORIZED, 401)])
    worker = DetectionEventSyncWorker(
        repository=repo,  # type: ignore[arg-type]
        identity_repository=identity_repo,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        batch_size=25,
        idle_interval_seconds=1.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=60.0,
        sleeper=lambda _d: asyncio.sleep(0),
        jitter=lambda: 0.0,
    )
    assert not hasattr(identity_repo, "mark_reactivation_required")

    asyncio.run(worker._drain_once())

    assert identity_repo.identity is not None
    assert identity_repo.identity.operational_state is OperationalState.OPERATIONAL


# --- Backoff ----------------------------------------------------------------------------------


def test_backoff_increases_with_each_consecutive_failure() -> None:
    repo = _FakeRepository([_event()])
    client = _FakeClient(
        [
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
        ]
    )
    delays: list[float] = []

    async def _sleeper(delay: float) -> None:
        delays.append(delay)

    worker = _worker(
        repository=repo,
        identity=_identity(),
        client=client,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=60.0,
        sleeper=_sleeper,
        jitter=lambda: 1.0,  # no downward jitter: delay == the full computed value
    )

    asyncio.run(worker._drain_once())
    asyncio.run(worker._drain_once())
    asyncio.run(worker._drain_once())

    assert delays == [1.0, 2.0, 4.0]


def test_backoff_is_capped_at_max() -> None:
    repo = _FakeRepository([_event()])
    client = _FakeClient([SyncBatchFailure(SyncFailureReason.SERVER_FAILURE)] * 6)
    delays: list[float] = []

    async def _sleeper(delay: float) -> None:
        delays.append(delay)

    worker = _worker(
        repository=repo,
        identity=_identity(),
        client=client,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=5.0,
        sleeper=_sleeper,
        jitter=lambda: 1.0,
    )

    for _ in range(6):
        asyncio.run(worker._drain_once())

    assert max(delays) <= 5.0
    assert delays[-1] == 5.0


def test_backoff_resets_after_a_success() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID), _event(event_id=OTHER_EVENT_ID)])
    client = _FakeClient(
        [
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
            SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={}),
            SyncBatchFailure(SyncFailureReason.SERVER_FAILURE),
        ]
    )
    delays: list[float] = []

    async def _sleeper(delay: float) -> None:
        delays.append(delay)

    worker = _worker(
        repository=repo,
        identity=_identity(),
        client=client,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=60.0,
        sleeper=_sleeper,
        jitter=lambda: 1.0,
    )

    asyncio.run(worker._drain_once())  # fail -> backoff 1.0
    asyncio.run(worker._drain_once())  # fail -> backoff 2.0
    asyncio.run(worker._drain_once())  # success -> reset
    asyncio.run(worker._drain_once())  # fail again -> back to 1.0

    assert delays == [1.0, 2.0, 1.0]


def test_bounded_jitter_never_exceeds_the_computed_delay() -> None:
    repo = _FakeRepository([_event()])
    client = _FakeClient([SyncBatchFailure(SyncFailureReason.SERVER_FAILURE)])
    delays: list[float] = []

    async def _sleeper(delay: float) -> None:
        delays.append(delay)

    worker = _worker(
        repository=repo,
        identity=_identity(),
        client=client,
        initial_backoff_seconds=2.0,
        max_backoff_seconds=60.0,
        sleeper=_sleeper,
        jitter=lambda: 0.3,
    )

    asyncio.run(worker._drain_once())

    assert 1.0 <= delays[0] <= 2.0  # within [50%, 100%] of the 2.0 computed delay


# --- OperationalComponent protocol / shutdown ---------------------------------------------------


def test_name_is_a_safe_static_identifier() -> None:
    worker = _worker(repository=_FakeRepository([]), identity=_identity(), client=_FakeClient([]))
    assert worker.name == "detection-event-sync"


def test_start_is_idempotent_and_non_blocking() -> None:
    worker = _worker(repository=_FakeRepository([]), identity=_identity(), client=_FakeClient([]))

    async def _run() -> None:
        await worker.start()
        await worker.start()  # second call is a no-op, not a second task
        await worker.stop()

    asyncio.run(_run())


def test_stop_before_start_is_a_safe_no_op() -> None:
    worker = _worker(repository=_FakeRepository([]), identity=_identity(), client=_FakeClient([]))
    asyncio.run(worker.stop())


def test_stop_closes_the_owned_client() -> None:
    client = _FakeClient([])
    worker = _worker(repository=_FakeRepository([]), identity=_identity(), client=client)

    async def _run() -> None:
        await worker.start()
        await worker.stop()

    asyncio.run(_run())
    assert client.closed is True


def test_shutdown_is_prompt_while_sleeping() -> None:
    # A sleeper that never resolves on its own; stop() must still return promptly via cancellation.
    async def _blocking_sleeper(_delay: float) -> None:
        await asyncio.sleep(3600)

    worker = _worker(
        repository=_FakeRepository([]),
        identity=_identity(),
        client=_FakeClient([]),
        sleeper=_blocking_sleeper,
    )

    async def _run() -> None:
        await worker.start()
        await asyncio.wait_for(worker.stop(), timeout=2.0)

    asyncio.run(_run())  # would hang/timeout if stop() were not bounded


def test_shutdown_is_prompt_during_an_in_flight_request() -> None:
    class _NeverReturningClient:
        async def send_batch(self, **kwargs: object) -> SyncBatchResult | SyncBatchFailure:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        async def aclose(self) -> None:
            pass

    repo = _FakeRepository([_event()])
    worker = _worker(repository=repo, identity=_identity(), client=_NeverReturningClient())

    async def _run() -> None:
        await worker.start()
        await asyncio.sleep(0)  # let the task actually start its in-flight request
        await asyncio.wait_for(worker.stop(), timeout=2.0)

    asyncio.run(_run())


def test_exception_in_send_batch_does_not_kill_the_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="weapon_detection_agent.sync.worker")
    repo = _FakeRepository([_event()])
    worker = _worker(repository=repo, identity=_identity(), client=_RaisingClient())

    async def _run() -> None:
        await worker.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await worker.stop()

    asyncio.run(_run())  # must not raise
    assert any(r.message == "detection_sync_worker_iteration_failed" for r in caplog.records)


# --- Idle vs. keep-draining decision --------------------------------------------------------------


def test_full_batch_success_keeps_draining_without_idle_sleep() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client, batch_size=1)

    idle = asyncio.run(worker._drain_once())

    assert idle is False  # a full batch (len(pending) == batch_size) signals "keep draining"


def test_short_batch_success_signals_idle() -> None:
    repo = _FakeRepository([_event(event_id=EVENT_ID)])
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client, batch_size=25)

    idle = asyncio.run(worker._drain_once())

    assert idle is True


# --- default_sync_components_factory (IP-08 T-108) -----------------------------------------------


def test_factory_returns_no_worker_when_disabled(tmp_path: Path) -> None:
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=tmp_path / "weapon-detection",
        detection_sync_enabled=False,
    )
    paths = resolve_paths(settings.root_path).provision()
    initialize_database(paths.database_file)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    components = default_sync_components_factory(settings, paths, identity_repository)

    assert components == ()


def test_factory_returns_one_worker_when_enabled(tmp_path: Path) -> None:
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=tmp_path / "weapon-detection",
        detection_sync_enabled=True,
    )
    paths = resolve_paths(settings.root_path).provision()
    initialize_database(paths.database_file)
    identity_repository = DeviceIdentityRepository(paths.database_file)

    components = default_sync_components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    assert isinstance(components[0], DetectionEventSyncWorker)
    assert components[0].name == "detection-event-sync"


def test_factory_wired_worker_cancels_snapshot_and_removes_jpeg_for_quota_suppressed_event(
    tmp_path: Path,
) -> None:
    # FS-09 §10, IP-11 T-181: the factory-built worker's quota_suppression_sink must cancel any
    # SnapshotOutbox row (never uploaded) and remove the local JPEG only after that terminal DB
    # state commits.
    from weapon_detection_agent.persistence.snapshot_models import UploadStatus
    from weapon_detection_agent.persistence.snapshot_outbox_repository import (
        SnapshotOutboxRepository,
    )

    spool_path = tmp_path / "snapshots"
    settings = load_settings(
        backend_base_url="http://backend.local:5230",
        root_path=tmp_path / "weapon-detection",
        detection_sync_enabled=True,
        detection_events_enabled=True,
        snapshot_capture_enabled=True,
        snapshot_spool_path=spool_path,
        deepstream_enabled=True,
        deepstream_executable_path="/opt/weapon-detection/deepstream-bridge/run.sh",
    )
    paths = resolve_paths(settings.root_path).provision()
    initialize_database(paths.database_file)
    identity_repository = DeviceIdentityRepository(paths.database_file)
    identity_repository.store(_identity())

    repo = DetectionEventRepository(paths.database_file)
    repo.insert(_event(event_id=EVENT_ID))

    snapshot_repository = SnapshotOutboxRepository(paths.database_file)
    jpeg_path = spool_path / f"{EVENT_ID}.jpg"
    jpeg_path.parent.mkdir(parents=True, exist_ok=True)
    jpeg_path.write_bytes(b"fake-jpeg-bytes")
    snapshot_repository.create_captured(
        event_id=EVENT_ID,
        local_path=jpeg_path,
        content_type="image/jpeg",
        size_bytes=len(b"fake-jpeg-bytes"),
        sha256="a" * 64,
    )

    client = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    components = default_sync_components_factory(settings, paths, identity_repository)
    worker = components[0]
    worker._client = client  # type: ignore[attr-defined]

    asyncio.run(worker._drain_once())

    record = snapshot_repository.get(EVENT_ID)
    assert record is not None
    assert record.upload_status is UploadStatus.SUPPRESSED_BY_QUOTA
    assert not jpeg_path.exists()


# --- Real-repository integration: restart-resume and duplicate retry ------------------------------


def _real_repo_setup(tmp_path: Path) -> tuple[Path, DetectionEventRepository]:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    initialize_database(paths.database_file)
    return paths.database_file, DetectionEventRepository(paths.database_file)


def test_worker_resumes_pending_rows_after_restart(tmp_path: Path) -> None:
    db, repo = _real_repo_setup(tmp_path)
    repo.insert(_event(event_id=EVENT_ID))
    repo.insert(_event(event_id=OTHER_EVENT_ID))

    client_1 = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker_1 = _worker(
        repository=repo,
        identity=_identity(),
        client=client_1,  # type: ignore[arg-type]
    )
    asyncio.run(worker_1._drain_once())
    assert [e.event_id for e in repo.list_pending(10)] == [OTHER_EVENT_ID]

    # A fresh worker instance, against the same repository/database state, resumes correctly.
    fresh_repo = DetectionEventRepository(db)
    client_2 = _FakeClient(
        [SyncBatchResult(accepted=frozenset({OTHER_EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker_2 = _worker(
        repository=fresh_repo,
        identity=_identity(),
        client=client_2,  # type: ignore[arg-type]
    )
    asyncio.run(worker_2._drain_once())

    assert repo.list_pending(10) == []


def test_crash_after_server_commit_succeeds_via_duplicate_retry(tmp_path: Path) -> None:
    # Simulates: the Backend already committed the Alert for EVENT_ID (a prior attempt's response
    # was lost before mark_delivered_many ran), but the local row is still 'pending'. The next send
    # gets 'duplicate' back and the worker correctly marks it delivered.
    _db, repo = _real_repo_setup(tmp_path)
    repo.insert(_event(event_id=EVENT_ID))

    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset(), duplicate=frozenset({EVENT_ID}), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)  # type: ignore[arg-type]

    asyncio.run(worker._drain_once())

    assert repo.list_pending(10) == []


def test_restart_does_not_resend_a_quota_suppressed_row(tmp_path: Path) -> None:
    db, repo = _real_repo_setup(tmp_path)
    repo.insert(_event(event_id=EVENT_ID))

    client_1 = _FakeClient(
        [
            SyncBatchResult(
                accepted=frozenset(),
                duplicate=frozenset(),
                rejected={},
                quota_exceeded={EVENT_ID: _QUOTA_INFO},
            )
        ]
    )
    worker_1 = _worker(repository=repo, identity=_identity(), client=client_1)  # type: ignore[arg-type]
    asyncio.run(worker_1._drain_once())
    assert repo.list_pending(10) == []

    # A fresh worker instance, against the same database state, must not resend the now-terminal
    # suppressed_by_quota row.
    fresh_repo = DetectionEventRepository(db)
    client_2 = _FakeClient([])
    worker_2 = _worker(repository=fresh_repo, identity=_identity(), client=client_2)  # type: ignore[arg-type]
    idle = asyncio.run(worker_2._drain_once())

    assert idle is True
    assert client_2.calls == []


def test_delivered_row_is_never_sent_again(tmp_path: Path) -> None:
    _db, repo = _real_repo_setup(tmp_path)
    repo.insert(_event(event_id=EVENT_ID))
    client = _FakeClient(
        [SyncBatchResult(accepted=frozenset({EVENT_ID}), duplicate=frozenset(), rejected={})]
    )
    worker = _worker(repository=repo, identity=_identity(), client=client)  # type: ignore[arg-type]
    asyncio.run(worker._drain_once())

    # A second drain must not resend the now-delivered event.
    idle = asyncio.run(worker._drain_once())

    assert idle is True
    assert len(client.calls) == 1
