# Feature Specification: Detection Event Backend Synchronization

| Field | Value |
|-------|-------|
| Feature ID | FS-06 |
| Title | Detection Event Backend Synchronization — reliable, idempotent outbox delivery of Jetson `DetectionEvent` rows to the ASP.NET Core Backend and SQL Server `Alert` table |
| Status | Complete — deployed and enabled in production. One real production detection traced end-to-end (EventId `9297b455…`, ~2.5s latency, `DetectedAtUtc` preserved, `Status=New`, `SnapshotReference=null`). 253 Alerts created over a 15-minute soak, zero duplicates, zero rejections, zero restarts. Historical backlog (4,785 rows) archived and confirmed never sent. |
| Related SRS Requirements | FR-SYN-002–004 (store-while-disconnected, sync-on-reconnect, server preserves original timestamp), NFR-REL-002 (no detection event lost during an outage) |
| Related Architecture Sections | §13.4 (Event Idempotency — `(DeviceId, EventId)` unique constraint), §14.1 API table (`POST /api/v1/sync/events`, Agent, `X-Device-Id` + `X-Device-Secret` — frozen, not re-decided here), §10.2 (Backend Sync Client component), §23 (Error Handling — retry/backoff detail is FS-level, not architectural) |
| Related ADRs | ADR-012 (Event Idempotency — binding, not re-decided here); ADR-002 amended (device auth headers, binding) |
| Owner | Farhan Naeem |
| Dependencies | IP-07 (Detection Event Bridge — delivered, T-80–T-93): `DetectionEvent` domain model, `DetectionEventRepository`, SQLite schema v3 `DetectionEvent` table with `DeliveryStatus` and its `(DeliveryStatus, CreatedAtUtc)` index built specifically anticipating this feature; `DeviceIdentityRepository`; `OperationalComponent`/`OperationalStateCoordinator`/`AgentRuntimeSupervisor`; the `CredentialValidationClient`/`BackendActivationClient` HTTP-client patterns. Backend: `DeviceCredentialValidator`, `Device`/`Camera` domain entities, `ApiResponse`/`ApiEnvelopeResultFilter` envelope. |
| Fulfills | The work IP-07 §8 explicitly deferred: "Reliable outbox delivery of persisted `DetectionEvent` rows to the ASP.NET Core backend, then SQL Server... `DeliveryStatus`/`EventId` are shaped in T-85 specifically so that feature can add delivery-attempt tracking without touching this plan's schema migration." |
| Explicitly excluded | Snapshot/recording capture or upload (FR-DET-004/FR-DET-007 snapshot acceptance is **not** claimed by this feature); `POST /api/v1/alerts/{id}/snapshot`; Angular alert dashboard views; operator alert-status transitions (acknowledge/dismiss); siren/notification commands; object tracking; any change to activation/reactivation/credential-validation behavior beyond the additive `Device` resolution described in §6.2; any change to the DeepStream Bridge, RTSP output, or `deepstream-rtsp-route.service`. |

---

## 1. Purpose and Scope

IP-07 gives the Agent a durable local outbox: every accepted detection is validated, deduplicated,
and persisted to SQLite as a `DetectionEvent` row with `DeliveryStatus='pending'`. Nothing today reads
that outbox or tells the Backend it exists. This feature closes that gap — and only that gap:

```text
SQLite DetectionEvent (DeliveryStatus='pending')
    ↓ oldest-first, bounded batch
Agent: DetectionEventSyncWorker (new)
    ↓ POST /api/v1/sync/events, X-Device-Id + X-Device-Secret
Backend: SyncEventsController (new)
    ↓ device authentication (existing validator, extended to resolve Device)
Backend: AlertSyncService (new)
    ↓ per-event: validate CameraId belongs to the authenticated Device's Branch
    ↓ idempotent insert keyed on (DeviceId, EventId) — DB unique constraint is final authority
SQL Server: Alert table (new), Status='New'
    ↓ per-event outcome: accepted / duplicate / rejected
Agent: mark only accepted/duplicate EventIds delivered (DeliveredAtUtc set)
```

Snapshot capture and `SnapshotReference` upload are **not** implemented by the current Bridge/Agent
lifecycle (IP-07 explicitly excluded them). This feature carries a nullable `SnapshotReference` on the
`Alert` row for forward compatibility only — it is never populated in this increment, and no snapshot
path is fabricated or uploaded. Metadata synchronization is complete in this increment; snapshot
synchronization remains a separate, future feature. Full FR-DET-004/FR-DET-007 acceptance (which
requires a snapshot) is **not** claimed here.

### 1.1 What This Feature Adds

| Capability | Basis |
|---|---|
| Backend `Alert` domain entity + EF Core migration, unique `(DeviceId, EventId)` index. | ADR-012/§13.4 (frozen). |
| `SyncEventsController` (`POST /api/v1/sync/events`), device-authenticated via the existing header contract, delegating to `AlertSyncService`. | ARCH-001 §14.1 (frozen endpoint/auth). |
| `AlertSyncService` (Application layer): per-event Branch-scoped Camera resolution, idempotent insert with DB-constraint-as-authority conflict handling, per-event outcome reporting. | Task's idempotency/partial-success semantics (§4/§5 below). |
| `DetectionEventRepository` extensions: `list_pending(limit)`, `mark_delivered_many(event_ids, delivered_at_utc)`. | IP-07's `(DeliveryStatus, CreatedAtUtc)` index was built anticipating exactly this query. |
| SQLite schema v3→v4 migration: `DetectionEvent.DeliveredAtUtc TEXT NULL`, widened `DeliveryStatus` CHECK to `('pending','delivered')`. | IP-07 §7 documented this as the next feature's job. |
| `BackendSyncClient` (Agent): POST batch, parse envelope, typed per-event result. | Mirrors `CredentialValidationClient`'s device-header/envelope pattern. |
| `DetectionEventSyncWorker` (Agent `OperationalComponent`): drain loop, backoff, lifecycle-wired after `DetectionIngestHandler`/DeepStream Bridge. | Task's worker/lifecycle requirement; reuses the existing `OperationalComponent` protocol unchanged. |
| `WDA_DETECTION_SYNC_*` settings, default `WDA_DETECTION_SYNC_ENABLED=false`. | Same two-layer kill-switch discipline as `WDA_DETECTION_EVENTS_ENABLED` (IP-07 §9). |

### 1.2 What This Feature Does Not Touch

- No change to `DetectionIngestHandler`, the DeepStream Bridge, cooldown suppression, or class/camera
  validation on the ingest path — this feature only reads already-persisted rows.
- No change to `DeviceCredentialValidator`'s constant-time comparison, revocation semantics, or the
  `/api/v1/device/credentials/validate` endpoint's behavior or response shape. §6.2 adds a new,
  additive, opt-in field to its result type; existing callers are unaffected.
- No new Backend authentication mechanism — the existing `X-Device-Id`/`X-Device-Secret` validator is
  reused, not replaced.
- No change to `deepstream-rtsp-route.service`, the multicast route fix, tracker/NMS/inference
  settings, or RTSP output.

## 2. Actors

| Actor | Description |
|---|---|
| `DetectionEventSyncWorker` (new) | An `OperationalComponent` running one coordinated `asyncio` task in the existing FastAPI/Uvicorn process. Drains `DetectionEventRepository.list_pending`, sends bounded batches via `BackendSyncClient`, marks only Backend-acknowledged EventIds delivered. |
| `BackendSyncClient` (new) | A dedicated HTTP client abstraction (mirrors `CredentialValidationClient`) that constructs the request, attaches device-auth headers, parses the response envelope, and returns a typed per-event result. Never touches SQLite directly. |
| `SyncEventsController` (new, Backend) | Thin API controller: reads `X-Device-Id`/`X-Device-Secret`, authenticates via the existing validator, binds the batch DTO, delegates to `AlertSyncService`, returns the uniform envelope. No transaction logic. |
| `AlertSyncService` (new, Backend Application layer) | Owns the per-event Camera/Branch validation, the idempotent-insert-with-conflict-resolution logic, and the transaction boundary for a batch. |
| `Alert` (new, Backend Domain entity) | The SQL Server row created per accepted event. |

## 3. Preconditions

- IP-07 is deployed and detection events are being persisted to SQLite with `DeliveryStatus='pending'`.
- The Agent has a loaded `DeviceIdentity` (`Operational` state) — the worker never sends before this.
- The Backend has at least one `Device` (Activated) and at least one `Camera` on that device's Branch
  whose `Name` matches the Agent's configured `WDA_DETECTION_CAMERA_ID` (§6.3 — this is the mapping
  this feature defines between the Agent's local camera identifier and the Backend's `Camera` row).

## 4. API Contract (frozen endpoint, this feature's payload design)

`POST /api/v1/sync/events`, headers `X-Device-Id` + `X-Device-Secret` (ARCH-001 §14.1 — not re-decided
here). No Activation Key, no Admin JWT, no credentials in the query string or body.

### 4.1 Request

```json
{
  "events": [
    {
      "eventId": "3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f",
      "cameraId": "camera1",
      "detectedAtUtc": "2026-07-24T18:30:00.000Z",
      "createdAtUtc": "2026-07-24T18:30:00.150Z",
      "classId": 0,
      "className": "gun",
      "confidence": 0.91,
      "sourceId": 0,
      "frameNumber": 12345,
      "frameWidth": 1280,
      "frameHeight": 720,
      "boundingBox": { "left": 420.0, "top": 180.0, "width": 250.0, "height": 190.0 }
    }
  ]
}
```

`cameraId` is the Agent's local camera identifier (`WDA_DETECTION_CAMERA_ID`, a short operator-chosen
string — today only `"camera1"`), **not** the Backend's `Camera.CameraId` GUID. §6.3 defines how the
Backend resolves it.

### 4.2 Response (uniform envelope, `data` shown)

```json
{
  "results": [
    { "eventId": "3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f", "outcome": "accepted", "alertId": "…" }
  ]
}
```

Outcomes: `accepted` (new `Alert` inserted), `duplicate` (safe idempotent replay — `(DeviceId,
EventId)` already exists with equivalent data — **not** an HTTP 409), `rejected` (not persisted;
carries `errorCode`; Agent leaves the row pending).

Whole-request status: `200` for any processed batch (including all-rejected); `400` structurally
malformed; `401` invalid device credentials; `413` batch/body exceeds the bounded size; `500`
unexpected failure only. The Agent marks an EventId delivered **only** if the response names that
exact EventId with outcome `accepted` or `duplicate` — missing/malformed/duplicated/unknown response
EventIds leave the corresponding rows pending.

### 4.3 Field Provenance and Validation

- `DeviceId` — from the authenticated header, never the body. Before sending, the Agent verifies each
  SQLite row's `DeviceId` matches its currently loaded `DeviceIdentity` (defense against a stale row
  surviving a device identity change).
- `EventId` — Jetson-generated, unchanged end to end.
- `detectedAtUtc` — the original detection timestamp; never overwritten by upload time (FR-SYN-004).
- `className` — the Agent-resolved label (IP-07 §5), not re-trusted from any Bridge wire payload at
  this layer (it is already Agent-resolved data read back from `DetectionEvent`).
- `confidence` — `0.0 ≤ value ≤ 1.0`.
- Bounding box / frame dimensions — bounds-validated (finite, non-negative, box within frame).
- `SnapshotReference` — never supplied by the Agent in this increment; absent from the request DTO
  entirely (not merely null), since the Agent has no snapshot to reference (§1).
- `DeliveryStatus` (`pending`/`delivered`) is local Agent state and is never sent; the Backend `Alert`
  business status always starts as `New` regardless of local delivery state (§5.3).

## 5. Backend Persistence Design

### 5.1 `Alert` Entity (new, `WeaponDetection.Domain`)

Mirrors the existing `Device`/`Camera` entity style (private setters, a constructor enforcing
invariants, no public mutation beyond what a defined transition allows):

| Field | Type | Notes |
|---|---|---|
| `AlertId` | `Guid` | Server-generated PK. |
| `DeviceId` | `Guid` | The authenticated device's external `DeviceId` (not `DeviceRecordId`). |
| `EventId` | `Guid` | Jetson-generated, unique together with `DeviceId`. |
| `CameraId` | `Guid` | Resolved `Camera.CameraId` (§6.3), not the wire's local `cameraId` string. |
| `DetectedAtUtc` | `DateTime` | Preserved from the request — never `ReceivedAtUtc`. |
| `ReceivedAtUtc` | `DateTime` | Server `UtcNow` at persistence. |
| `ClassId` | `int` | |
| `ClassName` | `string` | |
| `Confidence` | `double` | `0.0–1.0`, enforced at construction. |
| `FrameNumber`, `FrameWidth`, `FrameHeight` | `long`/`int`/`int` | |
| `BboxLeft`, `BboxTop`, `BboxWidth`, `BboxHeight` | `double` | |
| `SnapshotReference` | `string?` | Always `null` this increment. |
| `Status` | `AlertStatus` enum, `New` only defined value used | Never derived from local `DeliveryStatus`. |

Unique index on `(DeviceId, EventId)` — the migration's concurrency authority (§5.2).

### 5.2 Idempotent Insert (concurrency-safe, not check-then-insert)

`AlertSyncService` opens **one transaction per batch**. For each event, after Camera/Branch validation
passes, it creates a **savepoint**, attempts the `Alert` insert, and `SaveChangesAsync`s. Two outcomes:

- **Success** → `accepted`, savepoint released, continue to the next event in the same transaction.
- **`DbUpdateException` from the unique index** → roll back to the savepoint (undoing only this
  event's attempted write, not the whole batch), re-query the existing `Alert` by `(DeviceId,
  EventId)`:
  - If its immutable fields (`CameraId`, `DetectedAtUtc`, `ClassId`, `ClassName`, `Confidence`,
    `FrameNumber`, frame dimensions, bounding box) match the retried request → `duplicate`.
  - If they differ → `rejected` with a distinct `errorCode` (`EVENT_DATA_CONFLICT`) — never silently
    accepted as equivalent (task requirement: fail loudly, not silently).

This makes the database's unique constraint the sole authority for concurrent-retry safety (two
simultaneous retries of the same event race at the DB level, not in application logic), satisfying
"concurrent duplicate submissions create exactly one Alert" without an application-level lock.

The transaction commits once after all events in the batch are processed (accepted/duplicate/rejected
rows all included) — one invalid event never rolls back independently valid events in the same batch.

### 5.3 `Status`

Every newly inserted `Alert.Status` is `New`. The Agent's local `pending`/`delivered`
`DeliveryStatus` is never read by, or mapped into, this field — they are different state machines for
different actors (Agent outbox delivery vs. Backend operator workflow).

## 6. Device Authentication and Camera Resolution

### 6.1 Authentication (reuses the existing, frozen mechanism)

`SyncEventsController` reads `X-Device-Id`/`X-Device-Secret` exactly as `DeviceCredentialValidationController`
does (malformed `DeviceId` → `Guid.Empty`, treated uniformly), and calls the existing
`IDeviceCredentialValidator`. Failure at this stage always returns the existing uniform
`401 INVALID_DEVICE_CREDENTIALS` — the same response shape the credential-validation endpoint already
returns, so the Agent cannot distinguish a sync-endpoint auth failure from any other. Per the task's
binding security rule: a `401` from this endpoint **never** locks or reactivates the Agent — only the
dedicated `/api/v1/device/credentials/validate` endpoint is authoritative for confirmed revocation. The
sync worker's response to a `401` is exactly the same as a `500`/timeout: leave events pending, back
off (§8).

### 6.2 Resolving the Authenticated `Device` (additive extension)

`IDeviceCredentialValidator.ValidateAsync` today returns only a valid/invalid verdict —
`DeviceCredentialValidationResult` carries no resolved entity. `AlertSyncService` needs the
authenticated device's `BranchId` to scope Camera resolution (§6.3). Rather than introduce a second,
parallel lookup (which could theoretically resolve a different device than the one just authenticated,
if a row changed between two queries), `DeviceCredentialValidationResult.Valid()` is extended with two
additive, optional fields — `BranchId` and `DeviceRecordId` — populated by `DeviceCredentialValidator`
from the same `Device` row it already loaded for the credential check. This is non-breaking:
`DeviceCredentialValidationController` continues to check only `IsValid` and is unaffected;
`SyncEventsController` is the first caller to read the new fields.

### 6.3 Camera Resolution — mapping the wire's local `cameraId` to `Camera.CameraId`

The Agent's `cameraId` (`WDA_DETECTION_CAMERA_ID`, e.g. `"camera1"`) is a short local identifier, not
the Backend's `Camera.CameraId` GUID (there is no shared identifier between the two today — the Agent
has no network call to *learn* a Backend-assigned Camera GUID, since config sync (FR-SYN-005/006) is a
separate, not-yet-implemented feature). This feature defines the mapping as: **resolve by
case-insensitive, trimmed match against `Camera.Name`, scoped to `Camera.BranchId == device.BranchId`**
(the authenticated device's own Branch — never any other Branch's cameras, and there is no direct
Device↔Camera foreign key to join through otherwise). No match, or a match belonging to a disabled
Camera (`Enabled == false`), is rejected with `errorCode = "UNKNOWN_CAMERA"`. This requires the
Branch's Camera row to be named identically to the Agent's configured `WDA_DETECTION_CAMERA_ID` — an
operational convention this feature documents in the Jetson deployment README, not a new schema field.

## 7. Agent-Side Outbox Draining

### 7.1 `DetectionEventRepository` Extensions

```python
def list_pending(self, limit: int) -> list[DetectionEvent]: ...
def mark_delivered_many(self, event_ids: Sequence[UUID], delivered_at_utc: datetime) -> int: ...
```

- `list_pending`: `WHERE DeliveryStatus = 'pending' ORDER BY DetectedAtUtc ASC, EventId ASC LIMIT ?`
  (deterministic secondary ordering) — reuses IP-07's `(DeliveryStatus, CreatedAtUtc)` index; ordering
  by `DetectedAtUtc` rather than `CreatedAtUtc` satisfies "oldest events first" against the field the
  task defines as the delivery-order key, while the existing index still serves the `DeliveryStatus`
  filter efficiently. `limit` must be a positive integer; a non-positive value raises rather than
  silently returning everything or nothing.
- `mark_delivered_many`: single transaction, `UPDATE DetectionEvent SET DeliveryStatus='delivered',
  DeliveredAtUtc=? WHERE EventId IN (...) AND DeliveryStatus='pending'`. Returns the actual row count
  updated; the worker compares this against the expected count and logs (never raises into the
  caller — a mismatch is reported, not silently ignored, per the task's requirement) if they differ.
  Never `INSERT OR REPLACE`; never touches an already-`delivered` row; never deletes.

### 7.2 SQLite Schema v3→v4 Migration

```sql
ALTER TABLE DetectionEvent ADD COLUMN DeliveredAtUtc TEXT NULL;
```

`DeliveryStatus`'s CHECK constraint widens from `('pending')` (IP-07's placeholder) to `('pending',
'delivered')`. SQLite cannot alter a CHECK constraint in place — the migration follows the schema
module's own documented pattern for such a change: create the new-shape table, copy rows, drop the old
table, rename, all inside the migration's single transaction, `SchemaVersion` bumped last. Existing
`pending` rows are preserved unchanged (`DeliveredAtUtc` NULL).

### 7.3 `BackendSyncClient` (Agent, new)

Mirrors `CredentialValidationClient`'s construction (`base_url`, `timeout_seconds`, injectable
`httpx.AsyncClient`) and header constants. Responsibilities: build the POST, attach `X-Device-Id`/
`X-Device-Secret`, serialize the batch, parse the envelope strictly (reject a response whose `data`
shape doesn't match, exactly as `CredentialValidationClient` does for its own envelope), return a typed
result (`accepted: set[UUID]`, `duplicate: set[UUID]`, `rejected: dict[UUID, str]`) to the worker. Never
writes to SQLite. Never logs `X-Device-Secret`, full headers, or full request/response JSON — only
batch size and outcome counts (§9).

### 7.4 `DetectionEventSyncWorker` (Agent `OperationalComponent`, new)

One `asyncio` task in the existing FastAPI/Uvicorn process — no new systemd unit, no new process.
Loop: wait for `Operational` state → `list_pending(batch_size)` → if empty, sleep the idle interval →
else send via `BackendSyncClient` → `mark_delivered_many` for accepted+duplicate EventIds only →
continue draining immediately while pending rows remain → on network/5xx/401/malformed-response
failure, leave all unacknowledged rows pending and sleep with exponential backoff (bounded jitter,
capped) → repeat. Exactly one worker instance; disabled (`WDA_DETECTION_SYNC_ENABLED=false`, the
shipped default) constructs no worker at all (same two-layer kill-switch as IP-07 §9). Backend
availability never gates Agent/DeepStream startup — the worker is the last component started and the
first stopped (§8).

## 8. Lifecycle Wiring

Startup order: `DetectionIngestHandler` → DeepStream Bridge → `DetectionEventSyncWorker`. Shutdown:
reverse (sync worker cancels first — bounded, no hang on a sleeping backoff or an in-flight HTTP
request — then DeepStream Bridge, then `DetectionIngestHandler`), following the existing
`OperationalStateCoordinator` start-in-order/stop-in-reverse convention (IP-06/IP-07, unchanged).

## 9. Retry, Backoff, and Logging Policy

| Condition | Agent behavior |
|---|---|
| Empty outbox | Sleep `WDA_DETECTION_SYNC_INTERVAL_SECONDS`. |
| Successful batch (any accepted/duplicate) | Reset backoff; immediately attempt the next batch if pending rows remain. |
| Network error / timeout / Backend 5xx | Leave all unacknowledged rows pending; exponential backoff with bounded jitter, capped at `WDA_DETECTION_SYNC_MAX_BACKOFF_SECONDS`. |
| Backend 401 | Leave rows pending; log a redacted auth-failure event; back off exactly as a 5xx; never lock/reactivate the Agent (§6.1); never erase credentials. |
| Malformed response | Leave affected rows pending; log a protocol-error event; back off. |
| Per-event `rejected` | Leave that EventId pending; continue processing the rest of the batch's results; rate-limited logging (never unbounded per-event log lines that could let one poison event flood logs or block later attempts). |

Safe log fields only: batch size, accepted/duplicate/rejected/pending counts, HTTP status, a redacted
error category, a truncated EventId. Never: `X-Device-Secret`, full headers, activation keys, full
request/response JSON, credential-bearing URLs.

## 10. Configuration

`WDA_`-prefixed, same validation style as `WDA_DETECTION_*` (IP-07 §9):

| Field | Env var | Type / default |
|---|---|---|
| `detection_sync_enabled` | `WDA_DETECTION_SYNC_ENABLED` | `bool`, default `False` |
| `detection_sync_interval_seconds` | `WDA_DETECTION_SYNC_INTERVAL_SECONDS` | `float`, `Field(default=1.0, gt=0)` |
| `detection_sync_batch_size` | `WDA_DETECTION_SYNC_BATCH_SIZE` | `int`, `Field(default=25, ge=1, le=100)` |
| `detection_sync_initial_backoff_seconds` | `WDA_DETECTION_SYNC_INITIAL_BACKOFF_SECONDS` | `float`, `Field(default=1.0, gt=0)` |
| `detection_sync_max_backoff_seconds` | `WDA_DETECTION_SYNC_MAX_BACKOFF_SECONDS` | `float`, cross-field validated `>= detection_sync_initial_backoff_seconds` |

Reuses the existing `http_timeout_seconds` setting rather than adding a duplicate timeout. No
filesystem/network I/O inside settings validation (existing discipline, unchanged). Default
`WDA_DETECTION_SYNC_ENABLED=false` stays false until the isolated end-to-end staging test (IP-08
Phase 16) passes — production enablement is explicitly out of scope for this feature's completion.

## 11. Known Limitations Carried Into the Implementation Plan

- No snapshot delivery — `Alert.SnapshotReference` stays `null` for every row created by this feature.
- Camera resolution depends on an operational naming convention (§6.3), not a shared identifier —
  a future config-sync feature (FR-SYN-005/006) may replace this with a Backend-assigned Camera id the
  Agent learns directly, at which point this mapping is revisited.
- No dead-letter table — a `rejected` event stays `pending` forever if its rejection reason never
  resolves (e.g., a permanently misconfigured camera name); this is the documented, accepted behavior
  per the task's explicit exclusion of dead-letter machinery from this increment.

## 12. Open Items

| ID | Issue | Resolution proposed |
|----|-------|----------------------|
| OI-11 | `Camera.Name`-based resolution (§6.3) assumes Branch operators name their Camera row identically to the Agent's `WDA_DETECTION_CAMERA_ID`. No validation enforces this at Camera-creation time today. | Documented as an operational convention in the Jetson deployment README; a future feature could add a dedicated Backend-assigned camera identifier if this proves error-prone in practice. |
| OI-12 | EF Core savepoint support (`Database.CreateSavepointAsync`) on SQL Server needs a live-database confirmation as part of IP-08's integration tests, not assumed from documentation alone. | IP-08 Phase 13 integration tests exercise this directly against a real SQL Server test database. |
