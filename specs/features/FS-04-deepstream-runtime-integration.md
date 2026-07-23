# Feature Specification: DeepStream Runtime Integration (Phase 1)

| Field | Value |
|-------|-------|
| Feature ID | FS-04 |
| Title | DeepStream Runtime Integration — Agent-supervised DeepStream child process (Phase 1 of Jetson Agent Bootstrap & DeepStream Supervision) |
| Status | Draft — awaiting approval |
| Related SRS Requirements | Detection/inference requirements are assumed elsewhere in the SRS (weapon detection is the platform's stated purpose); this phase adds no detection-accuracy or alerting requirement — see §1.2. |
| Related Architecture Sections | ARCH-001 §10.2 (Pipeline Supervisor), §11.1 scenario 1, ADR-006 (systemd manages only the Agent, never DeepStream directly) — referenced as prior context; **ARCH-001 is not present in this repository** (see §9, Open Items) so these citations describe intent inherited from FS-02/IP-02, not a file this spec can re-verify. |
| Related ADRs | ADR-006 (systemd/DeepStream separation), ADR-010 (exactly one Uvicorn worker — the Agent process; DeepStream is a separate supervised child, not a second worker) |
| Owner | Farhan Naeem |
| Dependencies | IP-02 (Jetson Agent Foundation — delivered T-31–T-41: `AgentRuntimeSupervisor`, `OperationalStateCoordinator`, `OperationalComponent` protocol, `AgentSettings`/`WDA_` convention); IP-05 (Device Reactivation Security — delivered: the Operational/ReactivationRequired state machine this feature's start/stop gate depends on) |
| Fulfills | OI-1 from IP-02 (§21/526): "Author FS-03 — Jetson Agent Bootstrap & DeepStream Supervision before the plan that implements it." (That working title's "FS-03" slot has since been taken by FS-03 — Branch & Camera Management, so this spec is numbered FS-04. It covers **Phase 1 only** of that originally-envisioned feature — process supervision, not detection/alerting.) |
| Approval basis | Explicit user request to move forward with DeepStream integration (this document records that scope). No SRS or ARCH-001 requirement is added or changed by Phase 1. |

---

## 1. Purpose and Scope

DeepStream inference has been **manually proven** on the target Jetson (Orin NX Developer Kit, JetPack 5.1.2 / L4T R35.5.0, DeepStream 6.2.0, TensorRT 8.5.2): a YOLOv4 (ResNet18 backbone) two-class detector, exported through TAO, built into an FP16 TensorRT engine on this exact device, running through `deepstream-app` against a local test video at ~24.5 FPS with `App run successful`. See §6/§10 for the verified evidence.

What does **not** exist yet is application integration: the FastAPI Agent has no code path that starts, stops, or supervises DeepStream. This feature closes exactly that gap — nothing more.

```text
FastAPI Agent
    ↓ starts/stops/supervises   ◄── THIS FEATURE
DeepStream process
    ↓ reads
local video file (Phase 1) / RTSP camera (Phase 2)
    ↓ performs inference
weapon detections
    ↓ produces                  ◄── explicitly NOT this feature (§1.2)
events, snapshots, recordings and Backend alerts
```

### 1.1 What Phase 1 Adds

| Capability | Basis |
|------------|-------|
| A managed filesystem layout for model/config assets under `/opt/weapon-detection/` (ADR-008-style modes, consistent with IP-02 §5/§8) | Currently these assets live ad hoc under `/home/farhan/tao-experiments/`, outside the Agent's owned layout and outside the `weapon-detection` service user's ownership. |
| `WDA_`-prefixed settings for the DeepStream executable, config path, working directory, graceful-stop timeout, restart policy, and log path | Follows the existing `AgentSettings` convention (`config/settings.py`) exactly — no new configuration mechanism. |
| A `DeepStreamProcessManager` implementing the existing `OperationalComponent` protocol (`start`/`stop`/`name`) | The coordinator/supervisor already has this exact extension point (T-60); Phase 1 is the first thing to occupy it. |
| Registration of `DeepStreamProcessManager` as an operational component in `AgentRuntimeSupervisor` | Reuses the delivered start/stop/lock machinery unchanged (§4). |
| Process lifecycle safety: no `shell=True`, explicit argv, controlled working directory, SIGTERM-then-SIGKILL graceful stop, child reaping, duplicate-start rejection, idempotent stop, unexpected-exit detection, safe (credential-free) logging | Matches IP-02's existing security posture (no shell injection surface, no secret in logs) applied to a new subprocess boundary. |
| Local-video-file first execution mode | De-risks the integration (no RTSP/camera dependency) before Phase 2 changes the source. |

### 1.2 What Phase 1 Does Not Touch

Phase 1 is strictly process supervision. It does **not**:

- perform, parse, or act on inference output (no metadata extraction, no detection events);
- talk to the Backend about detections (no alerts, no snapshots, no recordings, no WebRTC);
- change the RTSP camera source (local video file only — §7);
- change activation, reactivation, or credential validation (FS-02/IP-05 unchanged);
- add a health/heartbeat endpoint (OI-3, still open, still out of scope);
- modify `ARCH-001`, `SRS-001`, or any Backend/Angular code;
- copy TensorRT engines, ONNX files, or video files into version control (§2, §8).

Everything in this list is future phases of the eventual "Jetson Agent Bootstrap & DeepStream Supervision" feature, not this one.

## 2. Actors

| Actor | Description |
|-------|-------------|
| `AgentRuntimeSupervisor` | Existing Agent runtime owner (IP-02 T-39, IP-05 T-61). Decides, via its existing startup branches, whether operational components — now including DeepStream — should run. |
| `OperationalStateCoordinator` | Existing component lifecycle owner (IP-05 T-60). Starts/stops/rolls back `DeepStreamProcessManager` exactly as it does any other `OperationalComponent`, with no DeepStream-specific logic of its own. |
| `DeepStreamProcessManager` (new) | Owns the DeepStream child process: launch, graceful stop, forced kill, exit detection, safe logging. |
| `deepstream-app` (external process) | NVIDIA's DeepStream reference application, unmodified, launched with a config file this feature manages. |

No Backend or Dashboard actor participates in Phase 1.

## 3. Preconditions

- The Agent has successfully activated or reactivated (IP-02/IP-05): a `DeviceIdentity` exists and `OperationalState` is `Operational`.
- The managed asset layout (§8) exists on the Jetson with the validated FP16 engine, config, and labels copied in (a deployment step, not a code precondition).
- DeepStream 6.2 and its dependencies are already installed at `/opt/nvidia/deepstream/deepstream-6.2/` (already true — proven in §6/§10; this feature installs nothing).

## 4. Functional Behavior

| # | Behavior | Basis |
|---|----------|-------|
| 1 | While `Operational`, exactly one DeepStream child process runs, supervised by the Agent. | Primary goal (this feature's reason to exist). |
| 2 | DeepStream starts only after the Agent's existing startup/reactivation machinery reaches `Operational` — Branch A, Branch B, and Branch D1 (validated, connected) / D2 (validated, indeterminate/offline) all start it; Branch C (locked, `ReactivationRequired`) and Branch D3/D4 (confirmed-rejected/lock-failure) never start it. | Reuses `AgentRuntimeSupervisor`'s existing branch decisions (§4 research, no new branch). |
| 3 | A confirmed credential rejection detected by the running credential-validation monitor (IP-05 T-59) stops DeepStream, through the same `coordinator.enter_reactivation_required()` path that stops every other operational component. | IP-05's existing lock semantics extended, not modified. |
| 4 | Agent shutdown (SIGTERM, systemd stop/restart) stops DeepStream before the Agent process exits. | `AgentRuntimeSupervisor.shutdown()` already calls `coordinator.enter_reactivation_required()` unconditionally; DeepStream inherits this for free once registered. |
| 5 | Reactivation (operator provisions a new key, Agent restarts and re-activates) allows DeepStream to start again. | Same start path as behavior #2, re-entered after a fresh `Operational` transition. |
| 6 | At most one DeepStream process ever exists under Agent supervision; a second `start()` call while one is running is rejected without spawning a duplicate. | Explicit lifecycle requirement (§Requirements, "reject duplicate start"). |
| 7 | Stopping an already-stopped `DeepStreamProcessManager` is a safe no-op. | Mirrors `AgentRuntimeSupervisor.shutdown()`'s own idempotency (IP-05 T-61). |
| 8 | An unexpected DeepStream exit (crash, non-zero return code, or clean exit while still expected to run) is detected and safely logged; it does not crash the Agent process. | Process-supervision requirement; matches the monitor's existing "fail-closed but the Agent keeps running" posture (IP-05 `_on_monitor_fatal`). |
| 9 | DeepStream is launched as a direct child process with an explicit argv list — never `shell=True`, never a string command line. | Removes a shell-injection surface entirely (no attacker-controlled or malformed input can be interpreted by a shell). |
| 10 | DeepStream's stdout/stderr are captured into a bounded, Agent-owned log location — never the Agent's structured JSON log directly (the two are different log streams; §8) — and never include an RTSP URL, credential, or secret. | Matches IP-02's existing "no secret in logs" invariant (MAC-9) applied to a new source of log output the Agent does not fully control the content of. |

## 5. Process Lifecycle State Machine

```
                  start()                 unexpected exit / stop()
   NOT_RUNNING ────────────▶ RUNNING ─────────────────────────────▶ NOT_RUNNING
        ▲                       │
        │      stop() (SIGTERM, wait ≤ timeout, else SIGKILL, reap)
        └───────────────────────┘

   start() while RUNNING            → rejected (duplicate start), no new process
   stop() while NOT_RUNNING         → no-op, idempotent
```

`RUNNING` exposes a safe PID and a monotonic "started at" timestamp; nothing else about the child process is exposed outside `DeepStreamProcessManager`.

## 6. Grounding in Verified Evidence (Jetson Discovery)

This section records what was found by inspecting the Jetson **without changing it** — every claim below is sourced from a specific file or command output, not assumed.

| Fact | Evidence |
|------|----------|
| DeepStream version | `6.2.0-1` (`dpkg -l`, `/opt/nvidia/deepstream/deepstream/version`). Executable at `/usr/bin/deepstream-app` (symlink) and `/opt/nvidia/deepstream/deepstream-6.2/bin/deepstream-app`. |
| Device | `NVIDIA Orin NX Developer Kit` (`/proc/device-tree/model`); `trtexec` logs confirm `Selected Device: Orin` for both engines built on-device. |
| JetPack / L4T | `R35 (release), REVISION: 5.0` — matches JetPack 5.1.2 (already recorded). System Python remains `3.8.10`; the Agent's own venv Python is unaffected. |
| TensorRT | `8.5.2-1+cuda11.4` (`dpkg -l`), matching the `TensorRT v8502` stamped in every `trtexec` log — the installed runtime is the **same version** that built the engines, so re-deserializing them today is expected to work. |
| Two candidate engines exist | `yolov4_fp16.engine` (71,132,659 bytes) and `yolov4_resnet18_jetson.engine` (37,149,614 bytes), both under `/home/farhan/tao-experiments/yolo_v4/export/`, both built **on this Jetson** (`trtexec_fp16_log.txt`, `trtexec_jetson_log.txt`) from the same `yolov4_resnet18.etlt.onnx` — they are the **same architecture** (YOLOv4, ResNet18 backbone), differing only in TensorRT precision: FP16 vs. INT8 (with `cal.bin` calibration). "`_resnet18_`" in the filename is **not** a different network family from "`_fp16`" — this is a naming trap worth flagging (see §9). |
| **Only the FP16 engine is proven to run successfully in DeepStream** | `deepstream_jetson_log.txt` and `deepstream_yaml_log.txt` both show `yolov4_fp16.engine` deserialized and `App run successful` at a steady ~24.5 FPS. `debug_log.txt` shows the **INT8** engine (`yolov4_resnet18_jetson.engine`) loaded and then `App run failed` (pipeline state-change failure). **The validated engine for this feature is therefore `yolov4_fp16.engine`, not `yolov4_resnet18_jetson.engine`.** |
| Exact working DeepStream command | From `~/.bash_history` (`strings`, read-only) and confirmed against the successful logs: <br>`deepstream-app -c ~/tao-experiments/yolo_v4/export/deepstream_app_config.txt` <br>(an equivalent `.yml`-config run also succeeded and produced `deepstream_yaml_log.txt`). |
| Working `primary-gie` config | `config_infer_primary_yolov4.txt`: `infer-dims=3;640;640`, `batch-size=1`, `network-mode=2` (FP16), `num-detected-classes=2`, `cluster-mode=2`, `output-blob-names=BatchedNMS`, `parse-bbox-func-name=NvDsInferParseCustomBatchedNMSTLT`, `custom-lib-path=/opt/nvidia/deepstream/deepstream-6.2/lib/libnvds_infercustomparser.so`. Matches exactly what the user described. |
| Labels | `labels.txt` — two lines: `gun`, `knife`. |
| Source used for the successful run | `file:///home/farhan/tao-experiments/test_video.mp4` (local file, `type=3` in `deepstream_app_config.txt`'s `[source0]`), a 24,460,908-byte MP4 also present verbatim on the Jetson. |
| Output produced | `output_v4.mp4` (162,837,632 bytes on-device) via `[sink1]` (`container=1`, `codec=1`, file sink) alongside an `[sink0]` display/fakesink. |
| All paths in every working config are absolute and hard-coded to `/home/farhan/tao-experiments/...` | Every `.txt`/`.yml` config inspected. **These must be rewritten to the managed `/opt/weapon-detection/...` layout (§8) before the Agent can launch DeepStream from a config it owns** — this is explicit work item #2 in the implementation plan, not something Phase 1 can skip. |

No file under `/home/farhan` or `/opt/weapon-detection` was modified during this discovery; every command used was read-only (`find`, `ls`, `cat`, `grep`, `strings`, `dpkg -l`).

## 7. Execution Mode (Phase 1)

Source is a **local video file** (`file://` URI), matching the already-proven `deepstream_app_config.txt`. Phase 1's success criterion is:

```
Agent Operational
  → Agent starts DeepStream (managed config, managed engine path)
  → DeepStream processes the local test video
  → DeepStream exits (end-of-stream) or remains active, matching the configured source mode
  → Agent observes the exit/continued-running state and handles it safely (no crash, no zombie, no duplicate)
```

RTSP is explicitly Phase 2 (source-file `uri=` change only — no new Agent code is anticipated for that swap, but it is out of scope for this document and this approval).

## 8. Asset and Filesystem Layout — generic, profile-based

**Amendment (this revision): the layout must not be architecturally tied to YOLOv4.** The deployed
inference engine may be any TensorRT engine file in the future; YOLOv4 FP16 is only the **first
validated model profile**. A "profile" is a named, self-contained set of model-specific assets
(engine + inference config + labels + a manifest recording its validated properties). The Agent's
own code (`DeepStreamProcessManager`, `AgentSettings`) knows **only** that a profile-shaped directory
exists at a configured path — it contains **no** model-specific name, dimension, output-tensor name,
parser name, or class count anywhere in Agent source. All of that lives exclusively inside a
profile's `infer-config.txt`/`manifest.env`.

New paths under the existing `/opt/weapon-detection/` root (ADR-008 mode conventions, consistent with IP-02 §5/§8's `config/`, `database/`, `logs/`):

| Path | Mode | Owner | Contents |
|------|------|-------|----------|
| `/opt/weapon-detection/models/<profile>/model.engine` | `0750` dir / `0640` file | `weapon-detection` | The active profile's TensorRT engine, installed under a **canonical filename** (`model.engine`) — never the source filename — so no Agent or DeepStream config ever needs to know what the engine was originally called. Deployment artifact, not in Git. |
| `/opt/weapon-detection/config/deepstream/profiles/<profile>/infer-config.txt` | `0750` dir / `0640` file | `weapon-detection` | The profile's `[property]` inference config (dimensions, precision, parser, class count) — model-specific values live **only** here. |
| `/opt/weapon-detection/config/deepstream/profiles/<profile>/labels.txt` | `0640` | `weapon-detection` | The profile's class labels. |
| `/opt/weapon-detection/config/deepstream/profiles/<profile>/manifest.env` | `0640` | `weapon-detection` | The profile's manifest (§8.2) — installed alongside the profile, not just kept in Git, so `deploy-engine.sh` can re-verify it on the device at any time. |
| `/opt/weapon-detection/config/deepstream/deepstream-app.txt` | `0640` | `weapon-detection` | The **single** DeepStream application config the Agent always launches (`-c deepstream-app.txt`) — completely profile-agnostic itself; its `[primary-gie] config-file=` line simply points at whichever profile is currently active (§8.3). |
| `/opt/weapon-detection/logs/deepstream/` | `0750` | `weapon-detection` | DeepStream's own stdout/stderr capture (rotated), separate from the Agent's structured JSON log |

For the initial deployment, `<profile>` = **`yolov4-fp16`**:

- engine destination: `/opt/weapon-detection/models/yolov4-fp16/model.engine` (source: `yolov4_fp16.engine`, SHA-256 `2298d1a3d85ef9ff9d630593a01d70532df3c3b484c9aa2c9a4cc2f78081bff8`)
- inference config: `/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/infer-config.txt`
- labels: `/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/labels.txt`
- validated values carried forward unchanged: input `3;640;640`, two classes, FP16 (`network-mode=2`), `output-blob-names=BatchedNMS`, `parse-bbox-func-name=NvDsInferParseCustomBatchedNMSTLT`, DeepStream 6.2, TensorRT 8.5.2 (§6).

A new Agent setting, `WDA_DEEPSTREAM_MODEL_PROFILE` (default `yolov4-fp16`), records which profile is nominally active. **It is deliberately not read by `DeepStreamProcessManager`** — the process manager only ever knows `deepstream_config_path` (the single `deepstream-app.txt`), never a profile name (§9, "Requirements" in the implementation plan). The setting exists for observability and for a later phase (e.g. metadata extraction, which will need to know a profile's labels file) — see IP-06 for the exact decoupling test that proves this boundary in code, not just in this document.

**Rollout kill switch.** A second new setting, `WDA_DEEPSTREAM_ENABLED` (default **`false`**), gates whether the real production Agent (`main.py`) registers a `DeepStreamProcessManager` at all. A fresh or updated deployment never launches DeepStream until an operator deliberately sets it to `true` — this is deliberately more conservative than "the component exists but starts appropriately": when disabled, no `DeepStreamProcessManager` is even constructed, so the supervisor has nothing DeepStream-shaped registered. Also never read by `DeepStreamProcessManager` itself (it is a wiring-time decision, in the same composition-root layer as the `components_factory` seam in IP-06 T-74) — an entirely separate mechanism from the per-branch Operational/ReactivationRequired gating in §4.

### 8.1 What enters version control vs. what does not

| Goes in Git (`deployment/jetson/deepstream/`) | Never in Git |
|---|---|
| `deepstream-app.txt` (the single, profile-agnostic application config template) | `*.engine` (TensorRT engines — device/runtime-specific, large, effectively a build artifact) |
| `profiles/<profile>/infer-config.txt`, `profiles/<profile>/labels.txt`, `profiles/<profile>/manifest.env` — small, model-specific, **not secret** | `*.onnx` (139 MB `yolov4_resnet18.etlt.onnx` — a model asset, not source) |
| `deploy-engine.sh` — the generic engine-deployment script (§8.2) | `*.mp4`, `*.mkv` (test videos and DeepStream output recordings) |
| Documentation (this spec, the implementation plan, the deployment/validation/rollback procedures) | `cal.bin` (INT8 calibration cache — irrelevant to the FP16 path; not deployed); any generated DeepStream log |

`.gitignore` additions required: `*.engine`, `*.onnx`, `*.mp4`, `*.mkv`, plus an explicit exclusion for the existing untracked `sampleFilesFoeDeepstream/` reference directory (it stays a local-only reference copy, never committed).

### 8.2 Generic engine deployment: `deploy-engine.sh`

Replaces the earlier (YOLOv4-named) `deploy-model.sh` concept entirely. Usage:

```bash
sudo deploy-engine.sh --profile <profile-name> --engine <source.engine> --manifest <manifest-file> [--force]
```

Requirements (binding on the implementation, detailed further in IP-06):

- accepts only a regular `.engine` file (rejects directories, symlinks, and anything not ending `.engine`);
- validates the profile name against a safe pattern (lowercase alphanumeric + hyphens — the same shape as `yolov4-fp16`) before it is ever used to build a filesystem path;
- verifies the supplied engine's SHA-256 against the `ENGINE_SHA256` field in the supplied/committed manifest — a mismatch aborts before anything is installed;
- installs the engine **atomically** (temp file in the destination directory, then `mv`) under the canonical name `model.engine`;
- refuses to overwrite an existing `model.engine` for that profile without an explicit `--force`;
- sets `0640 weapon-detection:weapon-detection` on the installed engine;
- never prints the engine's binary content, only filenames/hashes/sizes;
- never infers model compatibility from the source filename — compatibility is established **only** through the manifest fields (§8.3) and the checksum, never through a name like "yolov4" appearing anywhere.

### 8.3 Model profile manifest (`manifest.env`)

Each profile's manifest is a flat `KEY=VALUE` file (parseable by shell without a JSON/YAML dependency, matching this repository's existing bash-only deployment tooling) recording, at minimum:

```ini
PROFILE_NAME=yolov4-fp16
ENGINE_SHA256=2298d1a3d85ef9ff9d630593a01d70532df3c3b484c9aa2c9a4cc2f78081bff8
ENGINE_PRECISION=FP16
INPUT_DIMENSIONS=3;640;640
CLASS_COUNT=2
LABELS_FILE=labels.txt
OUTPUT_BLOB_NAMES=BatchedNMS
PARSER_LIB=/opt/nvidia/deepstream/deepstream-6.2/lib/libnvds_infercustomparser.so
PARSER_FUNC=NvDsInferParseCustomBatchedNMSTLT
DEEPSTREAM_VERSION=6.2
TENSORRT_VERSION=8.5.2
TARGET_DEVICE=NVIDIA Orin NX (aarch64, JetPack 5.1.2 / L4T R35.5.0)
VALIDATED_DATE=2026-07-08
VALIDATED_STATUS=validated
```

`deploy-engine.sh` treats a manifest missing any of these fields as **incomplete** and refuses to deploy — it never falls back to guessing a value. Deployment verification fails safely (aborts, prints a clear reason, installs nothing) when: the manifest is incomplete; the checksum differs; the profile's referenced `infer-config.txt` is missing; `labels.txt` is missing; `PARSER_LIB` names a file that does not exist on the device; or engine/config compatibility otherwise cannot be established from the manifest. **No check ever infers compatibility from the engine's filename.**

## 9. Open Items

| ID | Issue | Resolution proposed |
|----|-------|----------------------|
| **OI-5** | `ARCH-001` and `SRS-001` are cited throughout this repository's existing specs (FS-02, FS-03, IP-01–IP-05) but **no such file exists in this repository** (`specs/` contains only `features/` and `implementation-plans/`). | This spec cites them only where the existing FS-02/IP-02 text already established the intent (Pipeline Supervisor separation, ADR-006), and does not invent new section numbers. Confirm with the user whether ARCH-001/SRS-001 exist outside this repo (and should be added) or whether these citations are now historical/aspirational only. |
| **OI-6** | The two engine filenames (`yolov4_fp16.engine`, `yolov4_resnet18_jetson.engine`) suggest two different model *families* but are actually the same architecture at two precisions. A future contributor could reasonably deploy the wrong one by name alone. | **Resolved by design (§8.2/§8.3):** `deploy-engine.sh` never infers compatibility from a filename at all — it installs under the canonical name `model.engine` and validates purely against the manifest's checksum and fields. The `yolov4-fp16` manifest's `VALIDATED_STATUS=validated` records *why* FP16 was chosen; no manifest is written for the INT8 engine, so it cannot be deployed through this tooling without someone first writing (and thereby explicitly asserting) a manifest for it. |
| **OI-7** | Only `network-mode=2` (FP16) has a proven-successful run. Whether `network-mode=1` (INT8, using `yolov4_resnet18_jetson.engine`) can be made to work is unresolved — the one attempt failed with a pipeline state-change error unrelated (on its face) to the engine itself (`Resource not found`, seen alongside the engine load). | Out of scope for Phase 1. If INT8 is wanted later (lower memory, possibly higher throughput), it needs its own debugging pass — this spec does not claim INT8 is broken, only that it is **unverified**, and Phase 1 does not depend on it. |

## 10. Verified Command Reference (for the implementation plan)

```bash
# The proven-successful invocation (source: ~/.bash_history on the Jetson, cross-checked against
# deepstream_yaml_log.txt / deepstream_jetson_log.txt "App run successful"):
deepstream-app -c ~/tao-experiments/yolo_v4/export/deepstream_app_config.txt
```

The implementation plan's `DeepStreamProcessManager` launches the **equivalent** of this command against the **managed, profile-agnostic** config path, as an explicit argv (`[str(settings.deepstream_executable_path), "-c", str(settings.deepstream_config_path)]`, i.e. `["/usr/bin/deepstream-app", "-c", "/opt/weapon-detection/config/deepstream/deepstream-app.txt"]` by default) — never a shell string, and never a profile name.
