# Feature Specification: Branch-Level Daily Alert Quota

| Field | Value |
|-------|-------|
| Feature ID | FS-09 |
| Title | Branch-Level Daily Alert Quota — Backend-authoritative, concurrency-safe cap of 15 full Alerts per Branch per branch-local calendar day |
| Status | Draft — implementation in progress. |
| Related SRS Requirements | FR-DET-013 (new — see §12 amendment), BR-009 (new — see §12 amendment) |
| Related Architecture Sections | §13.4 (Event Idempotency — `(DeviceId, EventId)` unique constraint, unchanged, remains authoritative and checked first), §14.1 API table (`POST /api/v1/sync/events` — extended response only, endpoint/auth unchanged) |
| Related ADRs | ADR-012 (Event Idempotency — binding, unchanged; this feature is layered strictly after it, never before) |
| Owner | Farhan Naeem |
| Dependencies | FS-06/IP-08 (Detection Event Backend Synchronization — delivered): `Alert` entity, `SyncEventsController`, `AlertSyncService`, `(DeviceId, EventId)` idempotency; `DetectionEventRepository`/`DeliveryStatus` state machine. FS-08/IP-10 (Snapshot Evidence Capture — delivered): `SnapshotOutbox` schema, `SnapshotUploadWorker`, `alert_id_sink` correlation. |
| Explicitly excluded | Model, tracker, inference interval, confidence threshold, NMS, RTSP output, Data Protection, Device credential validation/activation. Angular dashboard quota UI. Any change to `(DeviceId, EventId)` idempotency semantics — it remains checked first and is unaffected by quota state. Per-Device or per-Camera limits (the limit is strictly per-Branch). |

---

## 1. Purpose and Scope

FS-06/IP-08 gives every accepted, non-duplicate `DetectionEvent` an unconditional `Alert`. Under load —
multiple Devices/Cameras submitting concurrently for the same Branch — this can flood the Backend and
downstream operators. This feature adds a Backend-enforced ceiling:

```text
Jetson DetectionEvent pending
    ↓
POST /api/v1/sync/events
    ↓
Backend authenticates Device (unchanged, FS-06 §6.1)
    ↓
resolve Device → Branch (unchanged, FS-06 §6.2)
    ↓
(DeviceId, EventId) idempotency check (unchanged, FS-06 §5.2 — checked FIRST)
    ↓ no existing Alert
atomic daily Branch quota check (NEW)
    ├── AcceptedAlertCount < 15
    │       ↓
    │   create Alert, increment counter
    │       ↓
    │   outcome = accepted
    │
    └── AcceptedAlertCount >= 15
            ↓
        do not create Alert
            ↓
        outcome = quota_exceeded, errorCode BRANCH_DAILY_ALERT_QUOTA_REACHED
            ↓
        Agent marks event DeliveryStatus = suppressed_by_quota (terminal, never retried)
```

Local detection, DeepStream processing, and event persistence to the Agent's SQLite outbox are
completely unaffected — the quota is enforced only at the Backend sync boundary, after the event has
already been safely captured locally.

### 1.1 What This Feature Adds

| Capability | Basis |
|---|---|
| `BranchDailyAlertQuota` table (one row per `(BranchId, LocalDate)`), atomically incremented in the same transaction/savepoint as the `Alert` insert. | Task's concurrency-safety requirement — explicit counter table, not check-then-insert. |
| `SuppressedDetectionEvent` table, unique `(DeviceId, EventId)`, so a retried quota-suppressed EventId is itself idempotent and never double-counts suppression statistics. | Task Phase 5. |
| Nullable `Branch.TimeZoneId` (IANA identifier), used to resolve the branch-local quota day; UTC is the documented fallback when unset. | Task Phase 2 — "do not silently assume the Backend server timezone." |
| New sync outcome `quota_exceeded` (`SyncEventOutcomeKind.QuotaExceeded`), carrying `errorCode=BRANCH_DAILY_ALERT_QUOTA_REACHED` and a `quota: { maximum, localDate }` object. HTTP status remains `200` for the batch. | Task Phase 3. |
| `AlertQuota__Enabled` / `AlertQuota__MaximumPerBranchPerDay` configuration, `IValidateOptions`-validated at startup (integer, `>= 1`, bounded ceiling, no silent clamping). | Task Phase 2, mirrors `AlertSnapshotStorageOptions`/`JwtOptionsValidator`. |
| Agent `DetectionEvent.DeliveryStatus` terminal state `suppressed_by_quota` (SQLite schema v5→v6), excluded from `list_pending`, never resent. | Task Phase 6. |
| Agent snapshot suppression: no `associate_alert_id` call for a quota-suppressed event; a new `SnapshotOutbox` terminal state (`suppressed_by_quota`) prevents upload and allows safe local JPEG cleanup, including the race where the ack arrives before capture completes. | Task Phase 7. |
| Rate-limited structured logs: `branch_daily_quota_reached`, `event_suppressed_by_quota`, `daily_quota_summary` — safe fields only, no credentials. | Task Phase 10. |

### 1.2 What This Feature Does Not Touch

- No change to `(DeviceId, EventId)` idempotency (FS-06 §5.2) — it is checked strictly before any quota
  logic runs; a duplicate retry never consumes quota, whether or not the original consumed it.
- No change to Camera resolution (FS-06 §6.3), device authentication (FS-06 §6.1), or the
  `SyncEventsController` request/auth contract.
- No change to snapshot capture, JPEG encoding, or the upload protocol (FS-08) for events that *are*
  accepted — only quota-suppressed events are affected, and only to prevent upload/retention.
- No change to historical/archived `DetectionEvent` rows (the `DetectionEvent_archive_*` table from
  FS-06) — quota enforcement only applies to newly submitted events going forward.
- No change to the model, tracker, inference interval, confidence threshold, NMS, or RTSP output.

## 2. Actors

| Actor | Description |
|---|---|
| `AlertSyncService` (extended, Backend) | Adds branch-local day resolution and atomic quota enforcement, strictly after existing idempotency resolution, inside the same per-batch transaction. |
| `BranchDailyAlertQuota` (new, Backend Domain entity) | The per-Branch/day atomic counter row — accepted count, suppressed counts (total and per-class), first/last suppression timestamps. |
| `SuppressedDetectionEvent` (new, Backend Domain entity) | Idempotency record so a retried quota-suppressed `EventId` does not increment suppression statistics twice. |
| `DetectionEventSyncWorker` (extended, Agent) | Marks `quota_exceeded` EventIds `suppressed_by_quota` instead of `delivered`; never resends them. |
| `SnapshotOutboxRepository` (extended, Agent) | Gains a suppression/cancellation path so a quota-suppressed event's snapshot row (if any) never becomes upload-ready and its local JPEG is cleaned up once the terminal DB state is committed. |

## 3. Preconditions

- FS-06/IP-08 is deployed: `SyncEventsController`/`AlertSyncService`/`Alert` table exist and
  `(DeviceId, EventId)` idempotency is enforced.
- FS-08/IP-10 is deployed: `SnapshotOutbox` schema and upload worker exist (quota suppression must
  interoperate with them even where snapshot capture is disabled).
- The Backend has `AlertQuota__Enabled=true` and a validated `AlertQuota__MaximumPerBranchPerDay`.

## 4. Quota Semantics

- **Scope**: per `BranchId`, across every `Device` and every `Camera` belonging to that Branch. Never
  per-Device, never per-Camera.
- **Limit**: `AcceptedAlertCount < MaximumPerBranchPerDay` (default 15) → event 1–15 accepted; event 16+
  → `quota_exceeded`.
- **Day boundary**: `[branch-local date 00:00:00, next branch-local date 00:00:00)`, resolved from
  `Branch.TimeZoneId` via `TimeZoneInfo.FindSystemTimeZoneById` (a proper IANA/Windows timezone
  identifier — never a fixed UTC offset, so DST transitions are handled correctly by the .NET timezone
  database). The event's `DetectedAtUtc` (the original Jetson-generated detection timestamp, consistent
  with FS-06 §4.3's provenance rule — never `ReceivedAtUtc`/upload time) is converted to branch-local
  time to determine which day's counter row applies. Both boundaries are converted back to UTC before
  being used in any SQL comparison.
- **Branch timezone design**: `Branch.TimeZoneId` is a new nullable `string` column. When null (the
  default for every existing Branch until an Admin sets one), the quota day is resolved in **UTC**, and
  this fallback is explicit — never silently treated as the Backend server's local timezone. This is
  the smallest schema change that lets Branches which need branch-local semantics opt in without
  forcing a value on every Branch immediately. No Admin UI for setting it is added by this feature
  (`Branch.UpdateTimeZone` is a domain method; if no admin surface calls it, every Branch keeps UTC
  quota days) — this is documented as a Known Limitation (§14).
- **Default limit**: 15, configurable via `AlertQuota__MaximumPerBranchPerDay`.

## 5. API Contract Extension

Extends FS-06 §4.2's response envelope. `SyncEventResultDto` gains an optional `errorCode` value and a
new optional `quota` object, populated only when `outcome == "quota_exceeded"`:

```json
{
  "results": [
    {
      "eventId": "3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f",
      "outcome": "quota_exceeded",
      "alertId": null,
      "errorCode": "BRANCH_DAILY_ALERT_QUOTA_REACHED",
      "quota": { "maximum": 15, "localDate": "2026-07-29" }
    }
  ]
}
```

- `quota.maximum` — the configured `AlertQuota__MaximumPerBranchPerDay` value at time of evaluation.
- `quota.localDate` — the resolved branch-local (or UTC-fallback) calendar date, `yyyy-MM-dd`.
- No internal database identifiers (quota row id, suppression counts) are ever exposed on the wire.
- HTTP status remains `200` for any structurally valid batch, exactly as FS-06 §4.2 — `quota_exceeded`
  is a per-item outcome, never a `429` for an individual event in a mixed batch.

## 6. Backend Persistence Design

### 6.1 `BranchDailyAlertQuota` (new, `WeaponDetection.Domain`)

| Field | Type | Notes |
|---|---|---|
| `BranchId` | `Guid` | Part of the unique key. |
| `LocalDate` | `string` (`yyyy-MM-dd`) | Part of the unique key — the resolved quota day (§4). |
| `AcceptedAlertCount` | `int` | Atomically incremented on each accepted Alert for this Branch/day. |
| `SuppressedDetectionCount` | `int` | Atomically incremented on each quota-suppressed detection. |
| `GunSuppressedCount` | `int` | Class-specific suppression count. |
| `KnifeSuppressedCount` | `int` | Class-specific suppression count. |
| `FirstSuppressedAtUtc` | `DateTime?` | Set once, on the first suppression for this Branch/day. |
| `LastSuppressedAtUtc` | `DateTime?` | Updated on every suppression for this Branch/day. |

Unique index `(BranchId, LocalDate)` is the primary key — this is the atomicity/idempotency authority
for the counter, mirroring `Alert`'s `(DeviceId, EventId)` unique-index-as-authority design (FS-06 §5.2).
No foreign key to `Branch` (mirrors `Alert`'s "no FK, index only" style — Alert rows are deliberately
independent of Branch/Device/Camera mutation).

### 6.2 `SuppressedDetectionEvent` (new, `WeaponDetection.Domain`)

| Field | Type | Notes |
|---|---|---|
| `DeviceId` | `Guid` | Authenticated device's external id. |
| `EventId` | `Guid` | Jetson-generated. |
| `BranchId` | `Guid` | The Branch the quota was evaluated against. |
| `LocalDate` | `string` | The resolved quota day at time of suppression. |
| `ClassName` | `string` | For observability only. |
| `DetectedAtUtc` | `DateTime` | Preserved from the request. |
| `Reason` | `string` | `"BRANCH_DAILY_ALERT_QUOTA_REACHED"` (extensible if future suppression reasons are added). |
| `CreatedAtUtc` | `DateTime` | Server time at suppression. |

Unique index `(DeviceId, EventId)` — the idempotency authority for suppression retries (Phase 5). No
bounding-box or snapshot evidence is stored here (task requirement — minimal record only).

### 6.3 Migration

One additive EF Core migration: `CreateTable` for both entities (mirroring `AddAlertSchema.cs`'s
style — `uniqueidentifier`/`nvarchar`/`int`/`datetime2` mappings, `PrimaryKey`, unique `CreateIndex`),
plus a nullable `nvarchar` `TimeZoneId` column added to `Branches` (mirroring
`AddAlertSnapshotFields.cs`'s purely-additive style). Symmetric `Down()`.

## 7. Concurrency Design

`AlertSyncService`'s existing per-batch transaction and per-event savepoint (FS-06 §5.2) are extended,
not replaced. Per event, in order:

1. **Idempotency first (unchanged)**: explicit lookup of an existing `Alert` by `(DeviceId, EventId)`.
   If found, resolve `duplicate`/`rejected(EVENT_DATA_CONFLICT)` exactly as FS-06 §5.2 — **no quota
   interaction at all**, whether or not the original submission consumed quota.
2. If no `Alert` exists, resolve the branch-local `LocalDate` (§4).
3. **Suppressed-retry idempotency**: look up `SuppressedDetectionEvent` by `(DeviceId, EventId)`. If
   found, return `quota_exceeded` again — **no counter mutation** (a retried quota-suppressed event
   must not increment `SuppressedDetectionCount` a second time).
4. Otherwise, inside a savepoint:
   a. Ensure a `BranchDailyAlertQuota` row exists for `(BranchId, LocalDate)` — insert-if-absent,
      catching the unique-index violation exactly as `Alert`'s duplicate handling does, so concurrent
      first-events-of-the-day for the same Branch race safely at the DB level.
   b. Execute one atomic conditional update:
      `UPDATE BranchDailyAlertQuota SET AcceptedAlertCount = AcceptedAlertCount + 1
       WHERE BranchId = @b AND LocalDate = @d AND AcceptedAlertCount < @max`
      and inspect the affected-row count. SQL Server row-locks the matched row for the duration of the
      predicate-evaluate-then-write, so concurrent requests against the same `(BranchId, LocalDate)` row
      serialize on it — this single statement is the entire concurrency-safety mechanism; no
      `SERIALIZABLE` isolation, application-level lock, or `SELECT COUNT` followed by a separate
      `INSERT` is used.
      - **1 row affected** → quota consumed → insert the `Alert` in the same savepoint. If that insert
        then fails on the `(DeviceId, EventId)` unique index (a race between two concurrent submissions
        of the same event, both having passed step 1's lookup before either committed), roll back to the
        savepoint — undoing both the `Alert` insert attempt and the quota increment together — and fall
        back to step 1's duplicate/conflict resolution. This satisfies "a failed Alert insert must not
        consume quota."
      - **0 rows affected** → quota exhausted for this Branch/day → insert a `SuppressedDetectionEvent`
        row (unique-index guarded, same race-and-rollback handling) and atomically increment
        `SuppressedDetectionCount`, the class-specific counter, and `First`/`LastSuppressedAtUtc` on the
        quota row, all in the same savepoint. Return `quota_exceeded`.
5. Commit once per batch, as today — one event's outcome never rolls back another valid event in the
   same batch.

## 8. Idempotency Ordering Summary

| Step | Check | On match |
|---|---|---|
| 1 | `(DeviceId, EventId)` existing `Alert` | `duplicate` or `rejected(EVENT_DATA_CONFLICT)` — quota untouched |
| 2 | `(DeviceId, EventId)` existing `SuppressedDetectionEvent` | `quota_exceeded` again — quota counters untouched |
| 3 | Atomic `BranchDailyAlertQuota` conditional increment | `accepted` (Alert created) or `quota_exceeded` (suppression recorded) |

This ordering is why `(DeviceId, EventId)` uniqueness remains the sole authority for event identity —
the quota layer only ever runs for events that are neither an existing Alert nor an existing suppressed
record.

## 9. Agent Delivery Status

`DetectionEvent.DeliveryStatus` gains a third terminal value, `suppressed_by_quota`, alongside the
existing `pending`/`delivered` (FS-06 §7.1/§7.2). SQLite schema v5→v6 widens the `DeliveryStatus` CHECK
constraint using the same rebuild-table migration technique already used for the v3→v4 widening.
`DetectionEventRepository` gains `mark_suppressed_by_quota_many(event_ids, finalized_at_utc) -> int`,
structurally identical to `mark_delivered_many` (single transaction, `WHERE EventId IN (...) AND
DeliveryStatus = 'pending'` guard — one-way transition, idempotent, never touches an already-terminal
row). `list_pending`'s existing `WHERE DeliveryStatus = 'pending'` filter requires no change — the new
status is not `'pending'` by construction. The original event row and all its metadata are preserved —
this feature never deletes a `DetectionEvent` row. `DetectionEventSyncWorker` calls
`mark_suppressed_by_quota_many` for every EventId whose outcome is `quota_exceeded`, exactly parallel to
its existing `mark_delivered_many` call for `accepted`/`duplicate` — a `quota_exceeded` outcome is never
mapped to `delivered`, and the worker never resends it (excluded from every future `list_pending` call).

## 10. Snapshot Interaction

For any event whose sync outcome is `quota_exceeded`:

- No `alertId` exists on the wire (§5) — the sync worker never calls `alert_id_sink`/
  `SnapshotOutboxRepository.associate_alert_id` for that `EventId`. `SnapshotOutboxRepository.list_upload_ready`
  already requires `BackendAlertId IS NOT NULL` (FS-08), so an unassociated row is structurally
  never upload-ready — no snapshot is uploaded.
- `SnapshotOutbox` gains a new terminal `UploadStatus` value (`suppressed_by_quota`) and a repository
  method `cancel_for_quota(event_id) -> bool`, structurally parallel to the existing
  `mark_capture_failed`. The sync worker calls it for every quota-suppressed `EventId` that has a
  `SnapshotOutbox` row (capture may have already completed by the time the ack arrives — this is the
  race the task calls out explicitly). This is treated as a policy outcome, never a capture failure.
- The local JPEG is removed only after the `DetectionEvent`'s terminal `suppressed_by_quota` state (and,
  where applicable, the `SnapshotOutbox` row's `suppressed_by_quota` state) is committed to SQLite —
  never before, and never leaving a dangling pending upload retry.
- If capture has not yet started when the ack arrives, `cancel_for_quota` still marks the (not-yet-
  existent or in-flight) outbox row so that when/if capture later completes, the existing
  capture-completion path sees a row that is already terminal and does not schedule an upload.

## 11. Configuration

`Section__Key` convention (mirrors `AlertSnapshots__StoragePath`):

| Field | Env var | Type / default |
|---|---|---|
| `Enabled` | `AlertQuota__Enabled` | `bool`, default `true` |
| `MaximumPerBranchPerDay` | `AlertQuota__MaximumPerBranchPerDay` | `int`, default `15`, validated `>= 1` and `<= 10000` (documented safe ceiling) |

Validation is shape-only (`IValidateOptions<AlertQuotaOptions>`, `ValidateOnStart()`) — invalid
configuration (non-integer, `< 1`, `> 10000`) stops Backend startup with a clear message; no clamping.
When `Enabled=false`, `AlertSyncService` skips quota enforcement entirely and every event is accepted
subject only to existing idempotency — this is the feature's kill-switch (§15/Rollback in IP-11).

## 12. SRS Amendment

Adds, following the repository's existing amendment convention (see FR-BRN-005/FR-BRN-008):

- **FR-DET-013** *(Added — Branch daily Alert quota; reason: prevent DetectionEvent synchronisation
  from flooding the central Backend under multi-device/multi-camera load. See FS-09.)*: The system
  shall enforce a configurable, Backend-authoritative maximum number of full Alerts created per Branch
  per branch-local calendar day (default 15); detections beyond the limit shall not create an Alert and
  shall be reported to the originating Jetson Agent as a distinct, non-error, terminal outcome.
- **BR-009** *(Added — see FS-09.)*: The Branch daily Alert quota applies across every Device and every
  Camera belonging to a Branch; it is never a per-Device or per-Camera limit.

## 13. Logging

No rate-limiting logging helper exists elsewhere in the Backend today (`ILogger` usage is otherwise
limited to `AdminBootstrapper`'s one-time startup messages) — this feature introduces `ILogger<AlertSyncService>`,
the first per-request-path logger in the sync flow, with three templates, all safe-fields-only
(`BranchId`, local date, maximum, accepted count, suppressed count, per-class counts — never
credentials, bounding boxes, or full request/response bodies):

- `branch_daily_quota_reached` — logged once, at the transition where a Branch/day's counter first hits
  the configured maximum (derived from the atomic update's affected-row-count going from 1 to 0, not a
  separate polling check).
- `event_suppressed_by_quota` — logged for a bounded, rate-limited subset of suppressions per Branch/day
  (not unbounded per-event logging, which could otherwise scale with detection volume rather than the
  15-Alert cap).
- `daily_quota_summary` — a periodic/end-of-day summary derived directly from the `BranchDailyAlertQuota`
  row's counts.

## 14. Known Limitations Carried Into the Implementation Plan

- `Branch.TimeZoneId` has no Admin-facing UI in this increment — every Branch defaults to UTC quota-day
  boundaries until a future feature exposes the field for editing. This is the documented "smallest
  approved timezone design" per the task's own instruction, not an oversight.
- The quota day is resolved from `DetectedAtUtc`, not the time the Backend receives the batch — a
  detection near a branch-local day boundary that arrives late (e.g. after an extended outage) is
  counted against the day it was actually detected, which can occasionally mean a backlog of old events
  is evaluated against an already-closed quota day. This is intentional (consistent with FS-06 §4.3's
  "preserve original timestamp" provenance rule) and documented as a known, accepted behavior.
- No dead-letter/replay mechanism for `quota_exceeded` — it is a deliberate terminal state, not a
  retryable failure, consistent with FS-06's existing no-dead-letter-queue precedent for `rejected`.

## 15. Open Items

| ID | Issue | Resolution proposed |
|----|-------|----------------------|
| OI-13 | `Branch.TimeZoneId` has no Admin UI yet — every Branch quota-days in UTC until a future feature adds it. | Documented as a known limitation (§14); a future Branch-management feature can add the Admin-facing field without further schema change (the column already exists, nullable). |
| OI-14 | The atomic conditional `UPDATE ... WHERE AcceptedAlertCount < @max` pattern's exact EF Core invocation (raw SQL vs. `ExecuteUpdateAsync`) needs a live SQL Server confirmation of affected-row-count semantics under concurrent load, not assumed from documentation alone. | IP-11's real-SQL-Server concurrency test (Phase 8 item 22 of the task brief) exercises this directly. |
