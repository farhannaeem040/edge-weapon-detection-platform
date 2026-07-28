# Implementation Plan: Detection Event Backend Synchronization

| Field | Value |
|-------|-------|
| Plan ID | IP-08 |
| Title | Detection Event Backend Synchronization — Backend `SyncEventsController`/`Alert`/SQL Server, Agent `DetectionEventSyncWorker` |
| Status | Draft — awaiting approval; no code written yet |
| Realizes | FS-06 |
| Governing Documents | FS-06, ADR-012 (frozen — event idempotency), ARCH-001 §14.1 (frozen — endpoint/auth headers), IP-07 (delivered — `DetectionEvent`/`DetectionEventRepository`/schema v3) |
| Depends On | IP-07 fully delivered (done, committed) |
| Task ID Range | **T-94 – T-115** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Everything FS-06's header/§1 lists — snapshots, recordings, Angular dashboard, alert status transitions, sirens, production enablement (`WDA_DETECTION_SYNC_ENABLED` stays `false` in production at the end of this plan), any change to the DeepStream Bridge/RTSP output. |

---

## 1. Task Breakdown

### Backend (.NET)

| Task | Description |
|---|---|
| T-94 | `Alert` domain entity (`WeaponDetection.Domain/Alert.cs`) — fields per FS-06 §5.1, constructor invariants (confidence bounds, non-negative frame/bbox values), `AlertStatus` enum (`New`). |
| T-95 | EF Core configuration + migration: `Alerts` table, unique index on `(DeviceId, EventId)`. Run `dotnet ef migrations has-pending-model-changes` after. |
| T-96 | Extend `DeviceCredentialValidationResult`/`DeviceCredentialValidator` with additive `BranchId`/`DeviceRecordId` fields on a valid result (FS-06 §6.2). Existing `DeviceCredentialValidationController` behavior unchanged — add/adjust its unit tests only to confirm no regression, not new behavior. |
| T-97 | `Api.Contracts` DTOs: `SyncEventsRequest`, `DetectionEventDto`, `BoundingBoxDto`, `SyncEventsResponse`, `SyncEventResultDto`. Request-size bound (`[RequestSizeLimit]`/batch count cap) → `413`. |
| T-98 | `IAlertSyncService`/`AlertSyncService` (Application layer): per-event Camera/Branch resolution (FS-06 §6.3), savepoint-based idempotent insert (FS-06 §5.2), per-event outcome list. |
| T-99 | `SyncEventsController` (`POST /api/v1/sync/events`): header auth via extended validator, `401` on failure (never lock/reactivate), delegate to `AlertSyncService`, uniform envelope response. |
| T-100 | DI registration (`DependencyInjection.cs`) for `IAlertSyncService`. |
| T-101 | Backend unit tests — FS-06/task Phase 13 items 1–19 (Alert creation, status=New, timestamp preservation, header validation, camera/branch checks, confidence/bbox validation, duplicate/conflict outcomes, mixed-batch per-item results). |
| T-102 | Backend integration tests against a real SQL Server test database — Phase 13 items 20–22 (unique constraint enforcement, concurrent duplicate submission race, EF migration applies, no pending model changes). |

### Agent (Python)

| Task | Description |
|---|---|
| T-103 | SQLite schema v3→v4 migration: `DeliveredAtUtc` column, widened `DeliveryStatus` CHECK (FS-06 §7.2). Schema tests for the rebuild-table migration path. |
| T-104 | `DetectionEventRepository.list_pending`/`mark_delivered_many` (FS-06 §7.1) + repository tests (Phase 14 items 1–3, 13–14). |
| T-105 | `BackendSyncClient` (FS-06 §7.3): request construction, header attachment, envelope parsing, typed result, redacted logging. |
| T-106 | `DetectionEventSyncWorker` (FS-06 §7.4): drain loop, backoff/jitter, cancellation, identity-mismatch guard. |
| T-107 | `WDA_DETECTION_SYNC_*` settings (FS-06 §10) + validation tests. |
| T-108 | Lifecycle wiring: `default_sync_components_factory`, `main.py` component ordering (FS-06 §8). |
| T-109 | Agent unit tests — Phase 14 items 1–28 (repository ordering/limits, worker outcome handling, backoff behavior, shutdown, identity mismatch, header names, secret redaction, disabled-feature no-worker, cooldown/Bridge regression unaffected). |

### Verification and Sign-off

| Task | Description |
|---|---|
| T-110 | Backend: `dotnet build`/`dotnet test`/`dotnet list package --vulnerable --include-transitive`/EF pending-model-check. |
| T-111 | Agent: focused sync tests, full agent suite, `ruff check`, `ruff format --check`, `mypy`. |
| T-112 | Bridge regression suite (no cross-boundary change expected — proof only). |
| T-113 | Isolated end-to-end test against a staging Backend + real SQL Server test DB + temporary Agent SQLite (FS-06 task Phase 16, Tests 1–4: online delivery, outage, reconnection, duplicate retry). Measure latency (target <5s). |
| T-114 | Update FS-06/IP-08 status fields to reflect completion; document metadata-only snapshot limitation in the Jetson `deployment/jetson/agent.env.example` and README. |
| T-115 | Final report (Phase 17 of the task brief) — stop before any production enablement. |

## 2. Frozen Contract Reference

The exact request/response JSON shapes, `Alert` field list, camera-resolution rule, idempotency
mechanism, retry/backoff table, and settings list are specified in **FS-06 §4–§10** and are binding for
this plan — this document does not restate them; implementers must read FS-06 in full before starting
T-94/T-103.

## 3. Acceptance Criteria (summary — full list is the task brief's Phase 13/14/16)

- A valid batch creates exactly one `Alert` per new `EventId`, `Status=New`, original `DetectedAtUtc`
  preserved, `SnapshotReference` null.
- Retried/duplicate submissions never create a second `Alert`; conflicting immutable data on a retried
  `EventId` is rejected with a named conflict error, never silently accepted.
- Invalid camera, confidence, frame/bbox, or credentials are rejected without persisting.
- The Agent never marks an event delivered unless the response names that exact `EventId` as
  `accepted` or `duplicate`.
- A Backend/network outage leaves affected rows `pending` with no busy-retry loop; recovery drains
  automatically once the Backend is reachable again.
- `WDA_DETECTION_SYNC_ENABLED` remains `false` in production at the end of this plan.

## 4. Rollback

Setting `WDA_DETECTION_SYNC_ENABLED=false` (the shipped default) fully disables the worker with no
other change required — the Backend endpoint existing but unused is harmless (no Agent ever calls it).
No production Agent configuration is touched by this plan (Phase 17).
