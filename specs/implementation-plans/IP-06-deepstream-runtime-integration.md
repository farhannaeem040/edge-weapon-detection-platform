# Implementation Plan: DeepStream Runtime Integration (Phase 1)

| Field | Value |
|-------|-------|
| Plan ID | IP-06 |
| Title | DeepStream Runtime Integration — Agent-supervised DeepStream child process, local-video-file mode |
| Status | Draft — awaiting approval; **no code written yet** |
| Realizes | FS-04 (DeepStream Runtime Integration, Phase 1) |
| Governing Documents | FS-04, IP-02 (Jetson Agent Foundation — delivered), IP-05 (Device Reactivation Security — delivered) |
| Depends On | IP-02 T-31–T-41 (complete); IP-05 T-48–T-61 (complete, `OperationalComponent`/`OperationalStateCoordinator`/`AgentRuntimeSupervisor` all delivered and reusable unchanged) |
| Task ID Range | **T-70 – T-79** (T-48–T-69 consumed by IP-05) |
| Owner | Farhan Naeem |
| Explicitly Excluded | RTSP source (Phase 2); metadata extraction, detection events, Backend alerts, snapshots, recordings, WebRTC, sirens (later phases); any Backend/Angular change; any change to activation/reactivation/credential-validation behavior; committing `*.engine`/`*.onnx`/`*.mp4`/`*.mkv`; any `.claude/settings.json`/`.mcp.json` change; the deferred Windows real-Backend contract-suite issue (not touched by this plan). |

---

## 1. Objective

Give the Agent direct, safe supervision of DeepStream as a child process, reusing the `OperationalComponent`/`OperationalStateCoordinator`/`AgentRuntimeSupervisor` machinery IP-02/IP-05 already built and tested — no new lifecycle abstraction, no new locking primitive. Prove it end-to-end against a local video file before any RTSP/camera work is considered.

## 2. Grounding in the Delivered Code

| Fact | Evidence | Consequence |
|------|----------|-------------|
| `OperationalComponent` is a `Protocol` with exactly `name: str`, `async start() -> None`, `async stop() -> None` | `agent/src/weapon_detection_agent/runtime/operational_components.py:22-43` | `DeepStreamProcessManager` needs no base class — structural typing is enough, mirrored by the existing `FakeComponent` test doubles. |
| `OperationalStateCoordinator.start_operational_components()` rolls back partial failures (T-60) | `runtime/operational_state_coordinator.py:159-194, 269-283` | If DeepStream fails to start, the coordinator's existing rollback stops any component that *did* start this attempt — no new rollback code needed. |
| `enter_reactivation_required()` stops all running components in reverse order, collects failures, still locks | `runtime/operational_state_coordinator.py:196-212, 285-307` | A DeepStream `stop()` that raises does not prevent the Agent from locking — matches the "fail-closed" posture the rest of the Agent already has. |
| `AgentRuntimeSupervisor._enter_operational` calls `coordinator.start_operational_components()` then `_start_monitor()`, for Branch A/B and Branch D1/D2 | `runtime/supervisor.py:198-236, 263-276` | Registering `DeepStreamProcessManager` in the `components=(...)` tuple passed to the supervisor is the **entire** integration point — no branch logic changes. |
| Branch C (`_enter_locked`) and Branch D3/D4 (`_locked_coordinator()` directly) never call `start_operational_components()` | `runtime/supervisor.py:187, 238-261` | DeepStream is provably never started while locked — by construction, not by a DeepStream-specific check. |
| `shutdown()` always calls `coordinator.enter_reactivation_required()` if a coordinator exists, idempotently | `runtime/supervisor.py:340-377` | DeepStream stop-on-shutdown is free once registered. |
| `AgentSettings` is `env_prefix="WDA_"`, frozen, `extra="ignore"`, validated via Pydantic field validators, wrapped into `ConfigurationError` on failure | `config/settings.py` | New DeepStream settings follow this exact pattern — same file, same validation style, same error wrapping. |
| No subprocess code exists anywhere in `agent/src/` today | grep confirmed | `DeepStreamProcessManager` is genuinely new code, not a refactor. |
| Test doubles for components are plain classes with `name`/counters, passed via `components=(a,)` | `agent/tests/test_operational_state_coordinator.py:53-95`, `agent/tests/test_agent_runtime_supervisor.py:103-118` | New tests mirror this exact style; a fake subprocess replaces `asyncio.create_subprocess_exec` the same way `_FakeComponent` replaces a real component. |

## 3. Verified Jetson Facts This Plan Relies On (see FS-04 §6 for full evidence)

- DeepStream 6.2.0 at `/usr/bin/deepstream-app`; TensorRT 8.5.2 (same version that built the engines).
- **Validated engine: `yolov4_fp16.engine`** (FP16, `network-mode=2`). The `yolov4_resnet18_jetson.engine` (INT8) is **not** used — its one DeepStream run attempt failed (`debug_log.txt`, `App run failed`). Do not substitute it.
- Proven command: `deepstream-app -c <config path pointing at deepstream_app_config.txt>`.
- Proven `primary-gie` config values (`infer-dims=3;640;640`, `batch-size=1`, `num-detected-classes=2`, `cluster-mode=2`, `output-blob-names=BatchedNMS`, `parse-bbox-func-name=NvDsInferParseCustomBatchedNMSTLT`) are to be carried forward unchanged — only paths change.
- All existing working configs hard-code `/home/farhan/tao-experiments/...` — every one of them needs its paths rewritten to the managed layout before the Agent can use them.

## 4. Task Breakdown

### T-70 — Generic profile-based asset layout + `deploy-engine.sh` (no Agent code)

**Amended.** The layout and deployment tooling must not name YOLOv4 anywhere in code or script logic — only inside a profile's own data files. See FS-04 §8 for the full amended layout.

- Extend `deployment/jetson/install.sh` to provision `/opt/weapon-detection/models/` and `/opt/weapon-detection/config/deepstream/profiles/` (`0750 weapon-detection:weapon-detection`, parents only — a specific `<profile>` subdirectory is created by `deploy-engine.sh`, not by `install.sh`) and `/opt/weapon-detection/logs/deepstream/`, alongside the existing `config/`, `database/`, `logs/` directories — same self-healing chmod/chown pattern already used for those. `install.sh` also rsyncs the committed `deployment/jetson/deepstream/` tree (the `deepstream-app.txt` template and every committed profile's `infer-config.txt`/`labels.txt`/`manifest.env`) into `/opt/weapon-detection/config/deepstream/`, the same way it already rsyncs the general Jetson deployment helpers — this never touches `models/<profile>/model.engine`, which only `deploy-engine.sh` writes.
- Add `deployment/jetson/deepstream/deploy-engine.sh`:

  ```bash
  sudo deploy-engine.sh --profile <profile-name> --engine <source.engine> --manifest <manifest-file> [--force]
  ```

  - Validates `<profile-name>` against `^[a-z0-9][a-z0-9-]*$` **before** it is used in any path (rejects path traversal / injection via a crafted profile name).
  - Requires `--engine` to be a regular file (`[[ -f "$ENGINE" && ! -L "$ENGINE" ]]`), filename ending `.engine` — rejects directories, symlinks, device files, anything else.
  - Loads the manifest (flat `KEY=VALUE`, parsed with the same `sed -n 's/^KEY=//p'` style `verify.sh`/`install.sh` already use — no `jq`/Python dependency), confirms every required field (§ manifest table below) is present and non-empty — an incomplete manifest aborts, nothing installed.
  - Computes the engine's SHA-256 and compares it to `ENGINE_SHA256`; a mismatch aborts, nothing installed, and the computed hash is the only engine-derived value ever printed (never the binary itself).
  - Confirms the profile's `infer-config.txt` and `labels.txt` already exist under `/opt/weapon-detection/config/deepstream/profiles/<profile>/` (installed there by `install.sh` from the committed template) — missing either aborts.
  - Confirms `PARSER_LIB` (if the manifest sets one) exists on the filesystem — missing aborts.
  - Never inspects or trusts the source filename for any compatibility decision — compatibility is the manifest + checksum, full stop.
  - Installs atomically: writes to a temp file in `/opt/weapon-detection/models/<profile>/`, `chmod 0640`, `chown weapon-detection:weapon-detection`, then `mv -f` to `model.engine`. Refuses to overwrite an existing `model.engine` without `--force` (same non-destructive-by-default posture as `set-activation-key.sh`).
- Add committed templates for the initial `yolov4-fp16` profile:
  - `deployment/jetson/deepstream/deepstream-app.txt` — the single, profile-agnostic application config, `[primary-gie] config-file=/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/infer-config.txt` (activating a different profile later means editing this one line — a manual, deliberate operator action, not something Agent code decides).
  - `deployment/jetson/deepstream/profiles/yolov4-fp16/infer-config.txt` — every `/home/farhan/tao-experiments/yolo_v4/export/...` path rewritten: `model-engine-file=/opt/weapon-detection/models/yolov4-fp16/model.engine`, `labelfile-path=/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/labels.txt`; all model-specific values (`infer-dims=3;640;640`, `num-detected-classes=2`, `network-mode=2`, `output-blob-names=BatchedNMS`, `parse-bbox-func-name=NvDsInferParseCustomBatchedNMSTLT`) carried forward unchanged from the proven config.
  - `deployment/jetson/deepstream/profiles/yolov4-fp16/labels.txt` (verbatim — `gun`, `knife`).
  - `deployment/jetson/deepstream/profiles/yolov4-fp16/manifest.env` (FS-04 §8.3 — includes `ENGINE_SHA256=2298d1a3d85ef9ff9d630593a01d70532df3c3b484c9aa2c9a4cc2f78081bff8`, the real hash of the validated `yolov4_fp16.engine`).
- Add `.gitignore` entries (root): `*.engine`, `*.onnx`, `*.mp4`, `*.mkv`, and confirm `sampleFilesFoeDeepstream/` stays untracked (already true — add an explicit root `.gitignore` line so it can't be accidentally staged with a future broad `git add`).

### T-71 — Agent settings for DeepStream

In `agent/src/weapon_detection_agent/config/settings.py`, add (naming mirrors the existing `WDA_HTTP_TIMEOUT_SECONDS`/`WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS` style exactly):

| Field | Env var | Type / default | Notes |
|---|---|---|---|
| `deepstream_enabled` | `WDA_DEEPSTREAM_ENABLED` | `bool`, default `False` | **Amendment — the rollout kill switch.** Read only by `main.py`'s composition wiring (`default_deepstream_components_factory`, T-74) — never by `DeepStreamProcessManager`. When `False` (the default), no `DeepStreamProcessManager` is constructed at all; the supervisor registers zero DeepStream-related components. A fresh or freshly-updated deployment never launches DeepStream until an operator deliberately sets this to `true`. |
| `deepstream_executable_path` | `WDA_DEEPSTREAM_EXECUTABLE_PATH` | `Path`, default `/usr/bin/deepstream-app` | Validated to be an absolute path (same pattern as `root_path`); existence is **not** required at settings-validation time (the executable may not exist in a CI/test environment) — existence is a `start()`-time concern (T-73). |
| `deepstream_config_path` | `WDA_DEEPSTREAM_CONFIG_PATH` | `Path`, default `/opt/weapon-detection/config/deepstream/deepstream-app.txt` | Absolute path, same validation style. **This is the only path `DeepStreamProcessManager` ever sees or launches with** — see T-72's genericness requirement. |
| `deepstream_working_directory` | `WDA_DEEPSTREAM_WORKING_DIRECTORY` | `Path`, default `/opt/weapon-detection` | The explicit `cwd=` passed to the subprocess — never inherited from the Agent's ambient CWD. |
| `deepstream_stop_timeout_seconds` | `WDA_DEEPSTREAM_STOP_TIMEOUT_SECONDS` | `float`, `Field(default=10.0, gt=0)` | Mirrors the systemd unit's own `TimeoutStopSec=20` — kept shorter so the Agent's own stop completes with margin inside systemd's budget. |
| `deepstream_restart_policy` | `WDA_DEEPSTREAM_RESTART_POLICY` | `str`, default `"none"`, validated against `{"none"}` for Phase 1 | Deliberately a closed set of one value for Phase 1 — auto-restart-on-crash is a Phase-2-or-later decision (unexpected exit is *detected and logged*, T-73, but not auto-restarted, to avoid masking a real failure loop). Field exists now so the settings surface doesn't need a breaking change later. |
| `deepstream_log_path` | `WDA_DEEPSTREAM_LOG_PATH` | `Path`, default `/opt/weapon-detection/logs/deepstream/deepstream.log` | Where stdout/stderr are captured (T-73) — a distinct file from the Agent's own structured JSON log. |
| `deepstream_model_profile` | `WDA_DEEPSTREAM_MODEL_PROFILE` | `str`, default `"yolov4-fp16"`, validated against the same safe-name pattern `deploy-engine.sh` uses (`^[a-z0-9][a-z0-9-]*$`) | **Amendment:** records which profile is nominally active, for observability and for a future phase (e.g. metadata extraction needing a profile's `labels.txt`). **`DeepStreamProcessManager` never reads this field** — see T-72's genericness requirement and T-75's decoupling test. Which engine actually runs is determined entirely by `deepstream_config_path`'s file content, assembled at deployment time by `deploy-engine.sh`/`install.sh`, never by Agent code branching on a profile name. |

No RTSP credential field is added in Phase 1 (no RTSP source exists yet — FS-04 §1.2/§7).

**Tests:** extend `agent/tests/test_settings.py` — valid construction, each field's default, each field's `WDA_` override, each validation-failure path wrapped into `ConfigurationError` naming the right field, exactly matching the existing tests' structure for `http_timeout_seconds`/`credential_validation_interval_seconds`. Include an explicit test that an invalid `deepstream_model_profile` (uppercase, path separator, leading hyphen) is rejected with the same error-wrapping discipline.

### T-72 — `DeepStreamProcessManager` — core start/stop

New module `agent/src/weapon_detection_agent/deepstream/process_manager.py`. **Class name is `DeepStreamProcessManager` — not `YoloV4ProcessManager` or any model-specific name** (the class supervises a process, not a model).

**Genericness requirement (binding):** this module's source contains **no** YOLOv4-specific token — no model name, no `640`/`3;640;640`-shaped dimension, no `BatchedNMS`/output-tensor name, no parser name (`NvDsInferParseCustomBatchedNMSTLT`), no class count, and **no reference to `deepstream_model_profile`**. Everything it knows is: an executable path, a config path, a working directory, a stop timeout, a restart policy, and a log path — the six fields already in this sentence, and nothing else. T-75 includes a static test enforcing this.

- Constructor takes exactly the six knows-only settings (`deepstream_executable_path`, `deepstream_config_path`, `deepstream_working_directory`, `deepstream_stop_timeout_seconds`, `deepstream_restart_policy`, `deepstream_log_path` — **not** `deepstream_model_profile`) plus an injectable subprocess factory (default `asyncio.create_subprocess_exec`) — the same dependency-injection seam `BackendActivationClient`/`CredentialValidationClient` already use for testability (no real DeepStream needed in unit tests).
- `name` → a fixed safe string, e.g. `"deepstream"` (never derived from a path or argv — nothing variable to leak).
- `async start()`:
  - Reject with a typed error (e.g. `DeepStreamAlreadyRunningError`) if a process handle is already tracked and alive — no duplicate spawn (Behavior #6).
  - Build the argv explicitly: `[str(executable_path), "-c", str(config_path)]` — **never** a shell string, **never** `shell=True` (Behavior #9).
  - Launch via the injected factory with `cwd=str(working_directory)`, stdout/stderr redirected to the T-73 log sink, stdin closed/devnull.
  - Store the resulting handle and PID; record a monotonic start timestamp.
- `async stop()`:
  - No-op (idempotent) if no process is tracked (Behavior #7).
  - Send `SIGTERM`; await process exit up to `deepstream_stop_timeout_seconds`; on timeout send `SIGKILL`; always `await process.wait()` afterward (reap — no zombie, Behavior requirement).
  - Clear the tracked handle only after the process is confirmed exited.
- A safe read-only property (e.g. `is_running` / `pid`) for observability — never exposes the full `Process` object outside the module.

### T-73 — Unexpected-exit detection and safe logging

- A background watcher (an `asyncio.Task` created in `start()`, mirroring the pattern `AgentRuntimeSupervisor._start_monitor`/`_run_monitor` already uses for the credential-validation monitor) awaits `process.wait()`. If it completes **without** `stop()` having been called first, this is an unexpected exit: log it (return code, safe component name, no argv/URL echoed) and leave `DeepStreamProcessManager` in a clean not-running state — it does **not** restart itself (T-71's `restart_policy="none"`) and it does **not** raise into the coordinator (an unexpected exit is observed, not treated as a fatal Agent error — matches FS-04 Behavior #8).
- stdout/stderr are redirected to `deepstream_log_path` (opened in append mode, directory created with the T-70 layout's mode) — **not** merged into the Agent's own structured JSON logger, since DeepStream's own output format is foreign and could theoretically contain a misconfigured RTSP URL in a future phase; keeping the streams separate is a deliberate credential-safety boundary, not an oversight.
- No log line ever includes the full argv, the config file's *contents*, or an RTSP URL (moot in Phase 1 — no RTSP source — but the log-construction code is written so Phase 2 doesn't have to revisit it).

### T-74 — Register as an `OperationalComponent`

Today, `runtime/startup.py::_start()` constructs `AgentRuntimeSupervisor(..., components=())` with a **hardcoded empty tuple** — there is no factory seam for components yet, unlike the Backend/validation clients. Introducing one carelessly is a real blast-radius risk: every existing simulated-integration and real-Backend-contract test calls `create_app()`/`create_lifespan()` **without** overriding a components argument (it doesn't exist today), so if the *default* suddenly became "build a real `DeepStreamProcessManager`," every one of those tests would try to spawn `/usr/bin/deepstream-app` on a machine that doesn't have it — breaking the entire existing test suite as a side effect of a Phase-1 process-supervision feature. That is not an acceptable cost of this plan.

**Design decision:** add the seam with a **safe, backward-compatible default**, and wire the real DeepStream component only at the one place that is genuinely production:

- `runtime/startup.py`: add `ComponentsFactory = Callable[[AgentSettings], Sequence[OperationalComponent]]`, a `default_components_factory(settings) -> Sequence[OperationalComponent]` that returns `()` (identical to today's hardcoded behavior), and a `components_factory: ComponentsFactory = default_components_factory` parameter on `create_lifespan()`, threaded into `_start()`'s `AgentRuntimeSupervisor(..., components=components_factory(settings))`.
- `app.py`: add the matching `components_factory` parameter to `create_app()`, defaulting the same way, passed through to `create_lifespan()`.
- `deepstream/process_manager.py`: add `default_deepstream_components_factory(settings: AgentSettings) -> tuple[OperationalComponent, ...]` returning `(DeepStreamProcessManager(settings),)`.
- `main.py` (the **real** Uvicorn entrypoint, and the only place this matters for the actual Jetson): change `app = create_app()` to `app = create_app(components_factory=default_deepstream_components_factory)`.

Net effect: the real production Agent gets DeepStream supervision exactly as FS-04 requires; every existing test keeps its exact current behavior (`components=()`) with **zero** test-file changes required as a side effect of this task; and T-75's new tests exercise `DeepStreamProcessManager` by passing it explicitly into `components=(...)`, the same pattern the existing coordinator/supervisor tests already use. No change to `AgentRuntimeSupervisor`, `OperationalStateCoordinator`, or any startup branch.

**Second, independent safety layer — `WDA_DEEPSTREAM_ENABLED` (default `False`, T-71).** Even `default_deepstream_components_factory` itself only constructs a `DeepStreamProcessManager` when `settings.deepstream_enabled` is `True`; otherwise it also returns `()`. So a freshly deployed or freshly updated real Jetson Agent — `main.py`'s explicit override notwithstanding — still launches nothing until an operator deliberately flips this setting. This is deliberately redundant with the `components_factory` default: one layer protects every test and non-production caller of `create_app()`, the other protects the real production Agent on first rollout.

### T-75 — Unit tests with a fake subprocess (no real DeepStream)

New `agent/tests/test_deepstream_process_manager.py`, using a fake subprocess factory (an injectable stand-in for `asyncio.create_subprocess_exec`, controllable like the existing `_FakeComponent`'s `start_gate`/`stop_fails` knobs):

| Test | Proves |
|---|---|
| `start()` launches with the exact expected argv, `cwd`, no `shell=True` | Behavior #9, the core safety property |
| `start()` twice raises the duplicate-start error, only one process launched | Behavior #6 |
| `stop()` sends SIGTERM, process exits before timeout, is reaped, no SIGKILL sent | Normal graceful stop |
| `stop()` on a process that ignores SIGTERM sends SIGKILL only after the configured timeout elapses | Forced-kill path |
| `stop()` when nothing is running is a no-op | Behavior #7 |
| A process that exits on its own (fake sets exit code without `stop()` being called) is detected as an unexpected exit and logged; `is_running` becomes `False`; no exception escapes into the coordinator | Behavior #8 |
| Cancelling the watcher task (Agent shutdown mid-wait) does not leave an orphaned process or hang | Clean cancellation, matches the credential-validation monitor's own cancellation contract |
| No RTSP/credential-shaped string ever appears in a captured log line (Phase 1 has none, but the log-capture path itself is asserted not to echo argv verbatim beyond the two safe fields) | Log-safety discipline, forward-compatible with Phase 2 |
| `OperationalStateCoordinator.start_operational_components()` with a real coordinator and a `DeepStreamProcessManager` (fake subprocess) actually calls `start()` | Coordinator integration, not just the component in isolation |
| `enter_reactivation_required()` calls `stop()` and the coordinator locks even if `stop()` raises | T-60 rollback/failure-tolerance reused correctly |
| A supervisor constructed with `components=(deepstream,)` and a **locked** startup branch (Branch C) never calls `start()` | FS-04 Behavior #2's negative case — proven, not assumed |
| A supervisor that starts (Branch A) then a simulated confirmed rejection locks it — `stop()` was called | FS-04 Behavior #3 |
| Restart/reactivation after a lock starts DeepStream exactly once more (not twice, not zero) | FS-04 Behavior #5/#6 combined |
| **A static source-text scan of `process_manager.py` contains none of: `yolov4`, `640`, `BatchedNMS`, `NvDsInferParseCustomBatchedNMSTLT`, `num-detected-classes`, `deepstream_model_profile` (case-insensitive)** | The genericness requirement (T-72), enforced mechanically, not just by review |
| Two `DeepStreamProcessManager` instances constructed with different `deepstream_config_path` values (two fixture files simulating two different deployed profiles) launch with correspondingly different `-c` argv values, with **no other change** to the manager's behavior or code path | **Amendment requirement:** "changing the configured profile/path changes the selected engine configuration without changing process-manager code" — proven, not asserted |
| Constructing `DeepStreamProcessManager` and asserting it exposes no attribute, parameter, or method referencing `profile` | Confirms `WDA_DEEPSTREAM_MODEL_PROFILE` truly never reaches this class, closing the loop on the settings-level note in T-71 |
| `default_components_factory` (T-74) returns `()`; a `create_app()`/`create_lifespan()` call with no override behaves identically to today (existing simulated/contract tests are unaffected — a regression check, not a new behavior) | Confirms the blast-radius mitigation in T-74 actually holds |
| `default_deepstream_components_factory` with `deepstream_enabled=False` (the settings default) returns `()` — no `DeepStreamProcessManager` constructed at all | The `WDA_DEEPSTREAM_ENABLED` kill switch (T-71/T-74) actually gates construction, not just startup |
| `default_deepstream_components_factory` with `deepstream_enabled=True` returns exactly one `DeepStreamProcessManager` built from the given settings | `main.py`'s real wiring is unit-testable without starting Uvicorn |

All of the above run in the **default** fast test suite — no real `deepstream-app`, no Jetson, no GPU required, consistent with IP-02's existing Layer A (simulated) test philosophy.

**`deploy-engine.sh` fail-safe verification (separate, bash-level tests or a documented manual check matrix — T-76/T-78):** an incomplete manifest (missing field), a checksum mismatch, a missing `infer-config.txt`/`labels.txt` for the target profile, and a missing `PARSER_LIB` target each abort cleanly with no partial install and no binary content printed. An unsafe profile name (`../etc`, `YOLOv4`, `profile;rm`) is rejected before any path is constructed.

### T-76 — Opt-in Jetson integration verification script

- `deployment/jetson/deepstream/verify-deepstream.sh`: **not** run by the default test suite or `verify.sh` (T-41's existing script stays untouched — DeepStream verification is deliberately separate, matching how the real-Backend contract suite is opt-in via `WDA_RUN_BACKEND_CONTRACT_TESTS`).
- Confirms (read-only where possible): the managed engine file exists and matches the T-70 manifest checksum; the managed config files exist and their `model-engine-file`/`labelfile-path` point at paths that exist; `deepstream-app` is on `PATH`; then (opt-in, explicit flag) actually launches DeepStream against the managed config + local test video for a bounded duration, checks for `App run successful` or a graceful timeout-then-stop, and tails the last N lines of `deepstream_log_path` on failure.
- This script is the bridge between T-75's fake-subprocess unit tests and a real end-to-end proof on the actual device — it is run manually by the operator, not by CI.

### T-77 — Local-video-file end-to-end proof

- With the managed layout deployed (T-70's `deploy-engine.sh --profile yolov4-fp16 --engine yolov4_fp16.engine --manifest manifest.env` run once, manually, by the operator — mirroring how `set-activation-key.sh` is operator-run, not automated) and a local test video staged, start the real Agent (real systemd service, real activation state) and confirm via `verify-deepstream.sh` (T-76) and `ps`/`pgrep` that exactly one `deepstream-app` process is running while `Operational`, and that it stops on `systemctl stop weapon-detection-agent`.
- This is a manual verification task, not new code — its output feeds the acceptance criteria (§6) and the report back to the user.

### T-78 — Documentation

- This spec (FS-04) and this plan (IP-06) — done.
- `deployment/jetson/deepstream/README.md`: model-asset deployment instructions (how to run `deploy-engine.sh` for a profile, how a profile's manifest is produced/updated, how to add a *new* profile), local-video validation procedure (how to run T-76's script), and the rollback procedure (§7 below).
- Update `deployment/jetson/README.md`'s file-contents table to mention the new `deployment/jetson/deepstream/` subdirectory, consistent with how it already documents `set-activation-key.sh` etc.

### T-79 — `AgentSettings`/README cross-check and final review

- Re-run `agent/tests/test_settings.py` and confirm no existing test's assumptions about the settings surface broke (e.g. a test that iterates all fields expecting a specific count).
- Confirm `dotnet build`/Backend is untouched (this plan touches no Backend/Angular file) — a cheap final sanity check before requesting approval to implement.

## 5. Testing Strategy Summary

| Layer | What | Real DeepStream needed? |
|---|---|---|
| Unit (T-75) | `DeepStreamProcessManager` in isolation + coordinator/supervisor integration, fake subprocess | No |
| Opt-in Jetson script (T-76) | Static checks (files/paths/checksum) + optional real launch | Optional flag; yes when used |
| Manual end-to-end (T-77) | Real Agent, real systemd, real DeepStream, local video | Yes |

## 6. Acceptance Criteria

1. `DeepStreamProcessManager` satisfies the `OperationalComponent` protocol and is registered with `AgentRuntimeSupervisor`.
2. Branch A/B (fresh/re-activation) and Branch D1/D2 (validated operational) start DeepStream; Branch C and D3/D4 (locked) never do — proven by T-75's supervisor-level tests, not just by code inspection.
3. A confirmed credential rejection (simulated in T-75, real in a future connected test) stops DeepStream via the existing coordinator lock path.
4. Agent shutdown stops DeepStream; no zombie or orphaned `deepstream-app` process remains (`stop()` always reaps).
5. Reactivation after a lock starts DeepStream again exactly once.
6. No more than one DeepStream process exists at any time, under any sequence of start/stop calls exercised by the tests.
7. DeepStream is launched with an explicit argv, no `shell=True`, and no RTSP/credential value in any log line (vacuously true in Phase 1 — no RTSP source exists — but the code path is structured so this stays true in Phase 2).
8. The full `agent` fast test suite (`python -m pytest`) passes with the new tests included, with no real DeepStream, GPU, or Jetson required.
9. On the real Jetson, with the managed asset layout deployed, the Agent starts DeepStream against the local test video, and DeepStream reaches the same "processing frames" state independently proven in FS-04 §6 (T-77, manual).
10. No `*.engine`, `*.onnx`, `*.mp4`, or `*.mkv` file is committed to Git at any point in this work.
11. `DeepStreamProcessManager`'s source contains no YOLOv4-specific token (name, dimension, output-tensor name, parser name, class count) and no reference to `deepstream_model_profile` — enforced by a static test (T-75), not just review.
12. Swapping which profile is active (a different `deepstream_config_path`, pointing at a differently-assembled `deepstream-app.txt`) changes which engine DeepStream loads, with zero change to `DeepStreamProcessManager` code — proven by T-75's decoupling test.
13. `deploy-engine.sh` refuses to install when the manifest is incomplete, the checksum mismatches, the profile's `infer-config.txt`/`labels.txt` is missing, or a referenced parser library does not exist — and never infers compatibility from the source filename.
14. `WDA_DEEPSTREAM_ENABLED` defaults to `False`; a real production Agent (`main.py`'s wiring) started with no override registers zero DeepStream-related components.

## 7. Risks and Rollback Plan

| Risk | Mitigation |
|---|---|
| The FP16 engine, though built on this exact device, was built five-plus days before this integration work and the device's TensorRT/driver stack could have changed (a package update). | T-76's verification script re-confirms `App run successful` (or at least clean startup) on the managed copy before T-77 is declared done — the plan does not assume the five-day-old proof still holds without re-checking. |
| `DeepStreamProcessManager`'s `stop()` could hang if `deepstream-app` ignores both SIGTERM and does not exit even after the process is confirmed killed (rare, but GPU driver processes can wedge). | The `SIGKILL` path plus `await process.wait()` with no additional timeout on the *kill* (only the graceful phase is time-bounded) — if this still wedges, it manifests as `systemctl stop` taking until `TimeoutStopSec=20` and then systemd force-kills the whole cgroup, which is systemd's existing safety net, unmodified by this plan. |
| A coordinator failure while starting a **later** component in a future phase could leave DeepStream started-then-rolled-back rapidly (flapping) if components are added carelessly after this one. | Out of scope for Phase 1 (DeepStream will likely be the *only* operational component for some time); noted here so a future phase's author sees it. |
| Deploying the wrong engine (`yolov4_resnet18_jetson.engine`, unproven/failing) by mistake, given the confusing filenames (OI-6 in FS-04). | `deploy-engine.sh` never trusts a filename at all — it installs under the canonical `model.engine` name and validates purely against the manifest's checksum; no manifest exists for the INT8 engine, so it cannot be deployed without someone first writing one (an explicit, reviewable act). |
| **Rollback procedure** if Phase 1 causes any Agent instability: `systemctl stop weapon-detection-agent`; remove/rename `/opt/weapon-detection/config/deepstream/` and `/opt/weapon-detection/models/` (or simply never run `deploy-engine.sh`); revert `main.py`'s `components_factory=default_deepstream_components_factory` override back to the plain `create_app()` call (T-74's default-safe design makes this a one-line revert, not a multi-file change); or redeploy the pre-Phase-1 Agent code via `update.sh` from the prior commit. Because `DeepStreamProcessManager` is an *addition* wired only through `main.py`'s explicit override and touches no activation/identity/database code path, rollback never risks Device Identity, `ActivatedAt`, or the SQLite store — the same isolation IP-05's operational components already guaranteed. |

## 8. What Happens Next (not part of this approval)

Phase 2 (RTSP source swap), metadata extraction, Backend alert submission, snapshots/recordings, and their own feature specs are explicitly future work, each requiring their own approval before implementation, per the Development Workflow (Requirements → Specification → Implementation Plan → Implementation → Review → Testing → Documentation) this repository already follows.
