# Implementation Plan: Administrator-Defined Camera Keys and Branch Jetson Network Configuration

| Field | Value |
|-------|-------|
| Plan ID | IP-14 |
| Title | `Camera.CameraKey` public mount identity + `Device.JetsonHost`/`Device.RtspOutputPort` structured network configuration |
| Status | **Complete — Production Deployed and Validated (all tiers).** T-261–T-290 delivered; 2717 tests green. Production migration applied once at 2026-08-02T20:46:33Z with verified SQL + SQLite backups taken first. Backend and frontend containers recreated; SQL Server container untouched. Agent adopted `configurationVersion` `cd8ffea3…` and performed exactly one Bridge restart; detection identity remains the immutable Camera GUID. The FS-12 Agent build was deployed at 2026-08-02T21:14:04Z; the legacy-cache compatibility path was exercised for real in production. See §4B/§4C. |
| Realizes | FS-12 |
| Governing Documents | FS-12, FS-02 (frozen — Branch/Device/Camera model, extended additively), FS-11 (Complete — output-path derivation replaced per FS-12 §2.1), IP-07 (frozen — Bridge wire protocol, unchanged) |
| Depends On | FS-02/IP-01, FS-11/IP-13 — both delivered |
| Task ID Range | **T-261 – T-290** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Browser RTSP playback / WebRTC / HLS. CameraKey editing. Multiple Devices per Branch. Dropping the `AnnotatedOutputBaseUrl` column (deferred). Model/parser/confidence/NMS/tracker/inference-interval/cooldown/snapshot settings. **Production deployment and migration execution — this plan stops at the production boundary (T-290).** |

---

## 1. Approved Design Decisions

Three choices were taken before implementation and are binding for this plan:

1. **`CameraConfigDto` gains `cameraKey` only.** `SourceOrder` remains auto-assigned from the camera's position
   in the creation array and `Enabled` remains defaulted to `true`, exactly as FS-02 defines today. This keeps
   the change additive and leaves existing branch-creation semantics and tests intact.
2. **Option A (transitional) for the base URL.** `AnnotatedOutputBaseUrl` is retained as a read-fallback only;
   removal is a later feature.
3. **The existing `PUT /api/v1/devices/{branchId}/annotated-output-base-url` endpoint is replaced** by
   `PUT /api/v1/devices/{branchId}/network`, rather than run alongside it — two writers over overlapping state
   would be free to diverge.

---

## 2. Task Breakdown

### Backend (.NET)

| Task | Description |
|---|---|
| T-261 | `Camera.cs` — add `CameraKey` property + `CameraKeyMaxLength`; `NormalizeAndValidateKey` enforcing the FS-12 §3 pattern, bounds and reserved list; change `DeriveOutputPath` to take a key; add `RequireCameraKey`. Constructor takes `cameraKey` as a required argument. |
| T-262 | `Device.cs` — add `JetsonHost` (`JetsonHostMaxLength = 255`) and `RtspOutputPort`; `SetNetworkConfiguration(host, port)` enforcing FS-12 §4; `ComposeAnnotatedOutputBase()` preferring structured fields with legacy-column fallback and IPv6 bracketing; `ComposeAnnotatedOutputUrl` rebuilt on top of it. `SetAnnotatedOutputBaseUrl` retained for the transition. |
| T-263 | EF `CameraConfiguration` — `CameraKey` column, required, max length; unique index `IX_Cameras_BranchId_CameraKey` on `(BranchId, CameraKey)`. Existing `(BranchId, SourceOrder)` filtered index preserved unchanged. |
| T-264 | EF `DeviceConfiguration` — `JetsonHost` nullable + max length, `RtspOutputPort` nullable int. `AnnotatedOutputBaseUrl` mapping retained. |
| T-265 | Migration `AddCameraKeyAndDeviceNetwork` — additive columns; backfill `JetsonHost`/`RtspOutputPort` by parsing existing `AnnotatedOutputBaseUrl`; backfill `CameraKey` for the two known POC rows (`front-camera`, `rear-entrance`) keyed on **CameraId**, not on name; then apply the unique index. |
| T-266 | `CameraConfigDto` — add `CameraKey`. `CreateBranchRequestDto` unchanged otherwise. |
| T-267 | `CreateBranchRequestDto`/Branch creation — add `JetsonHost` (required) and `RtspOutputPort` (optional, default 8554). |
| T-268 | `BranchService.CreateBranchAsync` — set Device network configuration inside the existing transaction; pass `cameraKey` into each `Camera`; map named validation errors; full rollback on any failure. |
| T-269 | `BranchService.UpdateBranchAsync` — reject any attempt to change an existing Camera's key with `CAMERA_KEY_IMMUTABLE`; require `cameraKey` on newly added Cameras; enforce uniqueness against stored keys. |
| T-270 | `BranchService.MapCamera` — derive output path from `CameraKey`; compose the output URL from the Device's structured network configuration. |
| T-271 | `DeviceService` — replace `SetAnnotatedOutputBaseUrlAsync` with `SetNetworkConfigurationAsync(branchId, host, port)`; `DeviceOutputBaseUrlUpdate` → `DeviceNetworkUpdate` carrying host, port and the composed base. |
| T-272 | `DeviceController` — `PUT /api/v1/devices/{branchId}/network`; remove the superseded `annotated-output-base-url` route and its DTO. |
| T-273 | Response DTOs — `DeviceSummaryDto`/`DeviceDetailResponseDto` expose `jetsonHost`, `rtspOutputPort`, computed `annotatedOutputBaseUrl`; `CameraResponseDto` exposes `cameraKey`. No credentials or Activation Keys. |
| T-274 | `DeviceConfigurationService` — include `cameraKey` in the Agent payload; derive `outputPath` from the key; `configurationVersion` input unchanged in *shape* (`CameraId\|StreamUrl\|SourceOrder\|OutputPath`) so host/port stay excluded by construction. |
| T-275 | `DeviceConfigurationResponseDto` — add `cameraKey`. |
| T-276 | Backend unit tests — FS-12 §9 and task-brief Phase 18 items 1–34. |
| T-277 | Backend integration tests (real SQL Server) — transaction rollback (items 20–23), cross-Branch key reuse (19), duplicate rejection (18), and green regression of quota/snapshot/device-auth/branch-transaction suites (35–38). |

### Agent (Python)

| Task | Description |
|---|---|
| T-278 | `configuration/models.py` — add `camera_key` to `DeviceCameraConfig` (diagnostics/contract fidelity; `output_path` remains the authoritative mount). |
| T-279 | `configuration/serialization.py` — parse and round-trip `cameraKey`. |
| T-280 | `configuration/validation.py` — accept the CameraKey-based `output_path`; keep the existing safety rules (relative, no traversal, no control characters, unique); no GUID-shaped assumption anywhere. |
| T-281 | `ConfigCache` — persist `cameraKey` alongside the existing fields; load a legacy GUID-path cache without error. |
| T-282 | Agent tests — task-brief Phase 19 items 1–16. |

### Bridge (Python 3.8)

| Task | Description |
|---|---|
| T-283 | Bridge tests only — confirm `cameras/front-camera` / `cameras/rear-entrance` are accepted, duplicates rejected, N mounts for N cameras, stale mounts removed on replacement, `source_id`→GUID mapping untouched, Python 3.8 compatibility. **No production Bridge code change is expected**; its output-path validation is already format-agnostic. |

### Frontend (Angular)

| Task | Description |
|---|---|
| T-284 | `branch.models.ts` — `cameraKey`, `jetsonHost`, `rtspOutputPort` on the request/response models. |
| T-285 | `branch.validators.ts` — CameraKey pattern/reserved-word validator; JetsonHost validator; port range validator. |
| T-286 | `camera-config-form.ts` — Camera key field with lowercase helper text, format hint, inline errors, value preserved across unrelated validation failures, optional explicit "suggest key from name" button whose suggestion is **not** submitted until accepted. Read-only in edit mode. |
| T-287 | `branch-create.ts` — Device section: "Jetson IP or hostname" + "RTSP output port" (default 8554). Labels must not mention Tailscale; helper text may explain the POC usage. |
| T-288 | `branch-detail.ts` / `branch-edit.ts` — show Jetson host, RTSP port, annotated output base; per-Camera card shows name, key (read-only), input URL and annotated output URL with **separate** copy actions. No claim that browsers play RTSP. |
| T-289 | Frontend tests — task-brief Phase 21 items 1–15. |

### Cross-cutting

| Task | Description |
|---|---|
| T-290 | Full verification (Phase 22) + migration/compatibility assessment (Phase 23) + final report. **Stops at the production boundary** — no migration is run against production, no service restarted, no publisher started. |

---

## 3. Live-Feed Discovery (Phase 17)

The existing `GET /api/v1/branches/{branchId}` response, once T-273 lands, already returns the Branch, its single
Device (host, port, composed base) and every Camera with `cameraId`, `cameraKey`, `name`, `enabled`,
`sourceOrder`, `outputPath` and `outputStreamUrl`. That is the complete data set a future live-feed page needs,
so **no `/live-feeds` endpoint is added** — creating a second API over identical data would be duplication.
Should a future page need enabled-only filtering, it is a query parameter on the existing route, not a new one.

---

## 4. Acceptance Criteria

FS-12 §9 in full. Summary: CameraKey is administrator-entered, validated, unique, immutable and the sole public
mount identifier; `Camera.Id` remains the internal detection identity everywhere; host/port are structured and
client-facing only; renames and network edits never restart the Bridge; a CameraKey change would (but cannot
occur post-creation); all existing suites stay green.

---

## 4A. Task Evidence (checkpoint, 2026-07-31)

| Task | Status | Evidence |
|---|---|---|
| T-261–T-275 | Complete | Backend production layer; `dotnet build` 0 errors; EF pending-model check clean |
| T-276 | Complete | 474 unit tests green, incl. new `DeviceNetworkTests` and rewritten `CameraTests` key/derivation coverage |
| T-277 | Complete | 451 integration tests green. New `CameraKeyAndDeviceNetworkApiTests` (30) and `CameraKeyMigrationSqlServerTests` (14) |
| T-278 | Complete | `camera_key` added to `DeviceCameraConfig`; parsed by `client.py` |
| T-279 | Complete | `serialization.py` writes `cameraKey`; legacy caches recover it from `outputPath` |
| T-280 | Complete | `validation.py`: key pattern, duplicate-key rejection, `output_path == cameras/{camera_key}` |
| T-281 | Complete | ConfigCache round-trip stability proven (no restart loop) |
| T-282 | Complete | 1079 agent tests green; `ruff check` clean; `ruff format --check` clean; mypy — only the known Windows typeshed false positive |
| T-283 | Complete | 269 bridge tests green (22 new); Python 3.8 compatibility green |
| T-284 | Complete | `branch.models.ts` — `cameraKey`, `jetsonHost`, `rtspOutputPort`, `SetDeviceNetworkRequest`/`DeviceNetworkUpdate`, key pattern + reserved list mirrored from the Backend |
| T-285 | Complete | `branch.validators.ts` — `cameraKey`, `jetsonHost`, `rtspOutputPort`, `uniqueCameraKeys` (array-level) |
| T-286 | Complete | `camera-config-form.ts` — key input with helper text, per-cause errors, opt-in "suggest key from camera name", read-only render when `keyReadOnly` |
| T-287 | Complete | `branch-create.ts` — "Jetson device configuration" section (host + port, default 8554), key control per row, payload carries `jetsonHost`/`rtspOutputPort`/`cameraKey`, Backend named codes mapped onto the offending control |
| T-288 | Complete | `branch-detail.ts` — Jetson host, RTSP port, annotated output base, per-camera key + source order; existing separate input/output copy actions retained. `branch-edit.ts` — key read-only and omitted from the update payload |
| T-289 | Complete | 442 frontend tests green (392 pre-existing + 50 new across `camera-key-network.spec.ts`, `camera-key-ui.spec.ts` and the `branch-create` FS-12 block) |
| T-290 | Complete | `npm ci` exit 0, lockfile MD5 unchanged, 539 packages, 0 production-dependency vulnerabilities. Backend restore/build/test 927 green, EF check clean. Migration Paths A/B/C green (16 tests). Agent 1079 green + ruff/format clean + mypy only the known false positive. Bridge 269 green + Python 3.8. Frontend 442 green after clean install + build + `tsc --noEmit`. `docker compose config --quiet` OK; backend/migrations/frontend images built in 63 s with containers untouched. Cross-tier contract matrix consistent. |

### Deviations recorded during implementation

1. **The scaffolded migration was wrong and was rewritten.** EF generated `CameraKey NOT NULL DEFAULT ''`
   immediately followed by the unique index, which fails for any Branch owning two Cameras. The backfill now
   runs between the two steps, and `CameraKeyMigrationSqlServerTests` seeds exactly that two-Cameras-one-Branch
   case so the ordering cannot silently regress.

2. **One Bridge production change was made** — `_parse_output_path` now rejects interior whitespace. This is a
   path-safety rule (an unescaped space makes a mount unreachable), deliberately *not* CameraKey grammar. The
   Bridge still knows nothing about key format; `test_uppercase_path_is_accepted_by_the_bridge_by_design`
   documents that placement rather than duplicating Backend/Agent policy.

3. **`duplicate_output_path` is now unreachable in the Agent.** Because `output_path` must equal
   `cameras/{camera_key}`, a shared mount can only arise from a shared key, which the earlier
   `duplicate_camera_key` check catches first. The rule is retained as defence in depth and its test updated to
   assert the reason actually reported.

---

## 4B. Production Deployment Record (2026-08-02)

| Item | Value |
|---|---|
| Migration applied | `20260731210201_AddCameraKeyAndDeviceNetwork`, once, 20:46:33Z, exit 0 |
| SQL backup | `WeaponDetection_preFS12_20260802T204508Z.bak`, 6 545 408 B, sha256 `bbcf7528…`, `RESTORE VERIFYONLY` valid |
| Jetson backup | `/opt/weapon-detection/backups/fs12-20260802T204521Z/agent.db`, 593 920 B, sha256 `e85a063d…`, `integrity_check=ok` |
| Device backfill | `JetsonHost=100.98.226.80`, `RtspOutputPort=8554`, legacy `AnnotatedOutputBaseUrl` retained |
| Camera backfill | Front `2613b331…` → `front-camera` (order 0); Rear `ad8a1f09…` → `rear-entrance` (order 1) |
| Preserved | Camera GUIDs, StreamUrls, SourceOrders, DeviceId, ActivationStatus, protected secret, Data Protection key sha256 `ed6f1d9e…`, Alerts 4, distinct EventIds 4, quota 4/1000, suppressed 0 |
| Containers | SQL untouched (`d6008174fc1b`, StartedAt unchanged); backend `fc050516`→`cae9bb12`; frontend `90c25cd3`→`2c9b6167` |
| Agent / Bridge | Agent 407832→418428, Bridge 407835→418441 — **one** Bridge transition; `NRestarts=0`; 5 fetches → 1 apply, no loop |
| Mount transition | Old GUID mounts and `/ds-test` unavailable; new `cameras/front-camera` + `cameras/rear-entrance` serving |
| Isolation proof | Simultaneous capture 0.27 s apart: front = blue "FS12 FRONT CAMERA", rear = maroon "FS12 REAR ENTRANCE"; no cross-assignment |
| False positives | None — DetectionEvents stayed 4, pending 0, suppressed 0 |

### Deviations and open items

1. **Pre-existing Agent fault, not caused by FS-12.** The configuration coordinator had been dead since
   `2026-08-01T13:25:56Z` after a run of `device_config_fetch_unavailable: timeout`, while the Agent process
   stayed healthy and kept validating credentials. It therefore could not adopt the new configuration on its
   own. A single deliberate `systemctl restart weapon-detection-agent` recovered it — permitted by the
   deployment brief as genuine fault recovery. **The underlying coordinator-death-after-timeout defect is not
   fixed and needs its own investigation**: a supervised task that exits silently will strand any future
   configuration change.

2. **The FS-12 Agent build was subsequently deployed** (see §4C). The gap recorded here — FS-11 Agent producing
   correct mounts from an opaque `outputPath` — was closed the same day.

---

## 4C. Agent Tier Deployment (2026-08-02T21:14:04Z)

Deployed with the approved `deployment/jetson/update.sh`, which preserves the env file, Device Identity,
ConfigCache, SQLite database and logs. Fresh SQLite backup taken first:
`/opt/weapon-detection/backups/fs12agent-20260802T211201Z/agent.db`, sha256 `cd688248…`, `integrity_check=ok`.

| Check | Result |
|---|---|
| Agent PID | 418428 → 420339 |
| Bridge PID | 418441 → 420348 (one transition, then stable across 75 s) |
| `NRestarts` | 0 |
| Generated config | now carries `camera-key = front-camera` / `camera-key = rear-entrance` |
| Output paths | unchanged: `cameras/front-camera`, `cameras/rear-entrance` |
| Detection identity | unchanged — all events still carry the GUID `CameraId`, `source_id=1` |
| Data | events 4, pending 0, DeviceIdentity 1, outbox 0, spool 0 — all preserved |
| Credential validation | HTTP 200 |
| Restart loop | none — 4 fetches, 0 further applies |
| Isolation re-proof | front = blue "FS12 FRONT CAMERA", rear = maroon "FS12 REAR ENTRANCE", no cross-assignment |

### Incident during deployment (resolved)

The first `update.sh` run **failed** because the packaged shell scripts carried CRLF line endings from the
Windows checkout (`install.sh: line 21: $'': command not found`). `update.sh` had already stopped the
service, so **the Agent was down for approximately 31 seconds** (21:12:56Z–21:13:27Z). Recovery: strip CR from
the staged `*.sh`, re-run `update.sh`, then `systemctl start` (the second run correctly observed the service as
already stopped and left it so). No data was lost and no publisher was running at the time.

**Follow-up required:** `deploy.ps1` packages the repository as-is from a Windows checkout, so any future Jetson
deployment hits the same CRLF failure. The packaging step should normalise line endings (or the repo should pin
`*.sh eol=lf` via `.gitattributes`). Not fixed here — it is outside FS-12's scope and needs its own change.

### ConfigCache shape note (by design, not a defect)

The cached JSON still has no `cameraKey` field, because `configurationVersion` did not change across the Agent
upgrade, so nothing was re-serialized. The FS-12 deserializer recovered `camera_key` from the stored
`outputPath` exactly as designed — which is why `camera-key=` appears in the generated config. This is the
FS-11→FS-12 legacy-cache compatibility path, proven in production rather than only in tests. The field will
materialise in the cache on the next real configuration change.

---

## 5. Rollback

The migration is additive apart from the new unique index. Rolling back the Backend to the previous build
restores GUID-derived output paths; `configurationVersion` changes back once and the Agent performs one further
controlled restart onto the GUID mounts. `AnnotatedOutputBaseUrl` is deliberately retained and still populated,
so the previous build keeps working unchanged — this is the specific reason Option A was chosen over a direct
migration. The `CameraKey` column and its index are inert to the old build.
