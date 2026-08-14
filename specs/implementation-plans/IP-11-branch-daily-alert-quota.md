# Implementation Plan: Branch-Level Daily Alert Quota

| Field | Value |
|-------|-------|
| Plan ID | IP-11 |
| Title | Branch-Level Daily Alert Quota — Backend `BranchDailyAlertQuota`/`SuppressedDetectionEvent`, extended `AlertSyncService`, Agent `suppressed_by_quota` delivery status |
| Status | In progress. |
| Realizes | FS-09 |
| Governing Documents | FS-09, FS-06 (frozen — idempotency ordering, checked first), FS-08 (frozen — snapshot outbox interaction) |
| Depends On | FS-06/IP-08 fully delivered (done), FS-08/IP-10 fully delivered (done) |
| Task ID Range | **T-166 – T-190** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Model, tracker, inference interval, confidence, NMS, RTSP output, Data Protection, Device credentials (all unchanged). Angular dashboard quota UI. `Branch.TimeZoneId` Admin-facing editing surface (field exists, no UI — FS-09 §14 OI-13). Production enablement/deployment — this plan stops at Phase 13 (isolated validation) per the task brief; `AlertQuota__Enabled` is not toggled on the production Backend by this plan. |

---

## 1. Task Breakdown

### Backend (.NET)

| Task | Description |
|---|---|
| T-166 | `Branch.TimeZoneId` nullable field + `UpdateTimeZone` domain method (FS-09 §4). `BranchDailyAlertQuota` and `SuppressedDetectionEvent` domain entities (FS-09 §6.1/§6.2). |
| T-167 | EF Core configurations (`BranchDailyAlertQuotaConfiguration`, `SuppressedDetectionEventConfiguration`) + one migration: both tables, unique indexes, `Branches.TimeZoneId` column. Run `dotnet ef migrations has-pending-model-changes` after. |
| T-168 | `AlertQuotaOptions`/`AlertQuotaOptionsValidator` (FS-09 §11), DI registration with `ValidateOnStart()`. `compose.yaml` `AlertQuota__*` env vars. |
| T-169 | `IAlertSyncService` contract extension: `SyncEventOutcomeKind.QuotaExceeded`, `SyncEventOutcomeNames.QuotaExceeded`, `SyncEventErrorCodes.BranchDailyAlertQuotaReached`, `SyncEventOutcome.QuotaExceeded(...)` factory carrying quota info. |
| T-170 | `SyncEventResultDto`/`SyncEventsController` mapping: optional nested `quota` object, populated only for `quota_exceeded` (FS-09 §5). |
| T-171 | `AlertSyncService`: branch-local day resolution (`Branch.TimeZoneId` → UTC fallback), reordered idempotency-then-quota flow (FS-09 §7/§8) inside the existing per-event savepoint. |
| T-172 | `AlertSyncService`: atomic `BranchDailyAlertQuota` conditional-increment-then-insert, `SuppressedDetectionEvent` insert on exhaustion, savepoint rollback on Alert-insert race (FS-09 §7 steps 4a/4b). |
| T-173 | `ILogger<AlertSyncService>` + `branch_daily_quota_reached`/`event_suppressed_by_quota`/`daily_quota_summary` rate-limited log templates (FS-09 §13). |
| T-174 | Backend unit tests — Phase 8 items 1, 3, 4, 6, 7, 9–11, 13, 14, 17–21 (single-request scenarios: accept/suppress boundary, shared quota across devices/cameras, branch isolation, duplicate/quota-retry idempotency, failed-insert-no-consumption, mixed-batch outcomes, date-boundary correctness, existing idempotency/auth/DataProtection regression). |
| T-175 | Backend integration tests against real SQL Server — Phase 8 items 2, 5, 8, 12, 15, 16, 22–24 (HTTP 200 for quota outcome, concurrent-submission boundary via `Task.WhenAll`, DST transition, next-day reset, EF migration applies cleanly, no pending model changes). |

### Agent (Python)

| Task | Description |
|---|---|
| T-176 | SQLite schema v5→v6 migration: widen `DetectionEvent.DeliveryStatus` CHECK to include `suppressed_by_quota`, widen `SnapshotOutbox.UploadStatus` CHECK to include `suppressed_by_quota` (FS-09 §9/§10), following the existing rebuild-table pattern. Schema tests. |
| T-177 | `DetectionEventRepository.mark_suppressed_by_quota_many` (FS-09 §9) + repository tests. |
| T-178 | `SnapshotOutboxRepository.cancel_for_quota` + `UploadStatus.SUPPRESSED_BY_QUOTA` (FS-09 §10) + repository tests. |
| T-179 | `sync/models.py`: `SyncBatchResult.quota_exceeded` field carrying per-event quota info. |
| T-180 | `sync/client.py`: `_OUTCOME_QUOTA_EXCEEDED` parsing branch, replacing today's silent-drop fallthrough for this outcome string. |
| T-181 | `sync/worker.py`: mark quota-suppressed EventIds via `mark_suppressed_by_quota_many`; never call `alert_id_sink` for them; call `SnapshotOutboxRepository.cancel_for_quota` for any associated outbox row; local JPEG cleanup ordered strictly after the terminal DB state commits (FS-09 §10). |
| T-182 | Agent unit tests — Phase 9 items 1–15 (terminal state, excluded from `list_pending`, unchanged delivered/rejected/malformed semantics, partial-batch handling, outbox cancellation, JPEG-removal ordering, no upload occurs, restart does not resend, existing metadata-sync/cooldown/historical-archive tests remain green). |

### Verification and Sign-off

| Task | Description |
|---|---|
| T-183 | Backend: `dotnet build`/`dotnet test`/`dotnet list package --vulnerable --include-transitive`/EF pending-model-check (Phase 11). |
| T-184 | Agent: focused quota tests, full agent suite, `ruff check`, `ruff format --check`, configured `mypy` (Phase 11). |
| T-185 | Bridge regression suite (no cross-boundary change expected — proof only). |
| T-186 | `docker compose config` validation of the new `AlertQuota__*` env vars. |
| T-187 | Isolated end-to-end validation: isolated compose project, test SQL Server, temporary Agent SQLite, 2 test Devices in 1 test Branch, 20-event send (Phase 12 items 1–8). |
| T-188 | Concurrent boundary test (Alerts 14–17 racing) against real SQL Server (Phase 12 item 9). |
| T-189 | Injectable-clock next-branch-local-day reset test (Phase 12 item 10). |
| T-190 | Final Phase 13 report — authoritative IDs, schema/contract changes, concurrency proof, test totals, deployment/rollback plan — stop before any production deployment. |

## 2. Frozen Contract Reference

The exact quota-day resolution rule, response JSON shape, entity field lists, concurrency/savepoint
design, idempotency ordering, snapshot-suppression mechanism, and configuration keys are specified in
**FS-09 §4–§13** and are binding for this plan; this document does not restate them. FS-06 §5.2/§6 (event
idempotency, camera/device resolution) and FS-08's `SnapshotOutbox` schema remain frozen and unchanged —
implementers must read FS-09 in full, plus FS-06/FS-08 for context, before starting T-166/T-176.

## 3. Acceptance Criteria (summary — full list is FS-09 task brief Phase 8/9/12)

- The first 15 Alerts for a Branch/local-day are created normally; the 16th+ detection creates no Alert
  and returns `quota_exceeded` with `errorCode=BRANCH_DAILY_ALERT_QUOTA_REACHED`.
- The limit is shared across every Device and Camera on the Branch, and is independent per Branch.
- `quota_exceeded` returns HTTP 200 within a structurally valid batch, never 429 for an individual item.
- A real-SQL-Server concurrent-submission test proves the Branch/day Alert count never exceeds the
  configured maximum.
- Duplicate `(DeviceId, EventId)` retries never consume quota; retried quota-suppressed EventIds never
  increment suppression counters twice; a failed Alert insert never consumes quota.
- The Agent marks `quota_exceeded` events `suppressed_by_quota` (terminal, excluded from `list_pending`,
  never resent) and never uploads or retains a snapshot for them.
- The next branch-local day resets the counter to 0.
- Historical archived `DetectionEvent` rows and existing Alert `(DeviceId, EventId)` idempotency are
  unaffected.

## 4. Rollback

Setting `AlertQuota__Enabled=false` fully disables quota enforcement — every event reverts to FS-06's
original unconditional-accept-subject-to-idempotency behavior, with no other change required. The new
`BranchDailyAlertQuota`/`SuppressedDetectionEvent` tables and `Branch.TimeZoneId` column are additive and
harmless if unused. No production Backend or Agent configuration is changed by this plan (Phase 13).
