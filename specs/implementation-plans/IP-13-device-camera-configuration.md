# Implementation Plan: Server-Driven Device Camera Configuration

| Field | Value |
|-------|-------|
| Plan ID | IP-13 |
| Title | Server-Driven Device Camera Configuration — Backend `DeviceConfigurationController`, Agent `configuration/` package, Bridge multi-source pipeline |
| Status | **Complete** — T-220–T-252 delivered, deployed and validated on real Jetson hardware. Production runs server-driven mode with two enabled Cameras, `batch-size=2`, and two CameraId-derived RTSP output mounts. Source0→Front Camera and source1→Rear Entrance both proven; zero cross-assignment, zero legacy-name fallback, zero duplicates, pending=0. Five-minute soak passed (0 restarts, PIDs stable). `WDA_DEVICE_CONFIG_MAX_CAMERAS` reduced 8→3 on measured GPU headroom. Final report: `docs/validation/FS-11-IP-13-production-validation-report.md`. Phase 22 production boundary reached — see §5. |
| Realizes | FS-11 |
| Governing Documents | FS-11, FS-02 (frozen — Camera entity, extended additively only), FS-06 (frozen except §6.3 `ResolveCameraIdAsync`, which this plan replaces per FS-11 §10), IP-07 (frozen — Bridge wire protocol, unchanged) |
| Depends On | FS-02/IP-01, FS-06/IP-08, IP-07 — all fully delivered |
| Task ID Range | **T-220 – T-260** |
| Owner | Farhan Naeem |
| Explicitly Excluded | TensorRT engine path, YOLO model, parser, confidence threshold, NMS, tracker, inference interval, encoder/RTSP output, cooldown, snapshot settings (all remain local reviewed deployment config). Branch timezone administration. Snapshot JPEG correlation. In-process GStreamer hot-plug. Production deployment/activation of `WDA_DEVICE_CONFIG_ENABLED=true` — this plan stops at Phase 21 (isolated validation) per the task brief; Phase 22 (production boundary) requires separate explicit approval. |

---

## 1. Task Breakdown

### Backend (.NET)

| Task | Description |
|---|---|
| T-220 | `AddCameraSourceOrder` EF migration — additive `SourceOrder int NOT NULL DEFAULT 0` column on `Cameras`; data-fixup sets `0` for all existing rows; partial unique index `(BranchId, SourceOrder) WHERE Enabled = 1` (FS-11 §2). |
| T-221 | `Camera.cs` domain — add `SourceOrder` property, extend `UpdateConfiguration` (or add `UpdateSourceOrder`) with the non-negative/uniqueness-at-service-layer contract; `CameraConfiguration.cs` EF mapping for the new column + index. |
| T-222 | `IDeviceConfigurationService`/`DeviceConfigurationService` (Infrastructure) — resolves authenticated Device → Branch → enabled Cameras ordered by `SourceOrder`; computes `configurationVersion` (FS-11 §4, SHA-256 over CameraId|StreamUrl|SourceOrder tuples). |
| T-223 | API DTOs: `DeviceConfigurationResponseDto`, `DeviceCameraConfigDto` (FS-11 §3 exact shape — no secrets). |
| T-224 | `DeviceConfigurationController` (`GET api/v1/device/configuration`) — `[AllowAnonymous]`, `X-Device-Id`/`X-Device-Secret` headers via the existing `IDeviceCredentialValidator` (reused, not reimplemented), uniform 401, `DeviceAuthenticationUnavailable` 503 reuse. |
| T-225 | `AlertSyncService.ResolveCameraIdAsync` replacement — direct `Guid.TryParse` + `(CameraId, BranchId)` lookup (FS-11 §10); transitional legacy-name fallback behind a logged deprecation path. |
| T-226 | DI registration for `IDeviceConfigurationService`, scoped. |
| T-227 | Backend unit tests — FS-11 §16 items 2–10, 13–15 (immutable id shape, name-with-spaces, name/url/enabled/sourceOrder version-hash behavior, disabled-camera omission, deterministic ordering, no-secret assertion, no-log-of-streamUrl-credentials). |
| T-228 | Backend integration tests (real SQL Server) — FS-11 §16 items 1, 11, 12, 16, 17, 19, 20, 21 (Device receives only its own Cameras, cross-Branch rejection, 401/503, empty-camera-list response, GUID-based sync resolution + rejection of foreign Camera, existing Alert-idempotency/quota/snapshot suites still green). |

### Agent (Python)

| Task | Description |
|---|---|
| T-229 | `agent/src/weapon_detection_agent/configuration/models.py` — `DeviceCameraConfig`, `DeviceConfiguration` value objects (mirrors `sync/models.py` shape). |
| T-230 | `agent/src/weapon_detection_agent/configuration/client.py` — `DeviceConfigurationClient`; `X-Device-Id`/`X-Device-Secret` (reuse `sync/client.py` header constants); outcome classification (`Valid`/`Unauthorized`/`Unavailable`/`Invalid`/`TransportFailure`); response-size/camera-count bounds; no `streamUrl`/secret logging. |
| T-231 | `agent/src/weapon_detection_agent/configuration/validation.py` — schema version, DeviceId match, BranchId presence, CameraId uniqueness, non-negative unique SourceOrder, absolute `rtsp(s)://` StreamUrl, max-camera-count, atomic reject (FS-11 §3, task brief Phase 7). |
| T-232 | `ConfigCacheRepository` — add the first writer (`save(config)`) alongside the existing reader; `agent/src/weapon_detection_agent/persistence/schema.py` unchanged (existing `ConfigCache` table reused, FS-11 §6) — no schema-version bump. |
| T-233 | `agent/src/weapon_detection_agent/configuration/coordinator.py` — `DeviceConfigurationCoordinator` lifecycle component (FS-11 §5): load-cache-first, fetch/validate/compare-version, persist-and-apply-only-on-change, exponential backoff, first-start-no-cache wait state, clean cancellation. |
| T-234 | `agent/src/weapon_detection_agent/config/settings.py` — add `WDA_DEVICE_CONFIG_ENABLED` (default `false`), `WDA_DEVICE_CONFIG_REFRESH_SECONDS` (default `30`), `WDA_DEVICE_CONFIG_MAX_CAMERAS` (default `8`), `WDA_DEVICE_CONFIG_CACHE_MAX_AGE_SECONDS` (default `0` = no forced expiry). |
| T-235 | Runtime DeepStream config generator (`agent/src/weapon_detection_agent/deepstream/config_generator.py`) — reads the static reviewed template once, replaces `[sourceN]`/`[streammux] batch-size`, temp-file+fsync+atomic-rename to `/opt/weapon-detection/runtime/deepstream.generated.conf`, correct ownership/mode (FS-11 §7). |
| T-236 | `DeepStreamProcessManager` — extend to accept the generated config path and support a controlled stop→wait→start restart cycle triggered by the coordinator (not a new supervisor; extends the existing one). |
| T-237 | Source-generation mapping — `agent/src/weapon_detection_agent/configuration/source_mapping.py`, `{source_id: CameraId}` per applied generation, monotonic generation counter, unknown-`source_id` safe rejection (FS-11 §9). |
| T-238 | `DetectionIngestHandler`/`ingest_handler.py` — replace the static `settings.detection_camera_id` wiring with a resolved `Camera.CameraId` (via the current generation's mapping) when `WDA_DEVICE_CONFIG_ENABLED=true`; keep the static path when `false` (FS-11 §11). |
| T-239 | `AgentRuntimeSupervisor`/component wiring — start `DeviceConfigurationCoordinator` only after `Operational`, only when `WDA_DEVICE_CONFIG_ENABLED=true`; clean shutdown ordering (coordinator stops before Bridge is torn down). |
| T-240 | Agent unit tests — FS-11 §17 items 1–5, 9–11, 16–20, 23–27, 30 (fetch-before-bridge-start, source-count-per-camera, batch-size, disabled-exclusion, no-op on unchanged version, cache persistence, duplicate/order/url/count rejection, source_id mapping + unknown rejection, 401-no-rotation, no-credential-in-logs, feature-disabled parity, snapshot-disabled parity). |
| T-241 | Agent integration/lifecycle tests — FS-11 §17 items 6–8, 12–15, 21, 22, 24, 25, 28, 29 (name-only no-restart, url/enabled/order-triggers-exactly-one-restart, cache-on-restart, outage-keeps-cache-running, no-cache-waits-safely, invalid-config-keeps-old-running, config-race handled via generation counter, backoff on 503, prompt cancellation, sync/quota regression green). |

### Bridge (Python 3.8)

| Task | Description |
|---|---|
| T-242 | `deployment/jetson/deepstream/bridge/app/deepstream_bridge/config.py` — extend `_parse_source` to loop `source0..sourceN` (bounded), producing a list of `SourceConfig` instead of one. |
| T-243 | `pipeline.py` — one source bin + decoder per parsed `SourceConfig`; request `sink_%u` pads keyed by each source's index; `streammux.batch-size` from the parsed config; keep one shared `nvinfer`/OSD/encoder branch unchanged. |
| T-244 | `pipeline.py` — per-source independent async state handling so one camera's connect failure/timeout does not block pad-added callbacks for the others. |
| T-245 | Bridge unit tests — FS-11 §18 items 1–5, 8–11 (one/multi source construction, correct request pads, correct batch-size, deterministic index assignment, no duplicate components, tracker/model/parser/NMS/confidence/RTSP-output unchanged). |
| T-246 | Bridge unit tests — FS-11 §18 items 6, 7, 12, 13, 14 (one-offline-camera doesn't corrupt mapping, clean multi-source shutdown, snapshot-disabled unchanged, Python 3.8 compatibility (`ast.parse(feature_version=(3,8))`, existing test extended), full existing suite green). |

### Cross-cutting

| Task | Description |
|---|---|
| T-247 | `deployment/jetson/agent.env.example` — document the four new `WDA_DEVICE_CONFIG_*` settings (default-disabled), matching FS-11 §11 rollout contract. |
| T-248 | `design/stitch/ANGULAR-IMPLEMENTATION-MAP.md` / Admin UI copy review only (no functional UI change in this plan) — confirm the existing Camera edit form's "Name" vs "Stream URL" fields are already labeled distinctly (FS-11 §"Phase 14" naming-vs-URL semantics); note any copy fix as a follow-up if the current labels are ambiguous. |
| T-249 | Full verification — `dotnet build`/`test`/vulnerable-package scan/EF pending-model-check; Agent full suite + `ruff check`/`ruff format --check`/`mypy`; Bridge full suite + Python 3.8 static check; `docker compose config --quiet` + `docker compose build backend migrations frontend` (no `up`). |
| T-250 | Isolated single-camera end-to-end validation (isolated Backend DB + temporary Agent db/config/cache, `WDA_DEVICE_CONFIG_ENABLED=true` in that isolated environment only) — FS-11 §12 items 9, plus task brief Phase 20 items 1–13. |
| T-251 | Isolated multi-camera end-to-end validation — FS-11 §12 item 10, task brief Phase 21 items 1–9; if two simultaneous real RTSP streams are not available, report the strongest safe isolated substitute honestly (e.g. one real stream + one pre-recorded loop, or two file sources through the same multi-source code path) rather than skip the proof. |
| T-252 | Final report (FS-11/IP-13 Phase 22) — production boundary; explicit confirmation production Camera/Branch/Device data, `WDA_DEVICE_CONFIG_ENABLED`, the Agent process, the Backend container, the publisher, and the quota counter were all left untouched. Stop before any production deployment/activation decision. |

*(Task numbers T-253–T-260 reserved for follow-up work identified during implementation — e.g. removing the
legacy Camera-name sync fallback once telemetry confirms zero usage — not part of this plan's code-complete
milestone.)*

---

## 2. Frozen Contract Reference

Read FS-11 in full before starting any task above — §2 (identity model), §3 (API contract), §4
(`configurationVersion` hash input), §9 (source_id mapping/generation-race safety), and §11 (rollout flag) are
binding. Do not deviate from the exact hash input or the "omit disabled Cameras" response contract without
updating FS-11 first.

## 3. Acceptance Criteria

Full list: FS-11 §12. Summary — Camera.Name never used as pipeline identity; Camera.RtspUrl/StreamUrl drives
source creation; one source per enabled Camera; batch-size matches; source_id maps deterministically to
immutable CameraId per generation; name-only edits never restart the Bridge; url/enabled/order edits restart it
exactly once; last-known-good cache survives a Backend outage; one single-camera and one multi-camera genuine
detection are both proven end-to-end with correct, non-cross-assigned Alert attribution; FS-06/FS-07/FS-08/FS-09
regression suites remain green.

## 4. Rollback

Set `WDA_DEVICE_CONFIG_ENABLED=false` (already the default) — the Agent reverts immediately to the static
`deepstream-app.txt`/`WDA_DETECTION_CAMERA_ID` path with no further action. The `SourceOrder` column and
`GET /api/v1/device/configuration` endpoint are additive and inert when unused; no Backend rollback step is
required beyond leaving the flag off. The transitional legacy-Camera-name sync fallback (T-225) keeps any
already-deployed Agent working throughout, so there is no forced simultaneous upgrade.

---

## 5. Delivery Record (T-252, 2026-07-31)

All tasks in §1 are **Complete**. Full evidence:
`docs/validation/FS-11-IP-13-production-validation-report.md`.

| Workstream | Tasks | Status |
|---|---|---|
| Backend — endpoint, `SourceOrder`, DTOs, GUID sync resolution, DI, tests | T-220–T-228 | Complete |
| Agent — config client, validation, cache writer, coordinator, settings, config generator, process manager, source mapping, ingest wiring, supervisor, tests | T-229–T-241 | Complete |
| Bridge — multi-source config parsing, N-source pipeline, per-source async state, tests | T-242–T-246 | Complete |
| Cross-cutting — env docs, UI copy review, full verification | T-247–T-249 | Complete |
| T-250 single-camera hardware validation | T-250 | Complete |
| T-251 multi-camera hardware validation | T-251 | Complete |
| T-252 final production-boundary report | T-252 | Complete |

Delivered beyond the original §1 breakdown, from the reserved T-253–T-260 range:

| Item | Status |
|---|---|
| Dynamic per-camera annotated output — Backend (`Camera.OutputPath`, `Device.AnnotatedOutputBaseUrl`) | Complete |
| Dynamic per-camera annotated output — Agent (generated per-source `output-path`) | Complete |
| Dynamic per-camera annotated output — Bridge (N demux branches, unique RTSP factories, cleanup) | Complete |
| API/frontend output discovery (input vs annotated URL, separate copy actions, no `/ds-test` assumption) | Complete |
| Production deployment and activation (`WDA_DEVICE_CONFIG_ENABLED=true`) | Complete |
| source0 immutable-CameraId proof (Front Camera, 15 events, history since cleared) | Complete |
| source1 immutable-CameraId proof (Rear Entrance, 3 events, `a285ea58…` traced) | Complete |
| Output mount cleanup on Camera removal (stale mount 404s, survivors unchanged) | Complete |
| ConfigCache outage / Agent-restart / Jetson-reboot recovery proof | Complete |

**Deviation from §Explicitly Excluded.** The original plan stopped at Phase 21 and deferred Phase 22
(production boundary) pending separate explicit approval. That approval was given and executed:
`WDA_DEVICE_CONFIG_ENABLED=true` is live in production. `WDA_DEVICE_CONFIG_MAX_CAMERAS` was reduced
from the planned default of 8 to **3**, on measured two-camera GPU utilisation (~70 % mean, peaking
98–99 %); three cameras were functionally demonstrated but only two were soak-validated, so four- and
eight-camera capacity is explicitly **not** claimed.

**Not completed by this plan:** FS-08/IP-10 snapshot evidence capture remains incomplete
(unresolved real-hardware JPEG correlation). Snapshot capture and upload stay disabled.
