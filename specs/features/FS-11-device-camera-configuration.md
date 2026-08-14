# Feature Specification: Server-Driven Device Camera Configuration and DeepStream Source Orchestration

| Field | Value |
|-------|-------|
| Feature ID | FS-11 |
| Title | Server-Driven Device Camera Configuration — the Backend's `Camera` entity becomes the authoritative source of DeepStream pipeline input; `Camera.Name` becomes a pure display label, decoupled from pipeline/source identity |
| Status | **Complete** — deployed and enabled in production (`WDA_DEVICE_CONFIG_ENABLED=true`). Both source mappings proven on real Jetson hardware against the immutable `Camera.CameraId`: source0 → Front Camera (15 events, zero cross-assignment; history since intentionally cleared) and source1 → Rear Entrance (3 events, EventId `a285ea58…` traced end-to-end, `DetectedAtUtc` preserved, ~974.6 ms to Backend / ~1405.2 ms to delivered). Zero cross-assignment, zero legacy-`Camera.Name` fallback usage, zero duplicates. Two CameraId-derived annotated RTSP output mounts served from one shared mux/inference/demux pipeline (`batch-size=2`); rename causes no restart, StreamUrl change causes exactly one. Five-minute soak passed with zero restarts. Snapshots remain disabled (FS-08 unresolved). Full evidence: `docs/validation/FS-11-IP-13-production-validation-report.md`. |
| Related SRS Requirements | FR-DEV-* (Device/Camera onboarding, existing), FR-DET-* (Detection event identity, existing) — no new SRS requirement; this feature corrects an implementation gap against the existing "Camera is a Branch-scoped, admin-managed entity" model |
| Related Architecture Sections | §14.1 API table (new `GET /api/v1/device/configuration` endpoint, Device-authenticated, same header convention as credential validation, ARCH-001 §14.1), Device authentication (`X-Device-Id`/`X-Device-Secret`, unchanged) |
| Related ADRs | None new. Follows the existing Device-authentication convention (ARCH-001 §14.1) rather than introducing a new one. |
| Owner | Farhan Naeem |
| Dependencies | FS-02 (Branch/Device/Camera onboarding — delivered): `Camera` entity, `Camera.Name`/`Camera.RtspUrl`/`Camera.Enabled`. FS-06/IP-08 (DetectionEvent sync — delivered): `SyncEventsController`, `AlertSyncService`, `DetectionEventDto.CameraId`. IP-07 (DeepStream bridge — delivered): Bridge process, wire protocol, single-source pipeline builder. |
| Explicitly excluded | TensorRT engine path, YOLO model, parser, confidence threshold, NMS, tracker, inference interval, encoder/RTSP output, cooldown, snapshot settings (all remain local reviewed deployment config, IP-06/IP-10 territory). Branch timezone administration (separate, already-identified gap, not this feature). Snapshot JPEG correlation (FS-08, unresolved, not this feature). |

---

## 1. Purpose and Scope

Today, DeepStream's video source is a hardcoded `uri=` line in a statically deployed `deepstream-app.txt`-shaped
config file (`deployment/jetson/deepstream/deepstream-app.txt`), read by the Bridge's `config.py`. The Agent
attributes every detection to a single, static, Agent-wide `WDA_DETECTION_CAMERA_ID` string (default `"camera1"`,
`agent.env`) — never to the physical source that actually produced it, because the Bridge builds only one source
element per pipeline. On the Backend, `AlertSyncService.ResolveCameraIdAsync` then case-insensitively matches that
wire string against `Camera.Name` — an operator-editable display label — to derive the actual `Camera.CameraId`
GUID used everywhere downstream.

This is architecturally wrong for two independent reasons:

1. **Identity leaks through a mutable label.** Renaming a Camera in the Admin UI (`"Front Camera"` → `"Main
   Entrance"`) silently breaks detection attribution the next time an Agent restarts with a stale static id, or
   requires the static id to be kept in permanent lockstep with the display name — which defeats the purpose of a
   display name.
2. **One Agent can only ever represent one camera.** The Bridge's single-source pipeline and the Agent's
   single static camera id make multi-camera Devices structurally impossible today, even though `Camera.BranchId`
   already supports many Cameras per Branch and (implicitly) per Device.

This feature makes the Backend's `Camera` row the single source of truth for pipeline input, decouples
`Camera.Name` from identity, and extends the Bridge to build one DeepStream source per enabled Camera assigned to
the authenticated Device, batched through one `nvstreammux`, with a deterministic `source_id → Camera.CameraId`
mapping owned by the Agent.

```text
Activation Key configured
    ↓
Device activation succeeds (unchanged, FS-02)
    ↓
DeviceId and Device secret persisted (unchanged)
    ↓
Agent authenticates with Backend (X-Device-Id / X-Device-Secret, unchanged)
    ↓
GET /api/v1/device/configuration  (NEW)
    ↓
Backend returns this Device's assigned enabled Cameras (CameraId, StreamUrl, Enabled, SourceOrder)
    ↓
Agent validates response (schema, uniqueness, bounds)
    ↓
Agent persists last-known-good configuration (ConfigCache)
    ↓
Agent generates atomic DeepStream runtime configuration file
    ↓
Bridge starts one pipeline containing all enabled sources
    ↓
nvstreammux batches frames from those sources (does NOT merge them into one image)
    ↓
Bridge reports numeric source_id per detection (unchanged wire shape)
    ↓
Agent maps source_id → immutable Camera.CameraId using the applied configuration generation
    ↓
DetectionEvent.camera_id = Camera.CameraId (GUID, as a string) — no longer a static Agent-wide setting
    ↓
Backend parses CameraId as a GUID, verifies Branch ownership — no Name matching
    ↓
Backend creates Alert for the correct Camera
```

## 2. Camera Identity Model (frozen)

| Concept | Meaning | Mutability | Pipeline effect |
|---|---|---|---|
| `Camera.CameraId` | Immutable Backend database identifier (existing GUID PK) | Never changes after creation | Authoritative identity in `DetectionEvent`/`Alert`. Never derived from `Name` or `StreamUrl`. |
| `Camera.Name` | Human-readable display label (existing `string`, max 200, already unconstrained free text) | Freely editable | **None.** Changing it must not restart the Bridge or change `configurationVersion`. |
| `Camera.RtspUrl` (exposed to the Agent as `streamUrl`) | Actual source URI DeepStream connects to (existing field, already free-form, may embed credentials) | Editable, pipeline-affecting | Changing it **must** change `configurationVersion` and trigger exactly one controlled Bridge restart. |
| `Camera.Enabled` | Whether this Camera's source is included in this Device's pipeline (existing `bool`) | Editable, pipeline-affecting | Same as `RtspUrl`. |
| `Camera.SourceOrder` (**new** field, this feature) | Explicit, stable, non-negative integer determining DeepStream `source_id` assignment | Editable, pipeline-affecting | Same as `RtspUrl`. Must be unique among a Device's *enabled* Cameras. Database return order is never relied upon. |

`SourceOrder` is a new nullable-at-the-domain-but-required-at-config-time `int` column on `Cameras`
(`WeaponDetection.Domain.Camera`), added via a new additive EF migration (`AddCameraSourceOrder`). It defaults to
`0` for every existing Camera row (including the production `camera1` row) via the migration's data-fixup step,
which is safe because there is currently at most one Camera per Branch in production. A partial unique index
enforces `(BranchId, SourceOrder)` uniqueness only where `Enabled = 1`, matching the "unique within the *enabled*
set" rule (mirrors the existing `ActivationKeys` partial-unique-index pattern, IP-01 §T-14).

## 3. `GET /api/v1/device/configuration`

Reuses the existing Device-authentication convention exactly (`X-Device-Id` / `X-Device-Secret`, `[AllowAnonymous]`
+ dedicated validator, ARCH-001 §14.1) — the same headers `DeviceCredentialValidationController` already
validates. A new `IDeviceConfigurationService` resolves the authenticated Device → Branch → its Enabled Cameras,
ordered by `SourceOrder`.

```json
{
  "schemaVersion": 1,
  "configurationVersion": "b6c1b6e0f2b94a...",
  "deviceId": "965032b6-26af-4506-81f9-2e7307290fa1",
  "branchId": "9b6796f7-b2f2-47f3-a2df-a06bf94c1345",
  "generatedAtUtc": "2026-07-30T12:00:00Z",
  "cameras": [
    {
      "cameraId": "2613b331-8783-4d51-903a-3e41a979a14c",
      "name": "Front Camera",
      "streamUrl": "rtsp://100.77.146.5:8554/camera1",
      "enabled": true,
      "sourceOrder": 0
    }
  ]
}
```

- Only enabled Cameras are returned (frozen contract choice — simpler than an `enabled:false` list the Agent must
  still filter; a disabled Camera is never pipeline-relevant, so omitting it keeps the Agent's validation surface
  smaller). Disabling a Camera therefore changes `configurationVersion` (it changes the returned set) exactly like
  any other pipeline-affecting edit.
- Cameras are returned ordered by `SourceOrder` ascending — deterministic, not insertion/PK order.
- Response contains **no** Device secret, JWT material, Activation Key, or Data Protection detail — the DTO has no
  member to carry them, following the same "no member to serialize" pattern as `BranchResponseDto`/
  `RegenerateActivationKeyResponseDto`.
- Invalid Device credentials → uniform 401 (identical envelope to `DeviceCredentialValidationController`'s
  failure path — indistinguishable from "no such device" per the existing non-enumeration rule).
- Data Protection unavailable while decrypting the Device's stored secret for auth → 503, matching the existing
  `DeviceAuthenticationUnavailable` contract already used by credential validation.
- A Device belonging to Branch A can never receive Branch B's Cameras — enforced by scoping the query to the
  authenticated Device's own `BranchId`, never a client-supplied one.

## 4. `configurationVersion`

An opaque, deterministic hash (not a raw `RowVersion`/timestamp) computed over exactly the pipeline-relevant
tuple of every enabled Camera, ordered by `SourceOrder`:

```text
configurationVersion = SHA256(
    for each enabled Camera ordered by SourceOrder:
        CameraId | StreamUrl | SourceOrder
)
```

`Camera.Name` is deliberately excluded from the hash input — renaming a Camera must never change
`configurationVersion`. `Enabled` is implicitly captured because the input set itself is "the enabled Cameras";
toggling `Enabled` changes which rows are hashed. This is a plain opaque string comparison on the Agent side (no
`ETag`/`If-None-Match` machinery — the project has no existing precedent for conditional-GET caching, and an
opaque version compared client-side is simpler and sufficient for a 30-second poll interval).

## 5. Agent: `DeviceConfigurationClient` and `DeviceConfigurationCoordinator`

A new `agent/src/weapon_detection_agent/configuration/` package, mirroring the existing `sync/` package shape
(`client.py`, `models.py`, `coordinator.py` in place of `worker.py`):

- **`DeviceConfigurationClient`** — sends `X-Device-Id`/`X-Device-Secret` (same constants as `sync/client.py`),
  parses/validates the response into a typed `DeviceConfiguration` value object, classifies the outcome
  (`Valid(config)` / `Unauthorized` / `Unavailable` / `Invalid(reason)` / `TransportFailure`), enforces a
  configured max camera count and max payload size before parsing further, and never logs a `streamUrl` (RTSP URLs
  may embed userinfo credentials, same rationale as `RtspUrlSanitizer` on the Backend side) or the Device secret.
- **`DeviceConfigurationCoordinator`** — a single lifecycle component (one instance, like
  `DetectionEventSyncWorker`), started only after `AgentRuntimeSupervisor` reaches `Operational`:
  1. load last-known-good configuration from `ConfigCache` (if present);
  2. if present, apply it (start Bridge) *before* the first network round trip — this is what makes an
     Agent restart during a Backend outage recover instantly instead of blocking on the network;
  3. fetch current configuration from the Backend;
  4. validate; on success, compare `configurationVersion` against the currently-applied one;
  5. unchanged → no-op (no Bridge restart);
  6. changed and valid → persist to `ConfigCache`, generate a new runtime config, restart the Bridge exactly once;
  7. invalid or unreachable → keep the current applied configuration running untouched, log a redacted
     validation/transport failure, back off exponentially (same backoff shape as `DetectionEventSyncWorker`);
  8. no cache and Backend unreachable on first-ever start → do **not** start the Bridge with an invented source;
     log a controlled `waiting_for_configuration` state and keep retrying;
  9. poll every `WDA_DEVICE_CONFIG_REFRESH_SECONDS` (default `30`, mirrors the existing settings-conventions
     style of `WDA_DETECTION_SYNC_INTERVAL_SECONDS`);
  10. stop cleanly (cancel the poll loop, no orphaned Bridge) on Agent shutdown, symmetric with the sync worker's
      shutdown handling.

A 401 from this endpoint never triggers local credential rotation or reactivation — it is handled identically to
a 401 from credential validation (log, leave `DeviceIdentity` untouched; only the existing reactivation workflow
may ever change `OperationalState`).

## 6. `ConfigCache` (repurposed, not replaced)

The existing singleton `ConfigCache(SingletonGuard, ConfigJson, UpdatedAt)` table (schema v6, currently unused —
"reserved for a future configuration-sync feature" per its own docstring) becomes that feature's storage. No new
table; `ConfigJson` gains its first real writer:

```json
{
  "schemaVersion": 1,
  "configurationVersion": "b6c1b6e0f2b94a...",
  "deviceId": "...",
  "branchId": "...",
  "receivedAtUtc": "...",
  "appliedAtUtc": "...",
  "cameras": [ { "cameraId": "...", "name": "...", "streamUrl": "...", "enabled": true, "sourceOrder": 0 } ],
  "pipelineHash": "b6c1b6e0f2b94a..."
}
```

`pipelineHash` is stored redundantly alongside `configurationVersion` (identical value today) so a future change
to what counts as "pipeline-relevant" doesn't require a schema migration — only a new hash. Writing goes through
the existing `open_connection`/transaction pattern already used by every other repository; no new SQLite table,
no schema-version bump.

## 7. Generated DeepStream runtime configuration

The repository's committed `deployment/jetson/deepstream/deepstream-app.txt` remains the **reviewed static
template** for every non-Camera setting (model, tracker, inference interval, confidence, parser, NMS, encoder,
decoder/memory/timeout settings) — this feature never edits it in place. The Agent generates a new file at
`/opt/weapon-detection/runtime/deepstream.generated.conf` by:

1. reading the static template once at startup (never re-parsed per refresh unless the file itself changes,
   which this feature does not do);
2. removing its single `[source0]` section;
3. emitting one `[sourceN]` section per enabled Camera, `N = SourceOrder`, `type=4` (RTSP), `uri=StreamUrl`,
   `enable=1`, preserving `live-source=1` and every other existing per-source setting the template already sets;
4. setting `[streammux] batch-size=<enabled camera count>`, leaving every other streammux setting (buffer pools,
   memory type, timeouts) untouched from the template;
5. writing to a temp file in the same directory, `fsync`, then an atomic `rename` onto the final path (same
   pattern already used by `set-activation-key.sh` for the Activation Key file);
6. applying `weapon-detection:weapon-detection` ownership and the same restrictive mode as the rest of
   `config/`.

No `streamUrl` value is ever written to the Agent's own log output during generation (only camera count and
`configurationVersion` are logged).

## 8. Bridge: multi-source pipeline

`pipeline.py`'s `_parse_source`/`_create_source_element`/`_on_source_pad_added` single-source assumptions are
extended to a bounded list:

- one `nvurisrcbin` (or equivalent decode bin) per parsed `[sourceN]` section;
- one shared `nvstreammux`, one `sink_%u` request pad requested per source, pad index == that source's `N`;
- `streammux.batch-size` set from the generated config (§7), matching the enabled camera count exactly;
- exactly one `nvinfer`, one OSD/output branch, one encoder — unchanged, shared across the batch, as DeepStream's
  `nvstreammux`→`nvinfer` design already intends (`nvstreammux` batches frames into one buffer; it does not merge
  images, and this feature does not change that model);
- a camera that fails to connect (RTSP timeout) does not block pad-added callbacks for the others — each source
  bin's async state change is independent; `nvurisrcbin`'s own reconnect behavior (already relied on today for
  the single-camera case) continues to apply per-source;
- configuration changes are applied via a **controlled full Bridge restart** (stop → wait for exit → atomically
  install new generated config → start new Bridge), not in-process hot-plug of `GstElement`s into a running
  pipeline — hot-plugging is explicitly out of scope for this increment (Phase 11 note in the task brief) since
  the existing Bridge has no supervised mechanism for it and inventing one is disproportionate to this feature's
  goal.

## 9. `source_id` → `Camera.CameraId` mapping (Agent-owned)

The Bridge's wire protocol is unchanged (§ "5. Bridge detection protocol" of the investigation — still only a
numeric `sourceId`, no camera identity). The Agent owns the translation, keyed by an **applied-configuration
generation**:

- each time the coordinator applies a new configuration (§5 step 6), it builds an in-memory
  `{source_id: Camera.CameraId}` dict from that configuration's `SourceOrder`s and assigns it a monotonic
  `generation` counter;
- the running Bridge process is associated with exactly one generation at a time;
- `DetectionIngestHandler` resolves `source_id` through the *currently applied* generation's mapping only;
- an unknown `source_id` (out of range for the current generation) is rejected safely (logged, not
  forwarded as a `DetectionEvent`) rather than raising;
- to prevent a race during a Bridge restart: the old Bridge process is fully stopped and reaped *before* the new
  generation's mapping is installed and the new Bridge is started — so no detection is ever interpreted against
  the wrong generation's mapping, because only one Bridge process (hence one generation) is ever alive at a time.

`DetectionEvent.camera_id` becomes the resolved `Camera.CameraId` (as a string GUID) instead of the static
`WDA_DETECTION_CAMERA_ID` setting; that setting is removed once this feature is enabled (kept only as the
feature-disabled fallback, §11).

## 10. Detection sync contract migration (Backend)

`DetectionEventDto.CameraId` / `SyncEventsRequest` shape is unchanged at the wire level (still a `string`) —
only its *meaning* changes, from "match against `Camera.Name`" to "parse as `Camera.CameraId` GUID directly."
`AlertSyncService.ResolveCameraIdAsync` is replaced with a direct `Guid.TryParse` + `Cameras.SingleOrDefault(c =>
c.CameraId == parsed && c.BranchId == branchId)` lookup — an unrelated Branch's Camera GUID is rejected exactly
like an unknown Camera is today (no Alert created, event outcome `rejected`). A transitional flag
(`SyncEventsController` accepts either a GUID-shaped or legacy name-shaped `cameraId` for one deprecation window,
logging a `legacy_camera_name_resolution_used` warning on the fallback path) lets an already-deployed legacy Agent
keep working until every Agent is confirmed migrated; the fallback is removed in a follow-up task once telemetry
shows zero legacy usage. No historical `Alert`/`DetectionEvent` row is ever rewritten.

## 11. Rollout — feature flag

`WDA_DEVICE_CONFIG_ENABLED` (default `false`) gates all of this at the Agent: when `false`, the Agent behaves
exactly as it does today (static `deepstream-app.txt`, static `WDA_DETECTION_CAMERA_ID`, no coordinator started).
This lets the code ship and pass every automated/isolated test without touching the currently-running production
pipeline. Enabling it is a separate, explicitly-approved deployment step (task brief Phase 22), never bundled
into this feature's code-complete milestone.

## 12. Acceptance Criteria

1. Agent fetches Camera configuration only after Device identity + credential validation are both valid.
2. `Camera.Name` is never read by any pipeline-identity code path (Bridge, source_id mapping, DetectionEvent
   construction, Backend Alert resolution).
3. `Camera.RtspUrl`/`streamUrl` is the sole driver of DeepStream source creation.
4. Exactly one DeepStream source per enabled Camera; `streammux.batch-size` equals that count exactly.
5. Every `source_id` reported by the Bridge resolves, for the lifetime of one Bridge process, to exactly one
   immutable `Camera.CameraId`.
6. A `Camera.Name`-only edit changes neither `configurationVersion` nor triggers a Bridge restart.
7. A `Camera.RtspUrl`/`Enabled`/`SourceOrder` edit changes `configurationVersion` and triggers exactly one
   controlled Bridge restart.
8. Backend-unreachable Agent restarts serve the last-known-good `ConfigCache` entry and keep the Bridge running.
9. One genuine single-camera detection reaches the correct Alert via the immutable `CameraId` path.
10. Multi-camera source mapping is proven with no cross-assignment between two concurrently configured Cameras.
11. FS-06 (sync/idempotency), FS-07 (Data Protection), FS-09 (quota), FS-08 (snapshot-disabled) automated test
    suites remain green, unmodified in intent.
