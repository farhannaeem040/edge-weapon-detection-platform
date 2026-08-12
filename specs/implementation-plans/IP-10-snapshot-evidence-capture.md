# Implementation Plan: Snapshot Evidence Capture and Upload

| Field | Value |
|-------|-------|
| Plan ID | IP-10 |
| Title | Post-OSD snapshot capture (Bridge), durable spool + upload (Agent), storage + endpoint (Backend) |
| Status | Draft — awaiting approval; no production capture/upload enabled |
| Realizes | FS-08 |
| Task ID Range | **T-131 – T-165** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Everything FS-08's header lists. Production activation (Stage 2) requires a separate approval gate after this plan's isolated validation passes. |

---

## 1. Task Breakdown

### Bridge (Python 3.8, `deployment/jetson/deepstream/bridge/`)

| Task | Description |
|---|---|
| T-131 | Protocol v2: `messageId` field, `schemaVersion=2`; a second, larger-cap frame `kind` (`detection`/`snapshot`) so JPEG bytes can share the same framed connection without raising `MAX_FRAME_BYTES` for ordinary detection messages. Backward-compat: v1 messages still parse. |
| T-132 | `TransportWorker` becomes bidirectional: an internal reader loop (separate from the existing sender thread) parses framed acknowledgements and invokes a caller-supplied callback keyed by `messageId`. Existing send-path behavior (drop-on-full-queue, drop-on-send-failure, bounded/aggregated logging) unchanged. |
| T-133 | Candidate frame-number tracking: extend the existing sink-pad metadata probe to record `frame_number` into a small bounded set when a frame has ≥1 raw detection; a second probe on the snapshot branch's queue reads it to gate the `valve`. |
| T-134 | Pipeline: add `tee` after `nvdsosd`; existing RTSP-out/fakesink branch re-attached to the tee's first pad, byte-for-byte unchanged; new branch `queue(leaky=downstream, max-size-buffers=WDA-equivalent Bridge config)` → `valve` → `nvjpegenc` → `appsink`. |
| T-135 | `SnapshotCandidateCache`: bounded dict (`max_retained_frames`, TTL), pure Python, holds JPEG `bytes` only — never `GstBuffer`. Encode happens on the appsink callback thread (never the pad-probe thread); JPEG bytes handed to the cache. |
| T-136 | Wire the acknowledgement callback: on `accepted` with `snapshotRequired=true`, look up the cached candidate by the messageId→frame_number mapping, send the JPEG frame (`kind=snapshot`) to the Agent; on `suppressed`/`rejected`/timeout, drop the entry. |
| T-137 | Config: `[bridge-snapshot]` section (`enabled`, `frame_ttl_ms`, `max_retained_frames`, `jpeg_quality`), gated two-layer kill switch identical to the RTSP-out section's existing pattern. |
| T-138 | Bridge tests (FS-08 §Phase 15 list) — fabricated `pyds`/`Gst` stubs for the pure logic; real-hardware proof deferred to T-158 (Phase 19). |

### Agent (Python 3.11, `agent/`)

| Task | Description |
|---|---|
| T-139 | `detection/protocol.py`/`validation.py`: accept v1 and v2 messages; generate the acknowledgement after `DetectionEventRepository.insert` succeeds (never before); write it back over the same UDS connection. |
| T-140 | Receive `kind=snapshot` frames from the Bridge (JPEG bytes + `EventId` correlation via the prior acknowledgement) — write to spool via temp-file+fsync+atomic-rename; validate JPEG signature/dimensions/size before creating the `SnapshotOutbox` row. |
| T-141 | SQLite migration v4→v5: `SnapshotOutbox` table (FS-08 §6) + its index. Reuses the exact idempotent single-transaction migration pattern already established (`schema.py`). |
| T-142 | `SnapshotOutboxRepository`: create-captured-row, associate-`BackendAlertId`, list-upload-ready-oldest-first, mark-uploaded, bounded-attempt-increment, redacted-error-record. Never deletes a pending row silently. |
| T-143 | Wire `AlertSyncService`'s accepted/duplicate `AlertId` (already returned by the existing `BackendSyncClient`/`DetectionEventSyncWorker`, FS-06) into `SnapshotOutboxRepository.associate_alert_id` — no change to `DetectionEventSyncWorker`'s own delivered-marking semantics. |
| T-144 | `SnapshotUploadClient` (mirrors `BackendSyncClient`). |
| T-145 | `SnapshotUploadWorker` (mirrors `DetectionEventSyncWorker`) — drain loop, retry/backoff, delete-local-file-only-after-uploaded-commit. |
| T-146 | `WDA_SNAPSHOT_*` settings (FS-08/task brief Phase 13 list) + validation. |
| T-147 | Lifecycle wiring: `default_snapshot_components_factory`, appended after the sync factory in `main.py`. |
| T-148 | Startup reconciliation: a captured-but-unpersisted-row crash-recovery check (file exists, no matching `SnapshotOutbox` row → orphan, safe to ignore/clean per a documented policy) and a persisted-row-but-missing-file check (record `capture_failed`, never crash). |
| T-149 | Disk-quota enforcement: skip capture (not detection) when `WDA_SNAPSHOT_MAX_SPOOL_BYTES` would be exceeded; rate-limited warning; never crash the Agent. |
| T-150 | Agent tests (FS-08/task brief Phase 16 list, 28 items). |

### Backend (.NET, `backend/`)

| Task | Description |
|---|---|
| T-151 | `IAlertSnapshotStorage`/`FileSystemAlertSnapshotStorage`; `compose.yaml` new named volume `alert-snapshots`, mounted only into `backend`; `backend/Dockerfile` pre-chowns the mount point (mirrors FS-07 §3.2 exactly). |
| T-152 | `Alert` entity: add nullable `SnapshotSha256`/`SnapshotContentType`/`SnapshotSizeBytes`/`SnapshotReceivedAtUtc`. EF migration, additive only. |
| T-153 | `IAlertSnapshotUploadService`/`AlertSnapshotUploadService`: validation order (§9), SHA-256 recompute, conflict detection, idempotent duplicate handling. |
| T-154 | `AlertSnapshotUploadController` (or extend the existing Alerts area) — thin, delegates, uniform envelope, the FS-07 503 branch reused unchanged. |
| T-155 | DI registration. |
| T-156 | Backend tests (FS-08/task brief Phase 17 list, 28 items) — unit + real-SQL-Server integration, mirroring FS-06/FS-07's established test-harness conventions (`SqlServerApiHostFactory`, temp key/storage directories). |

### Verification and Isolated Validation

| Task | Description |
|---|---|
| T-157 | Full verification suite (FS-08/task brief Phase 18: build/test/vulnerable-scan/EF-check on Backend; full suite/ruff/format/mypy on Agent; full suite + Python 3.8 compat check on Bridge; `docker compose config`; build the Backend image, do not deploy). |
| T-158 | Isolated Jetson capture validation (Phase 19) — temporary DB/UDS/spool, no production data, real detection, real hardware proof of clean-frame/EventId correlation/FPS impact. |
| T-159 | Isolated end-to-end upload validation (Phase 20, Tests A–E) — isolated compose project + test SQL Server + test Device, mirroring IP-09's `dp-acceptance`-style isolated project pattern exactly. |
| T-160 | Production rollout report (Phase 21) — stop for approval. |
| T-161–T-165 | Stage 1 deploy (disabled) → verify no behavior change → Stage 2 gate → enable → trace one real event → 15-minute soak (Phase 22), each its own explicit approval checkpoint, matching IP-08/IP-09's established two-stage rollout discipline. |

## 2. Frozen Contract Reference

FS-08 §3–§12 is binding for all three implementers — the exact protocol frame shapes, cache
semantics, schema DDL, endpoint contract, and settings names must not be invented independently by
whichever agent/session implements each side.

## 3. Acceptance Criteria

Identical to the task brief's Phase-21/22 "production acceptance criteria" list — reproduced in
FS-08's own file is unnecessary; this plan defers to the task brief verbatim for that list to avoid
drift between two copies.
