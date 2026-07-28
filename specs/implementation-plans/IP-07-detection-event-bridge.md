# Implementation Plan: DeepStream Detection Event Bridge

| Field | Value |
|-------|-------|
| Plan ID | IP-07 |
| Title | Detection Event Bridge — pyds metadata probe, UDS transport, validation/dedup/persistence in the Agent |
| Status | Complete — implemented and validated on real Jetson hardware; T-80–T-93 executed (T-92 incident fix, T-93 tracker-removal evaluation) |
| Realizes | FS-05 |
| Governing Documents | FS-05, ADR-005 (frozen — Agent↔DeepStream Unix domain socket, no broker), IP-06 (delivered T-70–T-79), IP-02 (schema/repository pattern), IP-05 (`OperationalComponent`/coordinator/supervisor) |
| Depends On | IP-06 fully delivered and deployed with `WDA_DEEPSTREAM_ENABLED=true`; OI-8 (pyds availability) resolved by T-80's original gate (done) — T-80's venv-migration correction (this revision, FS-05 §4.6) completes before T-88+ resumes |
| Task ID Range | **T-80 – T-91** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Everything FS-05 §1.2/Explicitly-excluded lists — Backend ingestion, SQL Server, Angular, snapshots, recordings, WebRTC, sirens, outbox/retry beyond the `pending` column, object tracking, any activation/reactivation/credential-validation change, any change to `deepstream-rtsp-route.service`. |

---

## 1. Objective

Give the Agent a reliable, architecture-compliant (ADR-005) path from real `NvDsObjectMeta` to a
validated, deduplicated, SQLite-persisted `DetectionEvent`, without touching the proven RTSP
pipeline's engine/config/route-fix, and without weakening the DeepStream lifecycle guarantees IP-06
already proved (exactly one process, clean stop, no orphan).

## 2. Grounding in Delivered Code

| Fact | Evidence | Consequence |
|---|---|---|
| `DeepStreamProcessManager` execs `(executable_path, "-c", config_path)` with no model-specific knowledge | `deepstream/process_manager.py:95` | Pointing `deepstream_executable_path` at a wrapper script requires zero changes to this class — T-72's genericness requirement is preserved by construction. |
| `default_deepstream_components_factory` is the only place DeepStream is wired into `main.py`, gated by `deepstream_enabled` | `deepstream/process_manager.py:185-209` | A parallel `default_detection_components_factory`, gated by its own `detection_events_enabled`, follows the identical shape — no change to the existing factory. |
| `OperationalStateCoordinator`/`AgentRuntimeSupervisor` accept any `Sequence[OperationalComponent]`; components are started/stopped/rolled-back generically | `runtime/operational_state_coordinator.py`, `runtime/supervisor.py` | `DetectionIngestHandler` registers as a second tuple element in `main.py`'s `components=(...)` — no coordinator/supervisor code changes. |
| `persistence/schema.py`'s migration pattern is forward-only, one `SchemaVersion` int, DDL in one transaction, each version's statements frozen at module scope | `persistence/schema.py:1-122` | v2→v3 (adding `DetectionEvent`) is one more `_migrate_v2_to_v3` function and one more `if version == 2:` branch in `initialize_schema` — same shape, no new dependency. |
| `DeviceIdentityRepository` takes `database_path` XOR `connection_factory`, opens a fresh short-lived connection per call, wraps writes in `transaction()` | `persistence/device_identity_repository.py` | `DetectionEventRepository` copies this constructor/connection shape exactly. |
| `config/paths.py` already reserves (but does not create) a `runtime/` directory in `DEFERRED_DIRECTORIES` | `config/paths.py:54-60` | The detection socket's directory is this feature's first writer — add it to `AgentPaths.managed_directories`, remove it from the deferred list, mode `0700` (matches `database_dir`, since a stray reader on the socket path is as sensitive as a stray DB reader). |
| `AgentSettings` validation/error-wrapping pattern (`_describe`, field-name-only messages) is fixed and reused by every new field | `config/settings.py:238-255` | New `WDA_DETECTION_*` fields need only new `Field(...)`/`field_validator` entries — no new error-handling code. |
| No `pyds` import anywhere in `agent/src/` today; the Agent's own venv is Python ≥3.10, DeepStream's `pyds` targets the Jetson's system Python 3.8 | grep confirmed; `deployment/jetson/README.md`'s Python-version note | The Bridge is **not** part of the `weapon_detection_agent` package and is never imported by it — it is a fully separate application (`deployment/jetson/deepstream/bridge/`, deployed to `/opt/weapon-detection/deepstream-bridge/`) with its own venv and its own Python 3.8 interpreter, communicating only over the UDS. This keeps the Agent/DeepStream process boundary (§9.2 SWA doc) intact and, per FS-05 §4.6, makes it structurally impossible for `pyds` to reach the Agent's own dependency graph. |
| `pyds` 1.1.6 confirmed importable on the real Jetson (`NvDsObjectMeta`/`NvDsFrameMeta` both present) as both `farhan` (interim) and `weapon-detection` (production account, via `sudo -u weapon-detection -H env PYTHONPATH=... /usr/bin/python3 -c ...`) | IP-07 T-80 execution, `deployment/jetson/deepstream/README.md` §"Installing `pyds`" | OI-8 is resolved — `pyds`/DS 6.2/JetPack 5.1.2/Python 3.8 is a working combination. The `farhan`-account copy has since been removed. The `weapon-detection`-account copy landed at `/opt/weapon-detection/runtime/python3.8/site-packages`, which this revision (FS-05 §4.6) now corrects — `runtime/` is reserved for transient objects only, so T-80 gains a follow-up migration step (below) moving the install into the Bridge's own venv and removing the `runtime/`-rooted copy. |

## 3. Verified Jetson Facts This Plan Relies On

- DS 6.2 ships `libnvds_amqp_proto.so`, `libnvds_kafka_proto.so`, `libnvds_azure_proto.so`,
  `libnvds_redis_proto.so`, and `libnvds_msgconv.so` under `/opt/nvidia/deepstream/deepstream-6.2/lib/`
  — **no** MQTT or local-socket adaptor. This confirms FS-05 §4.1's rejection of option 1.
  `pyds` is **not currently installed**; `python3 -c "import pyds"` fails on the system interpreter
  (`python3.8`). `gi`/`Gst` (GStreamer bindings) **are** present (`GStreamer 1.16.3`).
- `redis-server` is not installed and is not needed (rejected, §4.1 above).
- The current `/opt/weapon-detection/config/deepstream/deepstream-app.txt` and its `yolov4-fp16`
  profile (`infer-config.txt`, `labels.txt`, `manifest.env`) are exactly the IP-06-delivered,
  T-77-proven files — this plan reads them, never edits their inference-relevant values.

## 4. Task Breakdown

### T-80 — Resolve OI-8: confirm `pyds` install path on the real Jetson (no Agent code)

**Original gate — complete.** `pyds` 1.1.6 (the release matching DeepStream SDK 6.2 / Ubuntu 20.04 /
Python 3.8) was confirmed importable on the real Jetson, first as `farhan` (`pip install --user`,
since removed) and then, per the follow-up production-account check, as `weapon-detection` via
`/usr/bin/python3` with `NvDsObjectMeta`/`NvDsFrameMeta` both present. **Hard gate: passed.** (If it
had failed, FS-05 §4.1's option 3 — a compiled probe plugin — would have become the fallback,
requiring its own FS/IP revision; that path was not needed.)

**Correction (this revision, FS-05 §4.6) — install location superseded, migrate before T-88 (T-81 and
T-82 are already complete and are unaffected by this correction — they touch `AgentSettings` and
`config/paths.py` only, neither of which this migration changes).** The verification above installed
`pyds`/`pgi` into `/opt/weapon-detection/runtime/python3.8/site-packages`, owned by `weapon-detection`.
That directory is reserved exclusively for transient runtime objects (the detection socket) —
persistent dependencies do not belong there, and the Bridge is now a fully separate deployable
application with its own venv (§4.6). Before T-88 begins:

1. Build the Bridge's venv at `/opt/weapon-detection/deepstream-bridge/venv/` —
   `python3.8 -m venv --system-site-packages /opt/weapon-detection/deepstream-bridge/venv` (the
   `--system-site-packages` flag lets it see the already-installed system `python3-gi`/GStreamer
   bindings without reinstalling them).
2. Install the pinned `pyds` 1.1.6 wheel and `pgi` **inside that venv** via its own `pip` (not
   `--user`, not `--target=`, not a global `PYTHONPATH`).
3. Re-run the exact production verification, this time through the venv's own interpreter with no
   `PYTHONPATH` override needed (the venv makes `pyds` importable by construction):
   `sudo -u weapon-detection -H /opt/weapon-detection/deepstream-bridge/venv/bin/python -c "import pyds; ..."`.
4. Remove the now-superseded `/opt/weapon-detection/runtime/python3.8/site-packages` install
   entirely (`sudo rm -rf`) — `runtime/` must contain only `detection.sock` once this feature is live.
5. Update `deployment/jetson/deepstream/README.md`'s "Installing `pyds`" section to describe the venv
   location as authoritative and mark the `runtime/`-rooted install as a corrected, removed interim
   step (not delete the history — keep it as a recorded lesson, matching how this document already
   records the earlier `farhan`/`~/.local` correction).

This migration is scoped as part of T-80 (not a new task) because it corrects T-80's own deliverable
before anything downstream is built on top of it — T-88/T-89 assume the venv location from the start,
they do not migrate to it.

### T-81 — Agent settings for detection events

`agent/src/weapon_detection_agent/config/settings.py`: add the six `WDA_DETECTION_*` fields from
FS-05 §9, following the exact `WDA_DEEPSTREAM_*` style (frozen model, `Field(..., ge=/gt=/le=)`
validators, `field_validator` for `detection_camera_id` non-blank-when-enabled). Extend
`agent/tests/test_settings.py` with the same defaults/override/invalid-value coverage T-71 used for
DeepStream settings — one test per new field's default, one per `WDA_` override, one per validation
failure (confidence < 0, confidence > 1, cooldown ≤ 0, queue capacity ≤ 0, blank camera id while
enabled).

### T-82 — `runtime/` directory provisioning

`config/paths.py`: add `runtime_dir` (mode `0700`, matching `database_dir`) to
`AgentPaths.managed_directories`, remove `"runtime"` from `DEFERRED_DIRECTORIES`, add a
`detection_socket_file` path property. Extend `agent/tests/test_paths.py` (or equivalent) to assert
the new directory is created/mode-enforced and no longer listed as deferred — a regression check
mirroring T-70/T-34's own directory tests.

### T-83 — `DetectionEvent` domain model and pure validator

New `agent/src/weapon_detection_agent/detection/models.py` (dataclass, frozen, snake_case — mirrors
`persistence/models.py`'s `DeviceIdentity`) and
`agent/src/weapon_detection_agent/detection/validation.py` (pure functions: raw dict → `DetectionEvent`
or a typed `DetectionRejection` with a safe `reason` enum — `unknown_class`,
`confidence_below_threshold`, `confidence_out_of_range`, `non_finite_value`, `negative_value`,
`malformed_field`). Implements FS-05 §5's full validation/clamping rules. No I/O, no `pyds`, no
socket code — fully unit-testable per the "Event model" test list in FS-05/the original task brief
(valid gun/knife event, unknown class, confidence below threshold, confidence > 1, NaN/inf rejection,
negative bbox, bbox clamping, tz-aware timestamp).

### T-84 — `DetectionCooldownTracker`

New `agent/src/weapon_detection_agent/detection/cooldown.py`: a small class keyed on
`(device_id, camera_id, class_name)`, using an injectable monotonic clock (default `time.monotonic`,
matching the DI seam pattern used elsewhere), `allow(key) -> bool` plus a `record(key)`/combined
`try_accept(key) -> bool`. Tests: first-accept, within-cooldown-suppressed, after-cooldown-accepted,
independent classes, independent cameras, and an injected fake clock proving monotonic (not
wall-clock) behavior — matching FS-05 §6 and the task's "Duplicate suppression" test list exactly.

### T-85 — Schema v2 → v3 migration and `DetectionEventRepository`

`persistence/schema.py`: add `_DETECTION_EVENT_V3_DDL`, `_migrate_v2_to_v3`, bump
`CURRENT_SCHEMA_VERSION = 3`, add the `if version == 2:` branch — same idempotent, single-transaction
shape as v1→v2. New `agent/src/weapon_detection_agent/persistence/detection_event_repository.py`:
`insert(event: DetectionEvent) -> None` (parameterized, transactional, unique on `EventId` via the
primary key — a duplicate insert raises a typed `DetectionEventAlreadyExistsError`, defensive-only
since the cooldown tracker should make it unreachable in practice) and `list_recent(limit: int)` for
the diagnostics query (FS-05 §8). Tests mirror T-36/IP-05 §16.1's repository suite: migration from a
v2 fixture database, insert + retrieve, unique event id enforcement, rollback-on-failure (a
deliberately-failing statement proving the transaction rolls back), confirmation that existing
`DeviceIdentity`/`ConfigCache` rows are untouched by the migration, `pending` status stored by default.

### T-86 — `DetectionIngestHandler` (UDS server, `OperationalComponent`)

New `agent/src/weapon_detection_agent/detection/ingest_handler.py`. Constructor takes the socket
path, queue capacity, camera id, min confidence, cooldown seconds, a label-resolution source (the
active profile's `labels.txt`, read once at `start()`), the repository, and a clock — all explicit,
injectable (same DI seam style as `DeepStreamProcessManager`/`BackendActivationClient`).

- `start()`: binds an `asyncio` Unix domain socket server at the configured path (removing a stale
  socket file first, matching how `connect()` already handles a "must not silently paper over a bad
  prior state" posture), mode `0600`. Spawns the accept loop as a tracked `asyncio.Task` (same pattern
  as `DeepStreamProcessManager._watch`).
- Per connection: reads a 4-byte length prefix, rejects anything over a fixed max-frame-size constant
  before reading the body (bounded message size, task requirement), parses JSON, checks
  `schema_version` (reject unknown versions safely), runs T-83's validator, then T-84's cooldown
  check, then T-85's repository insert — each stage's outcome maps to one of the FS-05 §8 log events.
  A malformed connection is closed without raising into the accept loop (one bad client never takes
  down the listener).
- An internal bounded `asyncio.Queue` (size `detection_queue_capacity`) decouples socket reads from
  DB writes so a slow SQLite write never blocks accepting the next frame's message; a full queue
  drops the newest item with a rate-limited warning (never blocks, never crashes).
- `stop()`: closes the listener, cancels the accept loop and any in-flight connection tasks, drains/
  cancels the queue-consumer task, removes the socket file — idempotent, mirrors
  `DeepStreamProcessManager.stop()`'s reap discipline exactly (no leaked task/socket, task requirement).
- `name` → `"detection-ingest"`.

Tests (new `agent/tests/test_detection_ingest_handler.py`, real `asyncio` UDS over a temp path, no
DeepStream/pyds needed): valid message accepted end-to-end (received→persisted), malformed JSON
rejected, oversized payload rejected before parsing, unknown schema version rejected, one client
disconnecting mid-message doesn't affect another, clean `stop()` with an in-flight connection doesn't
hang, `start()`/`stop()` twice is safe, coordinator integration (`start_operational_components()`
actually calls `start()`; `enter_reactivation_required()` stops it cleanly even if a connection is
open), a locked startup branch never starts it (mirrors T-75's supervisor-level DeepStream tests).

### T-87 — Wire into `main.py` behind the same two-layer kill switch

`detection/ingest_handler.py`: add `default_detection_components_factory(settings) -> tuple[...]`,
returning `()` when `detection_events_enabled` is `False` (mirrors
`default_deepstream_components_factory` exactly). `main.py`: change
`components_factory=default_deepstream_components_factory` to a small composed factory returning
both components' tuples concatenated (DeepStream first, so it starts before the ingest handler and
stops after it — order matters for §9.2's boundary, though neither actually depends on the other's
liveness to start). `runtime/startup.py`'s own `default_components_factory` (returning `()`) stays
untouched — same blast-radius protection T-74 already established, re-verified by a regression test
that `create_app()`/`create_lifespan()` with no override still behaves identically.

### T-88 — The `deepstream_bridge` application package (Jetson deployment artifact, not Agent code)

New committed source tree `deployment/jetson/deepstream/bridge/app/deepstream_bridge/` — a proper,
installable Python package (not a loose script), with roughly:

| Module | Responsibility |
|---|---|
| `cli.py` | `argparse` definition for the real entry point: `--socket-path PATH` (required) and `--config PATH` (required) — this is the CLI FS-05 §4.3 documents, not a positional `-c` argument. |
| `pipeline.py` | Builds the `Gst` pipeline reconstructing the proven element chain from the active profile's `deepstream-app.txt`/`infer-config.txt` (source → `nvstreammux` → `nvinfer` with the profile's unmodified `infer-config.txt` → `nvvideoconvert` → `nvdsosd` → the existing RTSP output branch, tracker still disabled). |
| `probe.py` | The pad probe on the OSD element's sink pad: extracts `NvDsBatchMeta`/`NvDsFrameMeta`/`NvDsObjectMeta` per frame (the actual metadata API — no console/PERF-log parsing, no OCR, no re-decoding the outgoing RTSP stream) and builds the minimal wire payload (`schema_version`, `class_id`, `source_id`, `frame_number`, `frame_width`/`height`, `confidence`, raw pixel bbox) — no validation/dedup logic here (entirely the Agent's job, T-83/T-84), keeping this side minimal and fast. |
| `transport.py` | A non-blocking UDS client: connects to `--socket-path`, sends length-prefixed JSON frames, reconnects lazily if the Agent-side listener is absent, and **never blocks the probe** on a slow/absent socket (a short non-blocking send with a bounded retry, dropping the message on failure rather than stalling the pipeline — FS-05 §4.4). Never logs the connection string, argv, or any credential (none exist in Phase 1/2; written so this stays true if a later phase adds RTSP credentials, same discipline as T-73's DeepStream log-safety note). |
| `main.py` | `python -m deepstream_bridge.main` — wires `cli.py` → `pipeline.py` → `probe.py`/`transport.py` and runs the `GLib.MainLoop`. |

This package contains the *only* model-specific-looking code path in the deployment tree that reads a
profile's `infer-config.txt` structurally (element property names) — never a hardcoded model name,
matching T-72's genericness discipline extended to this new artifact. Its parsing/serialization logic
(`probe.py`'s batch-meta→dict step, `transport.py`'s framing) is unit-testable on a dev machine with a
fabricated `pyds`-shaped stub — a `tests/` directory ships alongside `app/` inside the Bridge's own
source tree, run with the Bridge's own tooling, **not** `agent`'s `pytest`/`mypy`/`ruff` gates (this
package is never imported by `agent/src/` — T-90 adds a static check enforcing that). A separate,
documented Jetson-side smoke test (T-91) covers the real, hardware-dependent path — mirroring how
`verify-deepstream.sh` is DeepStream's own opt-in, non-CI verification.

### T-89 — Bridge deployment scaffolding: venv build, `run.sh`, `requirements.lock`, `install.sh` wiring

- `deployment/jetson/deepstream/bridge/requirements.lock`: pinned versions (and, where practical,
  hashes) for `pyds==1.1.6` (referencing the wheel URL confirmed in T-80) and `pgi` — the Bridge's
  complete, reviewable **dependency-only** manifest (never includes `deepstream_bridge` itself).
- `deployment/jetson/deepstream/bridge/run.sh` (committed; staged verbatim to
  `/opt/weapon-detection/deepstream-bridge/run.sh`, mode executable): the defensive argv-translating
  wrapper specified verbatim in FS-05 §4.3 — `set -eu`; rejects anything other than exactly
  `-c <config-path>` (two args, first literally `-c`) with a clear message and non-zero exit; verifies
  the venv interpreter (`venv/bin/python`) exists and is executable; verifies the config path is
  readable; reads `SOCKET_PATH` from the inherited `WDA_DETECTION_SOCKET_PATH` environment variable
  (falling back to the same literal default `AgentSettings.detection_socket_path` uses, `${VAR:-default}`
  shell syntax) — **never a second hardcoded copy of the path**; exports a **process-local**
  `PYTHONPATH=/opt/weapon-detection/deepstream-bridge/app` so `deepstream_bridge` is importable
  without any install step; and `exec`s (not calls) the venv's `python -m deepstream_bridge.main
  --socket-path "$SOCKET_PATH" --config "$CONFIG_PATH"` so it replaces the shell as
  `DeepStreamProcessManager`'s tracked child — signals and exit codes propagate directly, with no
  intermediate process for `stop()`'s SIGTERM/SIGKILL/`wait()` bookkeeping (T-72) to lose track of.
  Tests: a bash-level test harness (or `agent`-side subprocess tests invoking the real script with a
  fake venv/config fixture) proving each failure mode (missing `-c`, wrong arg count, missing
  interpreter, unreadable config) exits non-zero with no Bridge process spawned, and that the happy
  path execs with the expected argv and inherited/derived `SOCKET_PATH`.
- `deployment/jetson/deepstream/bridge/README.md` (committed, staged to
  `/opt/weapon-detection/deepstream-bridge/README.md`): how to build/rebuild the venv
  (`python3.8 -m venv --system-site-packages venv && venv/bin/pip install -r requirements.lock` —
  **no** `pip install ./app`, since the venv holds dependencies only, FS-05 §4.6), how to run the
  Bridge's own unit tests, and the rollback procedure (repoint `WDA_DEEPSTREAM_EXECUTABLE_PATH` back
  to `/usr/bin/deepstream-app` — FS-05 §4.3).
- `deployment/jetson/deepstream/bridge/deploy-bridge.sh`: an operator-run script (mirroring
  `deploy-engine.sh`'s posture — manual, idempotent, non-destructive without `--force`) that creates
  the venv if absent and installs `requirements.lock` into it — **never `pip install`s `app/`** — then
  verifies (`venv/bin/python -c "import pyds; ..."`, the same production-account check as T-80) before
  declaring success. Refuses to silently reinstall/rebuild an existing venv without `--force`.
- `install.sh`: stage the Bridge's *source only* — `app/`, `requirements.lock`, `run.sh`, `README.md`,
  `deploy-bridge.sh` — into `/opt/weapon-detection/deepstream-bridge/` (same rsync pattern T-70
  already established for `deployment/jetson/deepstream/`), **with `venv/` explicitly excluded** from
  the rsync/copy (`rsync -a --exclude venv/ ...` or equivalent) so restaging on every install/update
  run never deletes, truncates, or triggers a rebuild of an already-provisioned venv. `install.sh`
  never builds or touches the venv itself — that is `deploy-bridge.sh`'s job, run manually, exactly
  like `deploy-engine.sh` is never invoked by `install.sh`.
  **Test:** an idempotency test that (1) stages the Bridge source into a temp root, (2) creates a
  marker file inside a fake `venv/` (simulating a built venv), (3) re-runs the staging step (simulating
  `update.sh`), and (4) asserts the marker file — and every other file under `venv/` — is byte-for-byte
  unchanged, proving source updates never touch the venv (FS-05 §4.3).
- `deployment/jetson/agent.env.example`: document every `WDA_DETECTION_*` variable with its safe
  default (`WDA_DETECTION_EVENTS_ENABLED=false`), and a comment on how `WDA_DEEPSTREAM_EXECUTABLE_PATH`
  is repointed at `/opt/weapon-detection/deepstream-bridge/run.sh` for a deployment that enables this
  feature (an operator decision, not an installer default — a fresh install keeps launching
  `/usr/bin/deepstream-app` unless both this feature and the executable-path override are deliberately
  set — FS-05 §4.3's preserved-rollback-runtime requirement).
- No change to `deepstream-rtsp-route.service` or `fix-rtsp-multicast-route.sh` (explicitly out of
  scope, FS-05 header).

### T-90 — Static safety checks: environment isolation, path consistency, and incompatible-configuration preflight

Three checks that verify FS-05 §4.6's binding rules mechanically, not just by review:

- A static test (`agent/tests/test_no_pyds_dependency.py` or similar) asserting the Agent's own
  dependency manifest (`agent/pyproject.toml`) names no DeepStream Python binding (`pyds`, `pgi`) and
  that no file under `agent/src/` contains the literal token `pyds` — the Agent must never import it
  (FS-05 §4.6, requirement 5).
- A check (unit test or a small script exercised by CI/opt-in verification) confirming
  `deployment/jetson/deepstream/bridge/run.sh`'s `${WDA_DETECTION_SOCKET_PATH:-default}` fallback
  value textually matches `AgentSettings.detection_socket_path`'s default (`config/paths.py`'s
  `detection_socket_file`) — resolving FS-05 §OI-10 by catching drift mechanically rather than
  trusting the two to stay in sync by convention. (The *runtime* value is not duplicated at all — it
  is inherited from the same environment the Agent itself was started with, per T-89's `run.sh`; only
  the unset-fallback literal needs this check.)
- **Incompatible-configuration preflight (FS-05 §4.3/§4.6 requirement 6).** A new cross-field check on
  `AgentSettings` — a `model_validator(mode="after")`, the pydantic v2 idiom for a check spanning more
  than one field, added alongside the existing per-field `field_validator`s (T-81, already delivered;
  this is an additive change to the same file/pattern, not a reopening of that task) — that raises when
  `detection_events_enabled is True` and `deepstream_executable_path` equals the literal default
  `Path("/usr/bin/deepstream-app")`. Routed through the existing `ConfigurationError` wrapping
  (`load_settings`'s `_describe`), naming both `WDA_DETECTION_EVENTS_ENABLED` and
  `WDA_DEEPSTREAM_EXECUTABLE_PATH` in the message, so Agent startup fails fast and clearly instead of
  the Agent starting normally with a `DetectionIngestHandler` that can never receive anything. Tests:
  the incompatible combination raises `ConfigurationError` naming both variables; detection events
  enabled with any *other* `deepstream_executable_path` value (e.g. `run.sh`'s path) succeeds; the
  default (`detection_events_enabled=False`) succeeds regardless of the executable path, since a
  disabled feature is never inconsistent with anything.

### T-91 — Live Jetson validation (manual, output feeds the final report)

Following the task brief's exact "Live Jetson validation" checklist (12 steps) — record git status,
back up the deployed `deepstream-app.txt`/env file, confirm current RTSP output and temperatures,
confirm exactly one DeepStream process, deploy (including `deploy-bridge.sh` to build the Bridge's
venv), then verify: Agent active; exactly one DeepStream-side (now: Bridge) process; RTSP output
still plays locally; ~25 FPS maintained; a controlled gun/knife test-video detection produces exactly
one structured event in the Agent's log and one row in `DetectionEvent`; the weapon remaining visible
across multiple frames is suppressed during the cooldown; a new event is accepted after the cooldown
elapses; no events with no configured weapon present; Agent restart creates no duplicate Bridge
process; temperatures stay reasonable (`tegrastats`); `/usr/bin/deepstream-app` is still present and
launchable by reverting `WDA_DEEPSTREAM_EXECUTABLE_PATH` (rollback runtime confirmed intact, FS-05
§4.3). No real weapons — the existing controlled test video only.

### T-92 — IDR/SPS-PPS RTSP-out production fix (post-T-91 incident follow-up)

Live cutover surfaced two H.264-output defects not covered by T-88's original acceptance criteria: a
late RTSP client join could never decode (SPS/PPS sent once at pipeline start only), and a lost/
corrupted P-frame could leave visible corruption on screen for up to ~8.5s (the encoder's default
256-frame IDR interval). Fixed entirely inside the Bridge's own RTSP-out branch
(`pipeline.py::_attach_rtsp_out`): `rtppay.set_property("config-interval", -1)` re-embeds SPS/PPS
before every IDR; a new `[bridge-rtsp-out] idr-interval=` config key (default 30 frames, `config.py`)
shortens the encoder's `idrinterval`. No change to `nvinfer`/tracker/OSD/probe/transport, confidence,
cooldown, or SQLite logic. Regression tests:
`deployment/jetson/deepstream/bridge/tests/test_pipeline_rtsp_out_idr.py`. See
`deployment/jetson/deepstream/README.md` "T-92" for the full incident writeup.

### T-93 — Tracker-removal evaluation (isolated live-Jetson A/B, no production change)

The project requirement (detect gun/knife, forward raw detections, apply class/camera cooldown,
persist accepted events) does not require persistent object identity, trajectory, or unique-object
counting — the only capabilities `nvtracker` adds. This task evaluates, but does not itself apply,
removing it: an isolated A/B (`[tracker] enable=1` vs `enable=0`, same camera-equivalent controlled
test video, model, `nvinfer interval` (real, unchanged), confidence threshold, RTSP-TCP transport, and
cooldown settings) confirmed tracker-off omits `nvtracker` entirely (never merely disables it at
runtime), the metadata probe still receives `NvDsObjectMeta` from `nvinfer` either way (attached
downstream of both chains on `nvdsosd`'s sink pad, registered exactly once), the full
Bridge -> UDS -> Agent -> SQLite flow is unaffected, and cooldown suppression (keyed on
`device_id`/`camera_id`/`class_name`, never a tracking ID) behaves identically with or without the
tracker. A follow-up repeated acceptance test (A1/B1/B2/A2, raw-count instrumentation) resolved an initial
unexplained accepted-event-count gap between configurations: it traces to `nvtracker` reducing overall
pipeline throughput (fewer frames of the fixed-duration test video processed per wall-clock second),
not to duplicate or spurious detections — confidence distributions and cooldown correctness were
statistically indistinguishable between configurations across all four runs. Full results are recorded
in `deployment/jetson/deepstream/README.md` "T-93"/"Acceptance test". Regression tests:
`deployment/jetson/deepstream/bridge/tests/test_pipeline_tracker_construction.py`,
`deployment/jetson/deepstream/bridge/tests/test_config.py`, `agent/tests/test_detection_cooldown.py`.
**Production `deepstream-app.txt` was changed** (`[tracker] enable=1` -> `enable=0`, the only line
changed, backup retained) after the acceptance evidence passed every gate — see the README for the
verification checklist and rollback procedure.

## 5. Testing Strategy Summary

| Layer | What | Real DeepStream/pyds needed? |
|---|---|---|
| Unit (T-81/T-83/T-84/T-85/T-86) | Settings, event model/validator, cooldown tracker, repository/migration, UDS handler over a real local socket with fabricated JSON payloads | No |
| T-87 regression | `default_components_factory`/`create_app()` defaults unchanged | No |
| T-88 Bridge unit tests | `probe.py`/`transport.py` parsing/framing logic, fabricated `pyds` stub, run with the Bridge's own tooling (not `agent`'s) | No |
| T-89 `run.sh`/staging tests | Wrapper argv/failure-mode behavior; staging idempotency (venv untouched on restage) | No |
| T-90 static checks | No `pyds` import in `agent/src/`; `run.sh` socket-path fallback matches `AgentSettings` default; incompatible-configuration preflight rejects `detection_events_enabled=True` + default executable path | No |
| T-80 gate | `pyds` import smoke test (original + venv-migration re-verification) | Yes (Jetson) |
| T-91 manual | Full live pipeline, real inference, real UDS traffic, real SQLite row | Yes (Jetson) |

## 6. Acceptance Criteria

Adopts FS-05's task-brief acceptance criteria verbatim (actual `NvDsObjectMeta` used; gun/knife
events converted to structured events; class labels resolved from the deployed profile; low-
confidence/unknown detections rejected; duplicates suppressed; accepted events persisted; Agent
remains active; exactly one DeepStream-side process; RTSP output remains playable; no RTSP
regression; multicast route fix untouched; malformed messages cannot crash the Agent; stopping the
Agent cleans up every child/background component including the UDS listener; existing tests stay
green; new tests cover the full event flow; static analysis passes; no model/video/credential/log/
runtime data committed), plus:

12. `pyds` availability is confirmed on the real Jetson (T-80) before any downstream task is
    implemented — a documented go/no-go gate, not an assumption.
13. `DeepStreamProcessManager`'s source still contains no model-specific token and no new
    detection-specific branch — this feature changes only *what path* `deepstream_executable_path`
    is configured to (an operational/deployment decision), never the class itself.
14. The Bridge is deployed as a fully separate application under
    `/opt/weapon-detection/deepstream-bridge/` with its own Python 3.8 venv; `pyds`/`pgi` are
    installed inside that venv only — never under `/home/*/.local`, the Agent's Python 3.11 venv, a
    global `PYTHONPATH`, or `/opt/weapon-detection/runtime/` (T-90's static checks, FS-05 §4.6).
15. `/opt/weapon-detection/runtime/` contains only transient runtime objects (the detection socket) —
    no persistent dependency, package, or venv lives there once T-80's migration is complete.
16. `/usr/bin/deepstream-app` remains the default `WDA_DEEPSTREAM_EXECUTABLE_PATH` and remains
    launchable throughout — the Bridge is additive, not a replacement, until it independently passes
    every acceptance check (FS-05 §4.3).
17. `AgentSettings` rejects `detection_events_enabled=True` combined with the default
    `deepstream_executable_path` with a named `ConfigurationError` at Agent startup — enforced in
    code (T-90), not merely documented as an operator caution.
18. `run.sh` never carries its own hardcoded socket path — it reads `WDA_DETECTION_SOCKET_PATH` from
    its inherited environment, falling back to the one literal default also used by
    `AgentSettings.detection_socket_path` (T-90's consistency check) — and staging updated Bridge
    source (`install.sh`/`update.sh`) never deletes or rebuilds an existing `venv/` (T-89's
    idempotency test).

## 7. Risks and Rollback Plan

| Risk | Mitigation |
|---|---|
| No working `pyds` build for DS 6.2 / JetPack 5.1.2 / Python 3.8 aarch64. | T-80 is a hard gate before any other task starts; failure triggers a documented pivot to FS-05 §4.1 option 3, requiring its own FS/IP revision — not silently absorbed into this plan. Resolved (T-80). |
| The hand-assembled Python Bridge underperforms the proven `deepstream-app` binary (FPS regression, RTSP instability). | T-91 re-proves ~25 FPS and playable RTSP output before this feature is considered done, exactly as IP-06 T-76/T-77 did for the original binary — no assumption that Bridge parity holds without re-measuring. Because `/usr/bin/deepstream-app` remains the default and stays untouched (acceptance criterion 16), a Bridge regression never leaves the device without a working RTSP pipeline. |
| A slow/blocked UDS send stalls the inference loop. | The probe never blocks on `send` (bounded, non-blocking, drop-on-failure — FS-05 §4.4); this is asserted by a T-88 unit test of the transport module in isolation (fabricated blocking socket), not just documented intent. |
| SQLite write volume from bursty detections. | The bounded internal queue (T-86) decouples ingestion from persistence; `WDA_DETECTION_QUEUE_CAPACITY` caps memory use; a full queue drops-newest rather than blocking or growing unbounded. |
| The Bridge's venv build (`deploy-bridge.sh`) is itself a new operator-run step that could be skipped or done incorrectly. | `deploy-bridge.sh` verifies its own result (a production-account `import pyds` check, mirroring T-80) before declaring success — a broken venv is caught at build time, not discovered later as a silent `DeepStreamProcessManager` launch failure. |
| An operator enables `WDA_DETECTION_EVENTS_ENABLED=true` without repointing `WDA_DEEPSTREAM_EXECUTABLE_PATH` away from `/usr/bin/deepstream-app` — the reference binary cannot publish detection events, so `DetectionIngestHandler` would start and simply never receive anything, with no clear signal why. | A preflight check (T-90) fails fast with a clear, named error for exactly this combination — see T-90's "incompatible configuration" check below. |

**Rollback.** `WDA_DETECTION_EVENTS_ENABLED=false` (default) fully disables the feature with zero
behavior change to the IP-06 baseline — no `DetectionIngestHandler` constructed, no socket, no
schema v3 data written to going forward (v3 migration itself is additive and safe to leave applied).
If the Bridge itself is the problem, reverting `WDA_DEEPSTREAM_EXECUTABLE_PATH` to
`/usr/bin/deepstream-app` restores the exact IP-06 T-77-proven state, independent of the detection
feature's enabled/disabled flag — this is a one-line revert, not a redeploy, because the binary was
never removed or modified (acceptance criterion 16). The Bridge's entire footprint
(`/opt/weapon-detection/deepstream-bridge/`) can also be removed outright with no effect on the
Agent, DeepStream binary path, or Device Identity. Full code rollback via `update.sh` from the prior
commit, as IP-06 §7 already documents.

## 8. What Happens Next (not part of this approval)

Reliable outbox delivery of persisted `DetectionEvent` rows to the ASP.NET Core backend, then SQL
Server, then the Angular security dashboard — each requiring its own FS/IP approval, per the
Development Workflow this repository follows. `DeliveryStatus`/`EventId` are shaped in T-85
specifically so that feature can add delivery-attempt tracking without touching this plan's schema
migration or the DeepStream Bridge at all.
