# Feature Specification: Replace Production YOLOv4 Detector with Custom YOLO26 Weapon Detector

| Field | Value |
|-------|-------|
| Feature ID | FS-13 |
| Title | Replace the production `yolov4-fp16` DeepStream inference profile with a custom `yolo26-fp16` profile (two-class gun/knife detector); re-enable NvDCF on the live Jetson device |
| Status | **WARNING — Deployed and Validated, two evidence items open.** Cutover applied 2026-08-12; 5-minute two-camera soak passed (zero restarts, zero errors, no resource leak). Gun and knife detection both proven end-to-end (DetectionEvent → Alert); NvDCF re-enabled and proven to assign real tracking IDs; no cross-camera assignment; no `person`-label leakage. Open: consecutive-frame NvDCF ID-persistence not visually captured (real IDs proven, non-consecutive), and exact FPS min/avg/max not instrumented (sustained real-time behavior evidenced qualitatively). See IP-15 §6–7 for full evidence and recommended closure steps. |
| Owner | Farhan Naeem |
| Related SRS Requirements | FR-DET-* (detection identity, existing) — no new SRS requirement; this feature changes only the model artifact and its inference profile behind the existing `[primary-gie]`/`[tracker]` sections |
| Related Architecture Sections | §13 (DeepStream pipeline), §14 (Agent config generation) — no structural change, profile-file swap only |
| Related ADRs | None new |
| Dependencies | FS-04/IP-06 (DeepStream runtime integration — delivered, established the `profiles/<name>/` convention and `deploy-engine.sh`), FS-05/IP-07 (detection event Bridge — delivered, frozen wire protocol), FS-11/IP-13 (server-driven multi-camera config — delivered, unaffected) |
| Explicitly excluded | General pipeline redesign. New person/other detector classes. Tracker algorithm redesign (only its enable-state is touched, see §5). Network/topology redesign. Backend/frontend/quota/snapshot/Agent config-generation redesign — none of these require code changes because the new detector's class taxonomy (`gun`, `knife`) is identical to the current one. Dropping YOLOv4 rollback assets. |

---

## 1. Purpose

The production Jetson pipeline currently runs a TAO-toolkit-exported YOLOv4 engine (`yolov4-fp16` profile) whose
TensorRT graph performs NMS internally (`BatchedNMS` output, parsed by the stock
`NvDsInferParseCustomBatchedNMSTLT`). A custom-trained YOLO26 gun/knife detector has been independently trained,
exported to a raw TensorRT engine, and validated end-to-end on the real Jetson hardware in an experimental
project (`/home/farhan/Desktop/yolo26-jetson`) — proven deserialization, ~30 FPS, correct gun bounding box, no
COCO/person label leakage.

This feature installs that detector as a new **sibling** inference profile (`yolo26-fp16`) in production, following
the exact `profiles/<name>/` convention FS-04/IP-06 already established, and switches the single
`[primary-gie] config-file=` line in `deepstream-app.txt` to point at it. Nothing upstream of the engine
(nvstreammux, source handling, CameraKey mounts) or downstream of the parser (NvDCF, probe/event logic,
nvstreamdemux, OSD, RTSP outputs, snapshots, Backend sync) is redesigned.

---

## 2. Current vs. New Profile (frozen facts, from repository inspection)

| Property | Current (`yolov4-fp16`) | New (`yolo26-fp16`) |
|---|---|---|
| Engine source | TAO-toolkit export, `BatchedNMS` TensorRT plugin output | Raw TensorRT engine exported from Ultralytics YOLO26, `output0` single tensor |
| Engine file | `/opt/weapon-detection/models/yolov4-fp16/model.engine` | `/opt/weapon-detection/models/yolo26-fp16/model.engine` (staged from the validated `best_deepstream.engine`, **not** `best.engine`) |
| Output layer(s) | `output-blob-names=BatchedNMS` (4-tensor NMS output) | `output0` = `1 x 300 x 6` (parser-internal decode) |
| Parser | `NvDsInferParseCustomBatchedNMSTLT` (stock DeepStream library) | `NvDsInferParseYolo` (custom library, production-built) |
| Parser library | `/opt/nvidia/deepstream/deepstream-6.2/lib/libnvds_infercustomparser.so` (stock) | `/opt/weapon-detection/lib/yolo26/libnvdsinfer_custom_impl_Yolo.so` (production-built for aarch64/DS6.2/TRT8.5.2.2 — **not** copied from the experimental desktop path) |
| `tlt-model-key` | `tao_encode` | removed — not applicable to a non-TAO engine |
| Labels | `gun`, `knife` (class 0, class 1) | **identical** — `gun`, `knife` (class 0, class 1) |
| `num-detected-classes` | 2 | 2 (unchanged) |
| `infer-dims` | `3;640;640` | `3;640;640` (unchanged) |
| `network-mode` | 2 (FP16) | 2 (FP16, unchanged) |
| `batch-size` (nvinfer) | 1 | 1 (unchanged — matches the validated engine's static `1x3x640x640` binding) |
| `net-scale-factor` / `offsets` / `model-color-format` | TAO mean-subtraction values (`1.0` / `103.939;116.779;123.68` / `1`=RGB) | **uncertain — must be verified** against the proven experimental `config_infer_primary_weapon.txt` on the real Jetson before being finalized (§4) |
| Tracker (NvDCF) | Repo template `enable=1`; **live device currently `enable=0`** (T-93 A/B evaluation, 2026-07-28) | `enable=1` on **both** the repo template and the live device — this feature re-enables NvDCF on the live Jetson, reversing T-93 (explicit decision, see §5) |

Because the class taxonomy is unchanged, **no Backend or frontend code changes are required** — the existing
`AlertController.KnownClassNames`, `AlertSyncService`'s `GunSuppressedCount`/`KnifeSuppressedCount`, and the
frontend `WeaponClassBadgeComponent`'s `gun`/`knife` states all remain correct as-is.

---

## 3. Scope

**In scope:**

1. A new `profiles/yolo26-fp16/` directory (`infer-config.txt`, `labels.txt`, `manifest.env`) mirroring the
   existing `yolov4-fp16` layout exactly.
2. A production-built `libnvdsinfer_custom_impl_Yolo.so` for the real Jetson target.
3. Engine staging, SHA-256 verification, and TensorRT deserialization proof for `best_deepstream.engine`
   (explicitly proving it is not `best.engine`).
4. The single `config-file=` line change in `deepstream-app.txt`.
5. Re-enabling NvDCF on the live device (§5).
6. Automated tests proving the new profile resolves correctly and the old one is no longer active.
7. Real-Jetson gun/knife runtime validation, two-camera validation, tracker validation, performance validation.

**Out of scope:** everything listed in the header's "Explicitly excluded" row, plus: rebuilding the TensorRT
engine from ONNX at any point in the normal startup path (forbidden — see §4), and dropping the YOLOv4 rollback
assets (retained per §7).

---

## 4. Inference Profile Contract (frozen)

`profiles/yolo26-fp16/infer-config.txt` `[property]` section must resolve to:

```
model-engine-file=/opt/weapon-detection/models/yolo26-fp16/model.engine
labelfile-path=/opt/weapon-detection/config/deepstream/profiles/yolo26-fp16/labels.txt
infer-dims=3;640;640
batch-size=1
network-mode=2
num-detected-classes=2
gie-unique-id=1
parse-bbox-func-name=NvDsInferParseYolo
custom-lib-path=/opt/weapon-detection/lib/yolo26/libnvdsinfer_custom_impl_Yolo.so
```

Fields classified per the task brief:

- **model-specific, use the proven YOLO26 value:** `model-engine-file`, `parse-bbox-func-name`, `custom-lib-path`,
  `output-blob-names` (omit unless the production parser build requires it — the known-working experimental
  config does not set it explicitly).
- **pipeline-specific, preserve production value:** `gpu-id`, `interval`, `is-classifier`, `cluster-mode`,
  `gie-unique-id`, `batch-size` (already 1, matches the engine).
- **obsolete YOLOv4-specific, remove:** `tlt-model-key`, `output-blob-names=BatchedNMS`.
- **uncertain, verify on-device before finalizing:** `net-scale-factor`, `offsets`, `model-color-format`,
  `class-attrs-*` thresholds — diff field-by-field against the proven
  `/home/farhan/Desktop/yolo26-jetson/DeepStream-Yolo/config_infer_primary_weapon.txt` during implementation;
  do not blindly inherit the TAO YOLOv4 mean-subtraction values.

`labels.txt` must contain exactly:

```
gun
knife
```

Startup must **never** trigger ONNX engine rebuilding — `model-engine-file` must directly deserialize
`best_deepstream.engine`; no `onnx-file=` key is set (or it is present only commented out, mirroring the
experimental config's documented pattern).

---

## 5. Tracker Re-enablement (explicit decision)

The live Jetson currently runs with NvDCF **disabled**, following an isolated A/B evaluation (T-93,
2026-07-28) that found no detection-quality cost and a throughput benefit from disabling it. This feature
**reverses that change**: NvDCF is re-enabled (`[tracker] enable=1`) on the live device as part of this
migration, so that YOLO26 detections carry stable tracking IDs (`gun <confidence> ID:<tracking-id>`,
`knife <confidence> ID:<tracking-id>`) as required by the task's tracker validation criteria. The repository
template already has `enable=1` — only the live device's runtime state changes. This is a deliberate,
user-approved reversal of T-93, not an oversight; it is recorded here and in the implementation plan's
deployment record for traceability.

---

## 6. Acceptance Criteria

1. Active production model profile is `yolo26-fp16`; `deepstream-app.txt`'s `[primary-gie] config-file=` points
   at `profiles/yolo26-fp16/infer-config.txt`.
2. `model-engine-file` resolves to a staged copy of `best_deepstream.engine`; `best.engine` is never referenced
   anywhere in production configuration.
3. Deployed engine SHA-256 is verified and recorded, proving it is the raw-TensorRT engine, not the
   Ultralytics-metadata-wrapped one.
4. `num-detected-classes=2`; labels resolve exactly to `gun` (class 0), `knife` (class 1); no COCO/person mapping
   is reachable anywhere in the active configuration.
5. Parser is `NvDsInferParseYolo`, loaded from a production-built (not experimental-path) custom library.
6. `network-mode=2`; `infer-dims=3;640;640`.
7. `batch-size=1` for nvinfer, unchanged from current production behavior; streammux batch-size continues to be
   set per-camera-count by the existing Agent config generator (`config_generator.py`), untouched by this
   feature.
8. Normal production startup never rebuilds the engine from ONNX.
9. NvDCF is enabled (`enable=1`) on the live device following this migration (§5); tracking IDs are produced and
   remain stable per existing tracker behavior.
10. One `nvstreammux`, one primary `nvinfer`, `nvstreamdemux`, and OSD remain unchanged in topology; the
    detection probe remains at its existing pipeline location.
11. `DetectionEvent`/`Alert` class names serialize as `gun`/`knife`; `Alert.CameraId` remains the immutable
    Camera GUID; quota, cooldown/suppression, and snapshot correlation behavior are unchanged.
12. CameraKey output mounts (`cameras/front-camera`, `cameras/rear-entrance`), `JetsonHost`/`RtspOutputPort`
    behavior, and `configurationVersion`/`ConfigCache` behavior are unchanged (FS-11/FS-12 untouched).
13. No Backend or frontend code change is required or made, because the class taxonomy is unchanged.
14. YOLOv4 rollback assets (`profiles/yolov4-fp16/*`, staged engine) remain present and are not deleted.
15. Existing Agent/Bridge/Backend test suites remain green; new tests (§ implementation plan) prove the above.
