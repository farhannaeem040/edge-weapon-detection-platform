# Feature Specification: Snapshot Evidence Capture and Upload

| Field | Value |
|-------|-------|
| Feature ID | FS-08 |
| Title | Capture one annotated post-OSD JPEG per accepted DetectionEvent and upload it durably to the matching Backend Alert |
| Status | Draft — awaiting approval; no production capture/upload enabled |
| Related Architecture Sections | §13.5/§20.2 (Backend snapshot storage, completeness guarantee — frozen), §14.1 (`POST /api/v1/alerts/{id}/snapshot`, frozen endpoint/auth), ADR-008 (filesystem layout), ADR-009 (API conventions — binary/multipart exceptions), ADR-011 (filesystem snapshot storage, no SQL BLOB) |
| Owner | Farhan Naeem |
| Dependencies | IP-07 (Bridge/probe/transport), IP-08 (DetectionEvent sync, live in production), IP-09 (Data Protection persistence, live in production) |
| Explicitly excluded | RTSP/OSD encoder work; tracker/inference/confidence/NMS changes; YOLO26; full video upload; Angular UI; alert status transitions; sirens; backfilling existing Alerts/DetectionEvents |

---

## 1. Purpose and Scope

Closes the last gap ARCH-001 §20.2 calls the "completeness guarantee" (an alert should carry its
evidence) for **new** detections only. Metadata sync (IP-08) already creates `Alert` rows with
`SnapshotReference=NULL`; this feature captures one annotated JPEG per Agent-accepted event and
uploads it to the matching Alert, leaving `SnapshotReference` non-null once durable.

## 2. Corrected Pipeline Topology (verified against the real deployed Bridge, not assumed)

The task brief's assumed pipeline (`nvinfer → nvdsosd → Bridge captures metadata`) is slightly
inaccurate: the **existing metadata probe sits on `nvdsosd`'s sink pad** (pure `NvDsObjectMeta`,
before rendering — IP-07 T-88, `pipeline.py::_attach_probe`), not on any post-OSD pixel data. The
post-OSD annotated frame exists only on `nvdsosd`'s **src** pad, flowing into either the RTSP-out
branch (`nvvidconv2 → nvv4l2h264enc → …`) or a `fakesink`, selected by `cfg.rtsp_out.enabled`
(`pipeline.py::build`). This is confirmed via direct inspection of the deployed
`deployment/jetson/deepstream/bridge/app/deepstream_bridge/pipeline.py`, not assumed.

```text
nvinfer → [nvtracker, disabled] → nvvideoconvert → nvdsosd
                                                       │ sink-pad probe (existing, unchanged):
                                                       │   raw NvDsObjectMeta → UDS → Agent
                                                       ▼
                                                   src pad
                                                       │
                                               NEW: tee ("osd-tee")
                                          ┌────────────┴────────────┐
                                   existing branch              NEW snapshot branch
                            (RTSP-out or fakesink,          (queue leaky=downstream →
                             byte-for-byte unchanged)         valve → nvjpegenc → appsink)
```

## 3. Design Decision — Design B, with a buffer-safety refinement (Phase 3 spike, real hardware)

**Spike findings** (`gst-inspect-1.0` on the production Jetson): `nvdsosd`, `tee`, `queue`, `appsink`,
`valve`, `nvvideoconvert` all present; **`nvjpegenc` present and hardware-accelerated** (rank 266,
"primary + 10") — the preferred encoder over software `jpegenc`.

**Design A (raw-`GstBuffer` retention until the Agent's async accept/reject) rejected.** Both designs
share the same core problem: the Agent's accept/suppress/reject decision arrives asynchronously,
after the corresponding post-OSD buffer has already flowed past the tee point. Design A proposes
holding the raw NVMM `GstBuffer` itself (pool-managed hardware memory with pipeline-tied refcounting)
across that async round trip. On a pipeline that has already had two prior stale-frame/box-artifact
incidents (T-92, T-94) from more mundane causes, adding NVMM buffer-pool lifetime extension as a new
failure mode is not an acceptable risk for this feature.

**Design B selected, refined:** the new tee branch **encodes every detection-bearing candidate frame
to JPEG immediately** (cheap — hardware encoder) and holds only the resulting **plain `bytes`** in a
small, bounded, TTL-expiring Python dict keyed by `frame_number` — never a `GstBuffer`. On the
Agent's accept response, the matching cached JPEG bytes are written to the spool; on suppress/reject/
TTL-expiry, the entry is simply dropped (no GStreamer object ever outlives its normal flow). This
still satisfies "no snapshot file persisted per raw detection" (only accepted candidates ever reach
disk) while eliminating buffer-pool lifetime risk entirely — the tradeoff is transient hardware-encode
work for candidates that turn out suppressed/rejected, bounded by the frame cache's own small size and
short TTL (§9).

**Gating which frames are even candidates.** Not every frame reaches the JPEG stage — a `valve`
(default `drop=True`) sits before `nvjpegenc`; the existing sink-pad metadata probe (already walking
`NvDsObjectMeta` for the unchanged detection-event path) additionally records the current
`frame_number` into a small shared set of "candidate frame numbers" whenever a frame has at least one
raw detection. A second, minimal probe on the tee's snapshot-branch queue pad reads that set and
briefly opens the valve (`drop=False`) for exactly the matching buffer, then closes it again — so only
frames that had *some* raw detection are ever JPEG-encoded, never every camera frame.

## 4. Bridge↔Agent Protocol v2 (breaking change, versioned, backward-compatible rollout)

### 4.1 Why the transport layer itself must change

The current `TransportWorker` (`deployment/…/bridge/app/deepstream_bridge/transport.py`) is
**send-only** — a dedicated thread drains a queue and calls `sock.sendall()`; it never reads from the
socket at all. `DetectionIngestHandler` on the Agent side is symmetrically receive-only. Implementing
an acknowledgement round trip is not a message-schema change alone; it requires the Bridge's transport
worker to also **read** framed responses off the same persistent connection (a second internal
reader loop feeding a callback keyed by `messageId`), and the Agent's ingest handler to **write** a
framed response after persisting each `DetectionEvent` row. This is called out explicitly because the
task brief's Phase 4 undersells it as "extend the framed protocol" — it is a bidirectional-transport
change on both ends, not just a payload-schema addition.

### 4.2 Versioning and compatibility

`schema_version` in the detection message becomes `2`. The Agent's validator must still accept
`schema_version: 1` messages from an un-upgraded Bridge (no acknowledgement expected/sent for those —
pure backward compatibility, no snapshot candidate is ever generated for a v1 message since the
Bridge itself won't have the tee/valve/cache logic yet). This lets Bridge and Agent be redeployed in
either order without a hard synchronization requirement, though the rollout plan (§13) deploys Agent
first (harmless no-op extension) then Bridge.

### 4.3 Detection message (Bridge → Agent, v2)

```json
{
  "schemaVersion": 2,
  "messageId": "b7e2...-uuid",
  "sourceId": 0,
  "frameNumber": 12345,
  "classId": 0,
  "confidence": 0.91,
  "boundingBox": { "left": 420.0, "top": 180.0, "width": 250.0, "height": 190.0 }
}
```

`messageId` is a Bridge-generated UUID, one per raw detection message — purely a correlation handle
for the acknowledgement, never used as (and never trusted as) `EventId`. Camelcase to match the
existing FS-06 wire convention (the Bridge/Agent detection-metadata wire has used snake_case
historically per FS-05 §5; **this is a deliberate, documented deviation for the new fields only** to
keep the acknowledgement schema visually distinct from the legacy v1 fields during the compatibility
window — the existing v1 snake_case fields are unchanged for v1 messages).

### 4.4 Acknowledgement (Agent → Bridge, new)

```json
{"messageId": "b7e2...-uuid", "outcome": "accepted", "eventId": "3f9c...-uuid", "snapshotRequired": true}
{"messageId": "b7e2...-uuid", "outcome": "suppressed", "snapshotRequired": false}
{"messageId": "b7e2...-uuid", "outcome": "rejected", "snapshotRequired": false, "errorCode": "UNKNOWN_CLASS"}
```

Rules (binding):
- The Agent remains the **sole** cooldown authority — the Bridge never suppresses on its own.
- The Bridge never generates `EventId`; it only learns it from an `accepted` acknowledgement.
- The Agent sends the acknowledgement **only after** the `DetectionEvent` row is durably committed to
  SQLite — never speculatively before persistence succeeds.
- A missing/malformed/late acknowledgement causes the candidate frame-cache entry to expire (§9) —
  never a pipeline fault, never a raised exception into the pad-probe thread.
- An acknowledgement naming an unknown `messageId` (already expired, or never a candidate — e.g. the
  frame had zero detections) is ignored safely, not logged as an error.
- A duplicate acknowledgement for an already-processed `messageId` is a no-op (idempotent) — it must
  never write a second JPEG file.
- The Agent's write-side of this exchange must be non-blocking with respect to
  `DetectionIngestHandler`'s existing accept/validate/cooldown/persist path — the acknowledgement is a
  cheap, bounded, best-effort send after persistence, not a new synchronous dependency the ingest path
  waits on beyond its own already-completed work.

## 5. Snapshot File Contract (Jetson spool)

- Format: JPEG (`image/jpeg`), quality ~85 (configurable), source: the post-OSD frame at native
  camera resolution (no forced resize).
- One file per accepted `EventId`: `<EventId>.jpg`, filename derived from `EventId` only — no class
  name, camera name, or any user/model-controlled text in the path (defends against path/log
  injection from a compromised or buggy inference pipeline).
- Spool root: `WDA_SNAPSHOT_SPOOL_PATH` (default `/opt/weapon-detection/snapshots/pending`), owned by
  the `weapon-detection` service identity, directory mode `0750`, file mode `0640`.
- Write discipline: temp file in the same directory, `fsync`, atomic `rename` to the final
  `<EventId>.jpg` — never a partially-written file visible under its final name.
- Before a captured file is accepted into `SnapshotOutbox` (§6), the Agent validates: JPEG magic
  bytes, decodable dimensions (non-zero width/height), size ≤ `WDA_SNAPSHOT_MAX_FILE_BYTES`, and a
  computed SHA-256 (recorded, never trusted from the Bridge — the Bridge only hands raw JPEG bytes
  across the UDS as part of the existing framed protocol's body, using a second, larger frame type
  distinguished by a `kind` discriminator, since JPEG bytes exceed the existing 64 KiB detection-frame
  cap; see IP-10 for the exact framing extension).
- Image bytes are **never** written to SQLite (ADR-004/ADR-011 discipline, matching `DetectionEvent`'s
  existing filesystem-reference-only pattern).

## 6. Agent `SnapshotOutbox` Schema (new SQLite table, additive migration)

```sql
CREATE TABLE SnapshotOutbox (
    EventId          TEXT PRIMARY KEY REFERENCES DetectionEvent(EventId),
    LocalPath        TEXT NOT NULL,
    CaptureStatus    TEXT NOT NULL CHECK (CaptureStatus IN ('captured', 'capture_failed')),
    UploadStatus     TEXT NOT NULL CHECK (UploadStatus IN ('pending', 'uploaded')) DEFAULT 'pending',
    BackendAlertId   TEXT NULL,
    ContentType      TEXT NOT NULL,
    SizeBytes        INTEGER NOT NULL,
    Sha256           TEXT NOT NULL,
    CapturedAtUtc    TEXT NOT NULL,
    UploadedAtUtc    TEXT NULL,
    AttemptCount     INTEGER NOT NULL DEFAULT 0,
    LastAttemptAtUtc TEXT NULL,
    LastErrorCategory TEXT NULL
);
CREATE INDEX idx_snapshot_outbox_upload_status_captured_at_utc
    ON SnapshotOutbox (UploadStatus, CapturedAtUtc);
```

`permanent_failure` is deliberately **not** an approved `UploadStatus` value for this increment — the
task's own minimal-state-model instruction and the absence of any operator-facing surface to act on a
permanently-failed snapshot (no Angular UI this feature) mean a stuck row simply stays `pending` and
keeps retrying with capped backoff; this is revisited only if a future feature adds an operator view.

`EventId` is a foreign key into the **active** `DetectionEvent` table only — `DetectionEvent_archive_
20260728` rows are never referenced, and no code path in this feature ever queries the archive table
(§14 excludes it explicitly).

## 7. Ordering Independence (metadata sync vs. snapshot capture)

Both orderings are valid and must both work, since metadata sync (existing `DetectionEventSyncWorker`)
and snapshot capture (new) are independent async processes reading the same `DetectionEvent` row:

- **Capture-first:** `SnapshotOutbox` row created with `BackendAlertId=NULL`; the snapshot upload
  worker skips rows with a null `BackendAlertId` (§12) until `AlertSyncService`'s
  accepted/duplicate response is persisted back onto the row.
- **Metadata-first:** `DetectionEventSyncWorker` already has the `AlertId` before the JPEG exists;
  the association is written to `SnapshotOutbox` only once that row exists (created by the capture
  path) — if no `SnapshotOutbox` row exists yet, the `AlertId` is discardable/re-derivable (the
  Backend response for accepted/duplicate is idempotent by `EventId`), not something the metadata
  worker must itself buffer.
- A metadata sync **rejected** outcome for an `EventId` means: no Alert exists, so no snapshot is ever
  uploaded for it — a `SnapshotOutbox` row in `pending` state with a permanently-null `BackendAlertId`
  simply never drains (bounded, low-volume — rejections are rare per FS-06's existing design) and is
  accepted as the documented behavior for this increment.

## 8. Backend Snapshot Storage

`IAlertSnapshotStorage` abstraction, production implementation `FileSystemAlertSnapshotStorage`,
backed by a **new**, separate named Docker volume `alert-snapshots` (distinct from
`dataprotection-keys`), mounted only into `backend` at `/var/lib/weapon-detection/alert-snapshots`,
pre-chowned for the non-root `app` user in the Dockerfile (mirrors FS-07 §3.2's exact pattern — the
identical class of bug FS-07 fixed for Data Protection keys is pre-empted here by construction, not
rediscovered).

Generated, server-side paths only (`{AlertId}.jpg`, no user-controlled path segment); `SnapshotReference`
returned to the Agent is an opaque key (`{AlertId}.jpg` or an API-relative path), never a filesystem
path. Atomic temp-then-rename write, matching §5's Agent-side discipline.

## 9. Backend Upload Endpoint

`POST /api/v1/alerts/{alertId}/snapshot` — the architecturally frozen route (ARCH-001 §14.1).
Device-authenticated (`X-Device-Id`/`X-Device-Secret`, the existing validator, extended identically to
how FS-06's `SyncEventsController` reused it — including the FS-07 `CredentialStorageUnavailable` →
`503` branch, unchanged). Multipart body: `file`, `eventId`, `sha256`. The server **recomputes**
SHA-256 server-side and never trusts the client-supplied value for integrity — the client value is
compared only as an early-reject sanity check.

Validation order: auth → Alert exists and belongs to the authenticated Device (via `BranchId`, same
join pattern as FS-06 §6.3) → `eventId` matches `Alert.EventId` → size/content-type/dimension bounds →
decode as real JPEG → store.

Outcomes: `accepted` (first successful upload, `SnapshotReference` set), `duplicate` (identical bytes,
same SHA-256, re-upload — idempotent, no second file), named **conflict** (different bytes for an
Alert that already has a snapshot — reject, preserve the existing one, never silently overwrite,
mirroring FS-06 §5.2's `EVENT_DATA_CONFLICT` precedent). HTTP mapping: `201`/`200` accepted, `200`
duplicate, `400` malformed, `401` invalid credentials, `403`/`404` per the existing non-disclosure
policy for a foreign Alert, `409` conflict, `413` oversized, `415` unsupported media, `503`
`DEVICE_AUTHENTICATION_UNAVAILABLE`, `500` unexpected only.

## 10. Backend `Alert` Schema Extension (additive migration)

Add `SnapshotSha256`, `SnapshotContentType`, `SnapshotSizeBytes`, `SnapshotReceivedAtUtc` (all
nullable) alongside the existing nullable `SnapshotReference` — no destructive change, every existing
`Alert` row (594 in production as of this feature's start, all `SnapshotReference=NULL`) remains
valid. No backfill.

## 11. Agent `SnapshotUploadClient` / `SnapshotUploadWorker`

Mirrors `BackendSyncClient`/`DetectionEventSyncWorker`'s established structure exactly (FS-06 §7.3/
§7.4) — same header constants, same typed-failure-not-exception discipline, same
`OperationalComponent` pattern, same two-layer kill switch
(`WDA_SNAPSHOT_CAPTURE_ENABLED`/`WDA_SNAPSHOT_UPLOAD_ENABLED`, both default `false`). Streams the file
from disk (never loads an unbounded image fully into a single in-memory buffer beyond what
`httpx`'s multipart streaming already requires). Never logs secrets, image bytes, or full paths —
truncated `EventId`/`AlertId`, byte counts, HTTP status only.

## 12. Lifecycle Ordering

`DetectionIngestHandler → DeepStream Bridge → DetectionEventSyncWorker → SnapshotUploadWorker` at
startup; reverse at shutdown. Backend/storage failures never gate detection or metadata-sync startup.
Disabled settings construct no capture/upload components at all (existing kill-switch idiom, IP-07
§9/IP-08 §10).

## 13. Rollout Compatibility

Stage 1 (this feature's "production boundary," §21 of the task brief): Backend + Agent + Bridge all
deployed with `WDA_SNAPSHOT_CAPTURE_ENABLED=false`/`WDA_SNAPSHOT_UPLOAD_ENABLED=false` — the Bridge's
v2 protocol and tee/valve/cache logic exist but produce no candidates while capture is disabled (the
candidate-frame-number recording and valve-opening logic itself is also gated by the same flag,
passed to the Bridge via its own config, so a disabled deployment has zero behavioral change to the
existing metadata/RTSP paths beyond the inert presence of an always-closed valve). Stage 2 (separate
gate) flips both flags together after isolated validation passes.

## 14. Known Limitations

- No dead-letter/operator view for `permanent_failure` rows this increment (§6).
- Camera-name-based EventId→Alert association reuses FS-06 §6.3's existing convention; no new
  camera-mapping concern is introduced by this feature.
- Historical archived events (`DetectionEvent_archive_20260728`) and existing Alerts are permanently
  excluded from this feature's scope — no backfill mechanism is built, by design.
