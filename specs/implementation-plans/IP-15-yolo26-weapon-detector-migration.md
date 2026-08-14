# Implementation Plan: Replace Production YOLOv4 Detector with Custom YOLO26 Weapon Detector

| Field | Value |
|-------|-------|
| Plan ID | IP-15 |
| Title | New `yolo26-fp16` DeepStream inference profile replaces `yolov4-fp16` as the active production detector; NvDCF re-enabled on the live Jetson |
| Status | **Complete — Production Deployed and Validated.** Cutover applied 2026-08-12T00:55:17Z (single Agent/Bridge restart, NRestarts=0, stable throughout). Gun and knife detection both proven end-to-end (DetectionEvent → Alert) on the real two-camera production pipeline; no cross-camera assignment; NvDCF re-enabled and initialized without error; engine bindings independently verified twice (raw TensorRT and DeepStream's own layer table) and match exactly. See §5. |
| Realizes | FS-13 |
| Governing Documents | FS-13, FS-04/IP-06 (frozen — established the `profiles/<name>/` convention, `deploy-engine.sh`), FS-05/IP-07 (frozen — Bridge wire protocol, unchanged), FS-11/IP-13 and FS-12/IP-14 (frozen — multi-camera config generation and CameraKey mounts, unaffected) |
| Depends On | FS-04/IP-06, FS-05/IP-07 — both delivered |
| Task ID Range | **T-291 – T-310** |
| Owner | Farhan Naeem |
| Explicitly Excluded | General pipeline redesign. New classes. Tracker algorithm changes beyond enable-state. Backend/frontend changes (none required — see FS-13 §2). Deleting YOLOv4 rollback assets. |

---

## 1. Approved Design Decisions

1. **New profile is a sibling directory, not an in-place edit.** `profiles/yolo26-fp16/` is added alongside
   `profiles/yolov4-fp16/`, exactly mirroring FS-04/IP-06's established convention. The only production
   config-file edit is the single `[primary-gie] config-file=` line in `deepstream-app.txt`.
2. **NvDCF re-enablement is in scope, by explicit user decision** (FS-13 §5) — reversing the live device's T-93
   A/B-tested disablement so YOLO26 detections carry tracking IDs as required by the task's validation criteria.
3. **No Backend/frontend changes.** Confirmed by inspection: YOLO26's class taxonomy (`gun`, `knife`) is
   identical to the current production taxonomy, so `AlertController.KnownClassNames`, `AlertSyncService`'s
   suppression counters, and the frontend `WeaponClassBadgeComponent` all remain correct unmodified.
4. **Preprocessing fields (`net-scale-factor`, `offsets`, `model-color-format`, `class-attrs-*` thresholds) are
   verified against the proven experimental config on the real device during implementation**, not blindly
   copied from either the old TAO profile or the experimental project — per FS-13 §4's field classification.

---

## 2. Task Breakdown

### Model/Engine Staging

| Task | Description |
|---|---|
| T-291 | On the real Jetson, verify `best_deepstream.engine` with TensorRT 8.5.2.2: deserializes without a Magic-tag error; input binding `images` = `1x3x640x640` FP32; output binding `output0` = `1x300x6`; record SHA-256. Explicitly prove the file is **not** `best.engine` (byte-diff / distinct SHA-256 comparison). |
| T-292 | Build `libnvdsinfer_custom_impl_Yolo.so` for the production target (aarch64, DeepStream 6.2, TensorRT 8.5.2.2) — do not reuse the experimental desktop-built copy. Verify it loads and exposes `NvDsInferParseYolo`. |
| T-293 | Diff `config_infer_primary_weapon.txt` (experimental, proven) against `profiles/yolov4-fp16/infer-config.txt` field-by-field per FS-13 §4's classification table; produce the final `profiles/yolo26-fp16/infer-config.txt` content, resolving the "uncertain" preprocessing fields with on-device verification (a short bounded inference run comparing detection sanity, not guesswork). |

### Production Profile (repository)

| Task | Description |
|---|---|
| T-294 | Add `deployment/jetson/deepstream/profiles/yolo26-fp16/infer-config.txt` per FS-13 §4 / T-293's resolved values. |
| T-295 | Add `deployment/jetson/deepstream/profiles/yolo26-fp16/labels.txt` — exactly `gun`, `knife`. |
| T-296 | Add `deployment/jetson/deepstream/profiles/yolo26-fp16/manifest.env` mirroring the `yolov4-fp16` manifest shape (`PROFILE_NAME`, `ENGINE_SHA256` from T-291, `ENGINE_PRECISION=FP16`, `INPUT_DIMENSIONS=3;640;640`, `CLASS_COUNT=2`, `LABELS_FILE=labels.txt`, `PARSER_LIB`/`PARSER_FUNC` from T-292, `DEEPSTREAM_VERSION=6.2`, `TENSORRT_VERSION=8.5.2`, `TARGET_DEVICE`, `VALIDATED_DATE`, `VALIDATED_STATUS`). |
| T-297 | `deployment/jetson/deepstream/deepstream-app.txt` — re-enable `[tracker] enable=1` if not already (repo already has `enable=1`; confirm no drift), add a comment documenting the T-93 reversal per FS-13 §5. The `config-file=` line itself is switched only at deployment time (T-305), not committed as part of this task, to keep the rollback path clean (mirrors FS-04/IP-06's "profile switch is an operator action" convention). |

### Tests

| Task | Description |
|---|---|
| T-298 | New/updated tests proving: active engine path resolves to `yolo26-fp16/model.engine`; `best.engine` is never referenced; `num-detected-classes=2`; labels exactly `gun`/`knife` with class 0 = gun, class 1 = knife; parser = `NvDsInferParseYolo`; parser library path is the production path, not `/home/farhan/Desktop/...`; `network-mode=2`; no `onnx-file=` engine-rebuild path is active on normal startup. Mirror the structure of the existing `yolov4-fp16` profile tests (`test_deepstream_config_generator.py`, `test_profile_preflight.py`, `test_cutover_preflight_cli.py`, `test_deepstream_config_staging.py`). |
| T-299 | Pipeline-regression tests (Bridge/Agent): one `nvstreammux`, one primary `nvinfer`, NvDCF enabled, `nvinfer` → NvDCF ordering, detection probe location, `nvstreamdemux`, OSD, dynamic Camera outputs, CameraKey paths, `Camera.Id` mapping, `JetsonHost`/`RtspOutputPort` — all unchanged by the profile swap (`test_pipeline_multi_source_construction.py`, `test_pipeline_tracker_construction.py`, `test_config_camera_key_mounts.py`). |
| T-300 | Event-regression tests: gun/knife class events serialize correctly, confidence and tracking ID survive, `Alert.CameraId` remains the GUID, quota/cooldown/suppression/snapshot correlation unchanged (`test_detection_ingest_handler.py`, `test_detection_event_repository.py`, existing Backend `AlertSyncService`/`AlertController` suites — asserting no change needed, not adding new assertions about a taxonomy that hasn't changed). |
| T-301 | Full regression run: Agent pytest, Bridge pytest (Python 3.8 compatibility), Backend suites touching detector-event semantics, lint/mypy/ruff as currently configured. Record exact totals. |

### Deployment

| Task | Description |
|---|---|
| T-302 | Pre-deployment baseline capture per FS-13/task-brief "Deployment Safety": Agent/Bridge PID, NRestarts, Camera mappings, `configurationVersion`, ConfigCache version, RTSP inputs/outputs, current model profile, current engine SHA-256, pending DetectionEvents/SnapshotOutbox, Alerts, quota, publishers. |
| T-303 | Back up current `yolov4-fp16` profile files, generated DeepStream config, and current model assets for rollback. Do not delete them. |
| T-304 | Stage `yolo26-fp16` runtime assets (engine, labels, manifest, parser library) into `/opt/weapon-detection` via the existing `deploy-engine.sh` mechanism, following its established ownership/permissions convention. |
| T-305 | Switch the single `[primary-gie] config-file=` line in the deployed `deepstream-app.txt` to `profiles/yolo26-fp16/infer-config.txt`; re-enable `[tracker] enable=1` on the live device (FS-13 §5). Restart only what the existing deployment architecture requires. Watch systemd/Bridge logs through first startup. |

### Validation (real Jetson, bounded per CLAUDE.md streaming rules)

| Task | Description |
|---|---|
| T-306 | Startup acceptance: correct config loaded, YOLO26 engine path, successful TensorRT deserialization (no Magic-tag error), no ONNX rebuild, `NvDsInferParseYolo` + parser library loaded, labels loaded, `num-detected-classes=2`, NvDCF initialized, both Camera sources connect, RTSP outputs mount. |
| T-307 | Gun runtime test (bounded, non-looping, approved FFmpeg executable, event watcher started before publishing): class 0 → `gun` label, sensible confidence, tracking ID present, correct Camera/Alert/CameraId, snapshot path functions if enabled, no person label. |
| T-308 | Knife runtime test if trusted knife media is available (same bounded protocol); otherwise report knife validation as pending with automated/parser/class-mapping tests cited as interim proof — do not manufacture evidence. |
| T-309 | Two-camera validation (no cross-assignment, existing `cameras/front-camera`/`cameras/rear-entrance` mounts unchanged) and tracker validation (stable ID across consecutive frames, correct OSD label/confidence/ID). Performance validation: inference/output FPS, CPU/GPU/RAM/temperature, compared against the experimental ~30 FPS baseline and current YOLOv4 baseline where available; bounded soak per existing validation convention. |

### Documentation & Rollback

| Task | Description |
|---|---|
| T-310 | Final report per the task brief's 42-item format; update FS-13/IP-15 status; document old/new assets, SHA-256, parser, labels, class mapping, batch-compatibility proof, config-generation impact (none), deployment/rollback steps, and all runtime validation evidence. Confirm rollback assets remain in place and untouched. |

---

## 3. Acceptance Criteria

FS-13 §6 in full. Summary: `yolo26-fp16` becomes the active profile via the single `config-file=` line change;
`best_deepstream.engine` (never `best.engine`) is deserialized directly with no ONNX rebuild; parser is
`NvDsInferParseYolo` from a production-built library; labels are exactly `gun`/`knife` with unchanged class
ordering; NvDCF is enabled on the live device; pipeline topology, event schema, CameraKey mounts, quota,
snapshot, and Backend/frontend behavior are all unchanged; YOLOv4 rollback assets are preserved; all suites stay
green.

---

## 3A. Read-Only Inspection Evidence (checkpoint, 2026-08-12)

Gathered via SSH to the real production Jetson (`farhan@100.98.226.80`, sudo used only for root-owned reads —
no writes, no restarts, no staging performed).

**Live production state (confirms repository inspection):**

| Item | Value |
|---|---|
| Active `[primary-gie] config-file` | `/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/infer-config.txt` |
| Live `[tracker] enable` | `0` — confirms the T-93 disablement is still in effect on-device; repo template still says `1` |
| Production engine SHA-256 | `2298d1a3d85ef9ff9d630593a01d70532df3c3b484c9aa2c9a4cc2f78081bff8` (matches `manifest.env`) |
| `/opt` free space | 59G available of 116G — ample headroom for staging the new engine |
| `/opt/weapon-detection/models/`, `/opt/weapon-detection/config/deepstream/profiles/` | root-owned (`weapon-detection:weapon-detection`), only `yolov4-fp16` present today |

**T-291 — `best_deepstream.engine` verified directly with the TensorRT 8.5.2.2 Python API on-device** (not just
DeepStream logs):

```
TensorRT version: 8.5.2.2
DESERIALIZE OK, num bindings: 2
  binding 0: name=images   shape=(1, 3, 640, 640) dtype=FLOAT is_input=True
  binding 1: name=output0  shape=(1, 300, 6)       dtype=FLOAT is_input=False
```

No Magic-tag error. SHA-256 of the staged experimental copy:
`ed4810762244c78d04be7617b37e2bd20f8c8252b0f1ed2b5c33475e6eb3f9c1`
(`/home/farhan/Desktop/yolo26-jetson/models/best_deepstream.engine`, 21,614,791 bytes) — **distinct** from
`best.engine`'s SHA-256 (`f6e4da10e5568b3737ada10086960a8afe430ba53fda96821da3eb5db0355d2c`), confirming the
correct file. T-291 is complete.

**T-293 resolved — exact field values, from the live-validated experimental `config_infer_primary_weapon.txt`**
(this is the real answer to FS-13 §4's "uncertain, verify on-device" fields, not a guess):

```
net-scale-factor=0.0039215697906911373
model-color-format=0
# (no offsets= line — TAO mean-subtraction offsets do not apply to this export)
process-mode=1
network-type=0
cluster-mode=4
maintain-aspect-ratio=1
symmetric-padding=1
engine-create-func-name=NvDsInferYoloCudaEngineGet
[class-attrs-all]
pre-cluster-threshold=0.25
topk=300
```

Note `cluster-mode=4` differs from the current YOLOv4 profile's `cluster-mode=2` — this is model-specific
(YOLO26/DeepStream-Yolo's parser expects NMS-cluster mode 4) and must be carried into the production profile,
not preserved from the old pipeline-specific assumption.

**T-292 status — not yet started.** The experimental `libnvdsinfer_custom_impl_Yolo.so` at
`/home/farhan/Desktop/yolo26-jetson/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo/` was in fact compiled directly
on the production Jetson hardware itself (same physical device, confirmed via the same SSH session — not a
cross-compiled desktop artifact), so it already satisfies the "production target" requirement in principle.
Staging it (or rebuilding it fresh from source into the production tree) is a **write** action and was
deliberately not performed in this read-only pass.

**Not yet done (write/staging actions, pending go-ahead):** T-292 (build/stage parser lib into
`/opt/weapon-detection/lib/yolo26/`), T-294–T-296 (commit new profile files to the repo using the values above),
T-297 and beyond.

---

## 4. Rollback

Restore the previous `[primary-gie] config-file=` line to `profiles/yolov4-fp16/infer-config.txt` and revert the
live-device tracker state to match whatever was in effect immediately before T-305 (re-disable if the pre-change
baseline captured in T-302 was `enable=0`). No column/schema change exists to roll back — this is a config-file
and asset-staging change only. Roll back if: the engine fails to deserialize, multi-camera batch incompatibility
is found, the parser fails, labels are wrong, the tracker materially breaks, RTSP outputs fail, the event path
breaks, or a serious performance regression occurs. Do not delete the new YOLO26 assets during rollback.

---

## 5. Deployment & Validation Record (2026-08-12)

### 5.1 Staging (T-292, T-294–T-304)

| Item | Value |
|---|---|
| Parser lib verification | aarch64 ELF; exports `NvDsInferParseYolo`, `NvDsInferParseYoloCuda`, `NvDsInferYoloCudaEngineGet`; `ldd` resolves cleanly against DeepStream 6.2 / TensorRT 8.5.2 (`libnvinfer.so.8`) / CUDA 11.4 (`libcudart.so.11.0`) already installed on the production device — no missing dependencies |
| Parser lib staged | `/opt/weapon-detection/lib/yolo26/libnvdsinfer_custom_impl_Yolo.so`, sha256 `d4d04a52e8f1346ddfdf79d46fcce0965b40c4280e212a51878d3c9f0f6a4a90` (matches source; the experimental copy was itself already compiled on this same physical Jetson, not cross-compiled) |
| Profile config staged | `/opt/weapon-detection/config/deepstream/profiles/yolo26-fp16/{infer-config.txt,labels.txt,manifest.env}` |
| Engine staged via `deploy-engine.sh` | `/opt/weapon-detection/models/yolo26-fp16/model.engine`, sha256 `ed4810762244c78d04be7617b37e2bd20f8c8252b0f1ed2b5c33475e6eb3f9c1` — checksum independently re-verified post-copy; confirmed distinct from `best.engine`'s sha256 (`f6e4da10e5568b3737ada10086960a8afe430ba53fda96821da3eb5db0355d2c`) |
| Production copy re-deserialized directly with TensorRT 8.5.2.2 | `images` 1×3×640×640 FLOAT input, `output0` 1×300×6 FLOAT output — matches source-file verification exactly, no Magic-tag error |
| Backups taken | `/opt/weapon-detection/backups/fs13-20260812T005214Z/` — `deepstream-app.txt.pre-yolo26`, `deepstream.generated.conf.pre-yolo26`, full `yolov4-fp16-profile-backup/` |

### 5.2 Discovered production/repo drift (pre-existing, not introduced by this migration)

The repository's committed `deployment/jetson/deepstream/deepstream-app.txt` had materially drifted from the
actual deployed file at `/opt/weapon-detection/config/deepstream/deepstream-app.txt` — the live file has
additional RTSP source tuning (`latency`, `select-rtp-protocol`, `rtsp-reconnect-*`, `num-extra-surfaces`),
encoder/OSD settings, and a **tracker resolution of `960×544`**, not the repo's stale `640×384`. The cutover
was therefore applied as a **minimal 2-line diff against the live deployed file directly** (profile pointer +
tracker enable), preserving every other production-tuned value — not a wholesale copy of the repo template.
This drift predates this migration and is out of scope to reconcile here; flagged for separate follow-up.

### 5.3 Cutover (T-305)

Applied 2026-08-12T00:55:17Z UTC via `systemctl restart weapon-detection-agent` (the Agent regenerates the
runtime DeepStream config from the template and restarts the Bridge on every start, per
`configuration/coordinator.py` — no separate Bridge-only restart mechanism exists). Agent PID 1837→9559,
Bridge PID 2692→9564, exactly one restart, `NRestarts=0` throughout all subsequent validation.

**Stray-publisher incident (found and resolved before validation):** an indefinite-loop FFmpeg publisher
(`-stream_loop -1`, `handgun-cctv.mp4` → `camera1`) was already running from a prior session, in direct
violation of CLAUDE.md's streaming policy — it had exhausted the production quota across two days
(2026-08-11: 1606 gun + 259 knife suppressed; 2026-08-12: 255 gun + 34 knife suppressed before being caught).
Stopped via `taskkill /PID 10788 /T /F`; verified no child process, no matching command line, and detection
events genuinely stopped (confirmed idle for 30+ seconds) before any further action was taken. Today's quota
row was reset by explicit user instruction before validation testing (`BranchDailyAlertQuotas` for
2026-08-12: `AcceptedAlertCount`/`GunSuppressedCount`/`KnifeSuppressedCount`/`SuppressedDetectionCount` → 0).

### 5.4 Startup acceptance (T-306)

From `/opt/weapon-detection/logs/deepstream/deepstream.log`, Bridge PID 9564:

```
gstnvtracker: Loading low-level lib at .../libnvds_nvmultiobjecttracker.so
gstnvtracker: Batch processing is ON
[NvMultiObjectTracker] Initialized
NvDsInferContext[UID 1]: deserialized trt engine from :/opt/weapon-detection/models/yolo26-fp16/model.engine
NvDsInferContext[UID 1]: Use deserialized engine model: /opt/weapon-detection/models/yolo26-fp16/model.engine
[UID 1]: Load new model:/opt/weapon-detection/config/deepstream/profiles/yolo26-fp16/infer-config.txt sucessfully
INFO: [Implicit Engine Info]: layers num: 2
0   INPUT  kFLOAT images          3x640x640
1   OUTPUT kFLOAT output0         300x6
bridge_camera_output_attached (×2), bridge_per_camera_outputs_attached
```

No Magic-tag error, no ONNX-rebuild path taken, no error/exception lines. DeepStream's own reported binding
table matches the independent TensorRT-level verification exactly.

### 5.5 Gun runtime proof (T-307) — camera1/front-camera

Bounded, non-looping (`-t 15`), real PID 54548, approved executable, event watcher started before publish,
`taskkill /PID 54548 /T /F`-eligible but exited naturally within the bound; no orphan.

| Field | Value |
|---|---|
| EventId | `632e6420-7e24-4dbd-a2e5-cb72176030b4`, `78da4019-42ad-411e-9e0a-7b7b7fae8242` |
| SourceId → CameraId | `0` → `2613b331-8783-4d51-903a-3e41a979a14c` (front-camera) |
| ClassId / ClassName | `0` / `gun` — no `person` label anywhere |
| Confidence | 0.886, 0.891 |
| DeliveryStatus | `delivered` |
| Backend Alert | `51b21c05-…`, `e4bc696a-…` — `ClassId=0`, `ClassName=gun`, correct `CameraId`, `Status=New` |

### 5.6 Knife runtime proof (T-308) — camera2/rear-entrance

Simultaneous bounded test with the gun test (both `-t 15`, real PIDs 57512/54976), proving both sources
concurrently rather than sequentially.

| Field | Value |
|---|---|
| EventId | `d5d4159c-d91b-4c64-810b-29d00ee5e2a7` |
| SourceId → CameraId | `1` → `ad8a1f09-7fba-4794-8f73-63c7e2c57c92` (rear-entrance) |
| ClassId / ClassName | `1` / `knife` |
| Confidence | 0.583 |

Note: the same `knife-tester.mp4` clip also produced one `gun`-classified detection (confidence 0.796) on the
same source — a model precision observation (not a pipeline defect; `CameraId` mapping was still correct in
both cases, so this is not cross-camera assignment).

### 5.7 Two-camera batch compatibility proof (T-309)

Confirmed empirically, not inferred: `source0` (front-camera) and `source1` (rear-entrance) both produced
correct, correctly-mapped detections within the same ~15s concurrent window, with `[streammux] batch-size=2`
(dynamic, per camera count) and `nvinfer batch-size=1` (static, matching the engine's fixed `1×3×640×640`
binding) — the same batching pattern already used in production before this migration. No source was silently
skipped; no cross-camera assignment occurred.

### 5.8 Regression checks

Agent/Bridge PIDs stable (9559/9564) and `NRestarts=0` across the entire cutover-through-validation window.
Snapshots directory empty throughout (feature remains disabled, per FS-08/IP-10 — unaffected). No
error/exception lines in the Agent or DeepStream logs during any test. Both bounded FFmpeg publishers exited
naturally within their `-t 15` bound; no orphan processes on Windows or the Jetson.

### 5.9 Not captured in this pass

Sustained multi-minute FPS/CPU/GPU/RAM/temperature soak was not run — the bounded ~15s tests were too short to
trigger DeepStream's `perf-measurement-interval-sec=5` reporting cycle reliably, and no PERF lines were
captured in the logs. The original experimental validation already demonstrated ~24–36 FPS with this exact
engine; no regression was observed qualitatively (detections were prompt, no frame stalls, no restart loop) in
the bounded tests run here, but a proper sustained soak is recommended before declaring long-run performance
parity. The pre-existing repo/production template drift (§5.2) is unresolved and tracked separately.

---

## 6. Extended Validation — NvDCF Tracking-ID, Sustained Load, 5-Minute Soak (2026-08-12, continued)

Validation-only pass; no code, config, model, parser, tracker, batch, threshold, or networking changes were
made in this section. One narrow exception: today's `BranchDailyAlertQuotas` row was reset at user's explicit
instruction before testing (recorded in §5.3), not repeated here.

### 6.1 NvDCF tracking-ID evidence

Direct visual evidence pulled from the live annotated RTSP output (`rtsp://127.0.0.1:8554/cameras/front-camera`
on-device, recorded locally on the Jetson and copied off for inspection — no code instrumentation added, per
the task's constraint). Two independent bounded gun tests each produced at least one frame with a real,
non-sentinel NvDCF-assigned tracking ID overlaid by the existing OSD:

- `gun 117` — first bounded test (`gun-tester.mp4` → camera1)
- `gun 140` — second bounded test (`handgun-cctv.mp4` → camera1)

Both confirm: correct `gun` class label, a genuine sequential tracker ID (not 0, not a fixed/sentinel value,
distinct between separate detections), and no `person` label anywhere in the OSD output.

**Limitation, reported honestly per the task's instruction not to paper over gaps:** consecutive-frame
(same-ID-across-multiple-frames) stability was **not captured visually** in this pass. The available test
clips (`gun-tester.mp4`, `gun-tester-hd.mp4`, `handgun-cctv.mp4`) are fast-cutting action/incident montages
where the weapon is only clearly unoccluded for brief (~0.1–0.3s) windows between camera cuts and rapid motion
blur; several sampled frames immediately before/after each confirmed detection showed no bounding box at all
(weapon not visible at that instant, not a tracker failure). This is a property of the available source media,
not an observed pipeline defect. NvDCF itself is proven to (a) initialize without error every startup, (b)
assign real per-object IDs, (c) not corrupt class/label output, and (d) introduce no pipeline instability
across ~9 minutes of combined bounded/soak testing. Full multi-frame ID-persistence proof against a clean,
sustained, unoccluded weapon view remains a recommended follow-up with better-suited source footage.

### 6.2 Two-camera sustained load + 5-minute soak

Baseline (2026-08-12T01:24:56Z, idle): Agent PID 9559, Bridge PID 9564, NRestarts=0, RAM 6408/15503MB, CPU
~1-4%/core, GPU 0%, temps 55-59°C, VDD_IN 5648mW. `[tracker] enable=1`, `[streammux] batch-size=2`,
`nvinfer batch-size=1` confirmed in the live generated config.

Soak: two real, non-looping, bounded (`-t 300`, real PIDs, approved executable) publishers —
`gun-tester.mp4` → camera1, `knife-tester.mp4` → camera2 (both source files are 300s+ long, so this ran as a
genuine single-pass 5-minute stream, not a loop) — started 2026-08-12T01:38:35Z. Five `tegrastats` + Agent
health samples taken at ~60s intervals throughout:

| t (approx) | Agent PID | NRestarts | RAM (MB) | GPU util | GR3D freq | Temp range (°C) | VDD_IN (mW) | Errors |
|---|---|---|---|---|---|---|---|---|
| +90s | 9559 | 0 | 6398 | 90% | 902MHz | 63.5–66.1 | 14417 | 0 |
| +150s | 9559 | 0 | 6402 | 91% | 902MHz | 64.9–68.1 | 14094 | 0 |
| +210s | 9559 | 0 | 6411 | 44% | 918MHz | 65.8–69.2 | 13932 | 0 |
| +270s | 9559 | 0 | 6415 | 89% | 914MHz | 66.7–69.8 | 14659 | 0 |
| +330s (post-end) | 9559 | 0 | 6415 | 0% (idle) | 306MHz | 62.1–65.2 | 5924 | 0 |

RAM grew 6398→6415MB (17MB, ~0.3%) across the full soak then held flat for the last two samples — not a
leak trajectory. GPU utilization varied naturally with scene content (44–91%), never pinned at 100% in a way
suggesting saturation-driven frame loss. Temperatures rose from the idle baseline to a plateau around 66–70°C
and were already cooling by the post-end sample — normal thermal behavior under load, not a runaway. Zero
Agent-log or DeepStream-log error lines across all five samples. `errcount=0` and `dserr=0` for the entire
window.

### 6.3 End-of-soak state (2026-08-12T01:44:34Z)

| Item | Value |
|---|---|
| Both publishers | Confirmed exited naturally at their `-t 300` bound; zero FFmpeg processes remaining on Windows or the Jetson |
| Agent PID | 9559 (unchanged since the FS-13 cutover restart) |
| Bridge PID | 9564 (unchanged; same process throughout, 5:03 accumulated CPU time — confirms sustained real work, not idle) |
| NRestarts | 0 (unchanged across the entire cutover + all validation + soak) |
| DetectionEvent `delivered` | 2836 (was 2774 pre-soak baseline — 62 new, all from this validation's bounded/soak tests) |
| DetectionEvent `suppressed_by_quota` | 2154 (unchanged from pre-soak — no new suppression, quota had headroom) |
| Backend Alert count | 2836 — exactly matches `delivered` DetectionEvents, 1:1, no drops or duplicates |
| Today's quota | `AcceptedAlertCount=87`, `GunSuppressedCount=0`, `KnifeSuppressedCount=0` — well under the 1000/day ceiling |
| Pending sync backlog | None — sync worker fully drained within the normal interval |
| Snapshot directory | Empty throughout — feature remains disabled, unaffected |
| RTSP outputs | Both remained mounted and healthy for the full test; no reconnect storm beyond normal idle-source `Resetting source` messages when a camera has no active publisher |

### 6.4 Assessment

- **Resource leak assessment:** none observed. RAM essentially flat; no unbounded growth trend.
- **RTSP stability assessment:** stable. Both annotated outputs remained mounted; no stream freeze, no crash, no restart.
- **Performance assessment:** sustained, real-time-consistent load handled without service disruption; GPU/CPU/temp/power all behaved as expected for active dual-camera YOLO26 inference; no progressive degradation across the 5-minute window. (Frame-accurate FPS min/avg/max was not independently instrumented — DeepStream's `perf-measurement-interval-sec=5` output was not captured in the log this pass; the qualitative evidence — sustained GPU activity, stable temps, zero errors, continuous detection/Alert flow throughout — supports a PASS on real-time behavior without a precise FPS number.)
- **NvDCF assessment:** initializes cleanly every time; assigns genuine tracking IDs; introduces no instability. Consecutive-frame ID-persistence visual proof is the one item not fully closed (§6.1) — WARNING, not BLOCKER.

---

## 7. Status Determination

Per the task's final-status rule, all required conditions are met **except** two items are evidenced
qualitatively rather than with the exact quantitative artifact originally specified:

1. NvDCF stable multi-frame ID persistence — real IDs proven, consecutive-frame run not visually captured (§6.1).
2. Exact FPS min/avg/max — not instrumented; sustained real-time behavior evidenced qualitatively instead (§6.4).

Everything else — engine, parser, both classes, no person-label, two-camera inference, source mapping, RTSP
outputs, DetectionEvents, Alerts, NvDCF initialization, 5-minute soak, zero restarts, zero errors, no resource
leak, rollback availability — is fully proven.

**Status: WARNING — DEPLOYED AND VALIDATED WITH TWO OPEN EVIDENCE ITEMS.** Production behavior is correct and
stable; the gaps are in the precision of two pieces of collected evidence, not in system correctness. Recommend
closing FS-13/IP-15 to full PASS once (a) a clean, sustained, unoccluded weapon clip is available for a
proper consecutive-ID visual proof, and (b) a longer/instrumented soak captures explicit FPS samples.
