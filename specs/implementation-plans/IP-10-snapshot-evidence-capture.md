# Implementation Plan: Snapshot Evidence Capture and Upload

| Field | Value |
|-------|-------|
| Plan ID | IP-10 |
| Title | Post-OSD snapshot capture (Bridge), durable spool + upload (Agent), storage + endpoint (Backend) |
| Status | **Stage B PROVEN (2026-08-12) — one genuine, fully-verified local JPEG produced end-to-end on the real device.** Three real GStreamer/Python defects found and fixed (candidate-tracker truthiness check, missing colorspace conversion before the hardware JPEG encoder, missing `async=false` on a rarely-fed appsink). A fourth, separate, pre-existing defect (an ACK-before-JPEG-cached race with no retry) causes intermittent capture and remains **unfixed** — out of this run's scope, documented as a follow-up. `WDA_SNAPSHOT_CAPTURE_ENABLED=true` left on (Stage B succeeded); `WDA_SNAPSHOT_UPLOAD_ENABLED=false` (Stage C not attempted, per instruction). See §5 for full evidence. |
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

---

## 4. Real-Device Investigation Record (2026-08-12)

Picking up from a prior session's code-only inspection, which reported the feature as "structurally
complete, sitting behind an OFF flag, with one arity defect." That inspection read only the
**repository's** source. This session discovered the **deployed Jetson code did not match the
repository at all** for the two files that matter most — a discovery that reframes everything that
inspection concluded.

### 4.1 Deployed-vs-repo drift (the real root cause of "unproven")

`probe.py`, `config.py`, `protocol.py`, and `transport.py` on the device matched the repo exactly.
**`pipeline.py` and `snapshot.py` did not** — the device was running an older pre-FS-11 version
(`pipeline.py`: 802 lines / 45 "snapshot" mentions vs. the repo's 838 lines / 59; `snapshot.py`:
wholesale different content, still using the older frame-number-only-adjacent design). A stray
`pipeline.py.bak-preT158fix` on the device confirms at least one prior partial fix attempt was made
directly on-device and never synced back to the repository, and the full FS-11-era dynamic per-camera
snapshot topology was apparently never deployed at all. **This — not a small arity bug — is why the
feature was "disabled and unproven": the code that was actually reachable on the hardware was
different from, and older than, what the repository's own passing test suite was validating.**

Full current bridge module set (`pipeline.py`, `snapshot.py`, `main.py`, `probe.py`, `config.py`,
`protocol.py`, `transport.py`, `cli.py`, `errors.py`, `__init__.py`) was synced from the repository to
the device, with a full pre-sync backup taken
(`/opt/weapon-detection/backups/fs08-full-bridge-sync-20260812T022105Z/`).

### 4.2 Defect 1 (found in prior session, confirmed fixed) — `on_candidate` arity

`probe.py`'s `handle_buffer` called `on_candidate(message_id, frame_number)` (2 args) against
`CandidateFrameTracker.record_detection(source_id, message_id, frame_number)` (3 args). Fixed by
threading `detection.source_id` through; regression test added (`test_probe.py`) that calls
`handle_buffer` with the *real* bound `CandidateFrameTracker.record_detection` method, not a
fabricated stand-in. 270→271 Bridge tests (before this session's second fix below).

### 4.3 Defect 2 (newly found this session) — `SnapshotCandidateCache.put` arity

Structurally identical bug, different call site: `pipeline.py`'s `_on_snapshot_new_sample` called
`self._snapshot_cache.put(frame_number, jpeg_bytes)` (2 args) against
`SnapshotCandidateCache.put(source_id, frame_number, jpeg_bytes)` (3 args). This would raise
`TypeError` on every successful JPEG capture, silently swallowed by the surrounding
`except Exception: _LOGGER.exception(...)` handler. Fixed (`pipeline.py`, 1 line); regression test
added (`test_pipeline_snapshot_construction.py`) that cross-checks the call against
`SnapshotCandidateCache.put`'s real signature via `inspect.signature`, not a fixed string. 271
Bridge tests passing.

### 4.4 Defect 3 (found, NOT resolved) — valve-gating probe never fires

With both defects above fixed and the correct code set deployed, capture was tested three times on
the real device (bounded, non-looping `gun-tester.mp4` → camera1, real DetectionEvents confirmed each
time). **Zero JPEGs, zero `SnapshotOutbox` rows, all three times.**

Diagnostic investigation (temporary, bounded `_LOGGER.info` instrumentation, added and fully reverted
after use — none remains in the deployed or repo code):

1. `[bridge-snapshot] enable=true` confirmed present in the live generated runtime config.
2. Per-camera snapshot branch confirmed **correctly constructed** at startup for both cameras:
   `DIAG_camera_output_snapshot_gate index=0 enabled=True tee_built=True` (and `index=1`, same).
3. `_attach_snapshot_branch` confirmed to run to completion and successfully attach the pad probe:
   `DIAG_snapshot_branch_attached src=0 probe_id=1 queue=snapshot-queue-0 valve=snapshot-valve-0
   jpegenc=snapshot-jpeg-encoder-0 appsink=snapshot-appsink-0` (and `src=1`, same) — a nonzero,
   valid GStreamer probe ID.
4. Despite (2) and (3), **`_on_snapshot_valve_probe` itself was never invoked** during a real,
   confirmed detection — zero `DIAG_valve_probe` log lines across three separate test runs, even
   though the same camera's RTSP output was actively and correctly streaming through the same tee at
   the same time.

This means the snapshot branch is topologically correct and successfully wired into the pipeline
graph, but no buffer ever reaches the queue's src pad where the probe is attached — a GStreamer
runtime behavior this session could not resolve without pipeline-graph introspection tooling
(`GST_DEBUG_DUMP_DOT_FILE` or equivalent) that goes beyond what's proportionate to add speculatively.
**This is the actual current BLOCKER for Stage B capture.**

### 4.5 What is/isn't proven

- **Proven:** Bridge code now matches the repo exactly (checksummed); both known arity defects are
  fixed with regression tests; YOLO26/NvDCF/RTSP outputs/DetectionEvents are completely unaffected by
  any of this investigation (regression-checked after every restart); production was returned to a
  clean, safe, capture-disabled baseline (`[bridge-snapshot] enable=false`,
  `WDA_SNAPSHOT_CAPTURE_ENABLED=false`) with zero residual `SnapshotOutbox` rows or spool files.
- **Not proven:** any JPEG has ever been produced by the current implementation on real hardware.
  Backend retrieval endpoint and frontend display work (Phases 9–13 of the task brief) were
  deliberately **not started**, per the task's own explicit instruction to stop at this exact point
  if capture doesn't work.

### 4.6 Recommended next step (superseded — resolved in §5)

The valve-probe non-firing needed GStreamer-level debugging on the device itself. §5 records exactly
that investigation and its resolution, continuing from this checkpoint in the same session.

---

## 5. Root Cause Resolution and Stage B Proof (2026-08-12, continued)

Picking up directly from §4.6. Per the task brief's primary hypothesis ("closed valve downstream of
the gating probe"), the actual code was inspected first: the gating probe is on the **queue's src
pad**, upstream of the valve, and sets `valve.drop` synchronously on the same buffer before the push
continues — the documented, correct GStreamer idiom for "let exactly this one buffer through." The
closed-valve-deadlock hypothesis was **not confirmed** — pad placement was already correct.

### 5.1 Real root cause #1 — candidate-tracker truthiness bug

`_on_buffer_probe` built the callback as:

```python
on_candidate = self._candidate_tracker.record_detection if self._candidate_tracker else None
```

`CandidateFrameTracker` defines `__len__` (so callers can check "any pending candidates"). Python
falls back to `len(obj) != 0` for `bool(obj)` when `__len__` is defined and `__bool__` is not. A
**freshly emptied** tracker — its normal resting state between detections, since entries are consumed
immediately on match — has `len() == 0`, so `if self._candidate_tracker:` evaluated **False** at
exactly the moment a new detection needed to register a candidate. `self._candidate_tracker is not
None` (identity) was always True; the bare truthiness check was not. `record_detection` was therefore
never actually called, confirmed by bounded diagnostic instrumentation on the real device: a genuine,
persisted `gun` detection occurred while `tracker_present=True` logged on every single buffer-probe
call, yet the candidate-registration closure never fired. **Fix:** `is not None` (identity), matching
every other candidate_tracker/snapshot_cache check already in the file. One line,
`pipeline.py::_on_buffer_probe`.

### 5.2 Real root cause #2 — missing colorspace conversion before the hardware JPEG encoder

With #1 fixed, the valve was confirmed (via a temporary diagnostic) to open correctly for the exact
candidate frame — but no JPEG still appeared. `gst-inspect-1.0 nvdsosd` on the real device shows its
src pad can negotiate NV12 **or RGBA**; `gst-inspect-1.0 nvjpegenc` shows its NVMM sink template only
accepts NV12/I420. The RTSP-out branch already handles this with its own `nvvideoconvert`
(`_attach_rtsp_out`'s `postconv`) before its encoder; the snapshot branch taps the tee immediately
after `nvdsosd`, before any such conversion, and fed nvjpegenc directly. (On this device nvdsosd
happened to already be negotiating NV12, so this specific mismatch wasn't the proximate blocker here —
but it is a real, latent defect: with OSD display properties that force RGBA, or on hardware/driver
combinations that negotiate differently, capture would silently fail exactly the same way. Fixed
regardless, since it's correct per the encoder's own documented contract.) **Fix:** insert
`nvvideoconvert -> capsfilter(video/x-raw(memory:NVMM), format=NV12)` between the valve and
`nvjpegenc`, placed *after* the valve so the conversion cost is only ever paid on the rare candidate
frame that gets through.

### 5.3 Real root cause #3 — appsink stalls without `async=false`

With #1 and #2 fixed, bisection probes (temporary, on every pad from the valve's src through
`nvjpegenc`'s src) proved the buffer flowed **all the way through the chain**, including a
successfully JPEG-encoded output at `nvjpegenc`'s src pad (`caps=image/jpeg, sof-marker=4,
width=1280, height=720...`) and successfully arriving at `appsink`'s own sink pad. Yet `appsink`'s
`"new-sample"` signal never fired. `GstAppSink` inherits `GstBaseSink`'s preroll/state-change
synchronization by default (`async=true`); a sink on a branch the valve keeps closed almost all the
time never receives a buffer during the pipeline's initial PAUSED→PLAYING preroll, so it can stall
waiting for that handshake instead of emitting `"new-sample"` as soon as the first (long-delayed)
real buffer finally arrives. **Fix:** `appsink.set_property("async", False)`. Confirmed immediately:
`"new-sample"` fired three times in the very next test.

### 5.4 Stage B — full proof

Bounded gun test (`gun-tester.mp4` → camera1, 15s, real PID, approved executable) with capture
enabled, upload disabled:

| Field | Value |
|---|---|
| EventId | `d4d54b5d-a81d-4f6a-b73f-317b03f43ef0` |
| source_id / message_id | `0` / `bc054019-6b79-49c0-9add-530f7585555d` |
| frame_number | `106` |
| CameraId | `2613b331-8783-4d51-903a-3e41a979a14c` (front-camera) |
| class / confidence | `gun` / `0.816` |
| Local JPEG path | `/opt/weapon-detection/snapshots/d4d54b5d-a81d-4f6a-b73f-317b03f43ef0.jpg` |
| JPEG validity | `file` reports "JPEG image data, baseline, precision 8, 1280x720, components 3" |
| JPEG dimensions | 1280×720 |
| JPEG size | 100,735 bytes |
| JPEG SHA-256 | `c49d62e92dd8f82ae5ef0866f714c42d48de3b57f150cce6fdf8b10fa0e4585b` |
| Post-OSD annotation | Confirmed visually — a red bounding box labeled `gun 1` around the visible weapon, correct camera content |
| SnapshotOutbox row | `CaptureStatus=captured`, `UploadStatus=pending`, `ContentType=image/jpeg`, `SizeBytes=100735`, `Sha256` matches the file exactly, `BackendAlertId` populated (via the pre-existing, unrelated detection-sync path) |

### 5.5 Known follow-up (not fixed this session, out of scope)

A repeat of the same bounded test **did not** reliably reproduce a second JPEG — `SnapshotOutbox`
stayed at one row across two further attempts. Root cause identified by reading
`SnapshotAcknowledgementHandler.__call__` (`snapshot.py`):

```python
event_id = message.get("eventId")
jpeg_bytes = self._snapshot_cache.pop(source_id, frame_number)
if not isinstance(event_id, str) or jpeg_bytes is None:
    return
```

If the Agent's acknowledgement arrives **before** the appsink has finished encoding and caching the
JPEG (`jpeg_bytes is None`), the handler silently drops the snapshot with no retry/wait — a real,
pre-existing architectural gap. FS-08's own original design language describes both "JPEG first" and
"ACK first" as valid orderings, but only "JPEG first" is actually implemented. This is almost
certainly why the feature never produced a snapshot in ordinary operation even after the flag was
flipped correctly in earlier attempts, and it explains this session's own intermittency (the
diagnostic-instrumented build, with extra logging latency in the capture path, coincidentally
succeeded; the clean/faster build raced the ACK more often). **Not fixed in this session** — it needs
a deliberate design decision (bound the wait, or re-order the check, or retry once) rather than a
one-line patch, and the task's explicit scope was "prove the root cause, fix it, produce one valid
JPEG," not "make capture 100% reliable." Flagged here as the next concrete task.

### 5.6 Files changed

- `deployment/jetson/deepstream/bridge/app/deepstream_bridge/probe.py` — `on_candidate` arity (3
  fixes carried from §4, unchanged).
- `deployment/jetson/deepstream/bridge/app/deepstream_bridge/pipeline.py` — truthiness fix (§5.1),
  `nvvideoconvert`/`capsfilter` insertion (§5.2), `appsink async=false` (§5.3), `put()` arity carried
  from §4.
- `deployment/jetson/deepstream/bridge/tests/test_pipeline_snapshot_construction.py` — regression
  tests for all of the above, including one that instantiates a real, empty `CandidateFrameTracker`
  and asserts `bool(tracker) is False` to document the exact trap.
- `deployment/jetson/deepstream/bridge/tests/test_probe.py` — unchanged from §4 (arity regression
  test calling the real bound method).

Bridge suite: **274 passed**, 12 skipped, 0 failed. Ruff clean.

### 5.7 Final device state

`WDA_SNAPSHOT_CAPTURE_ENABLED=true` (Stage B proven, left on per the staged-rollout policy),
`WDA_SNAPSHOT_UPLOAD_ENABLED=false` (Stage C explicitly out of scope this run). Agent/Bridge:
`NRestarts=0` for the entire session from first deploy through final test. YOLO26/NvDCF/RTSP outputs
all reconfirmed healthy after every restart. `SnapshotOutbox` holds exactly the one proof row; the
corresponding JPEG remains on disk as evidence. No test publisher left running (Windows or Jetson).
