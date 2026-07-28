# Feature Specification: DeepStream Detection Event Bridge

| Field | Value |
|-------|-------|
| Feature ID | FS-05 |
| Title | Detection Event Bridge — DeepStream metadata → structured events → Agent validation, dedup, and SQLite persistence |
| Status | Approved — implemented and validated on real Jetson hardware (IP-07 T-80–T-93) |
| Related SRS Requirements | FR-DET-001–004 (detection), NFR-PRF-001–002 (inference not blocked by orchestration; <5s downstream latency budget), NFR-MNT-001 (model/runtime swap needs no control-plane change) |
| Related Architecture Sections | ARCH-CON-001 / ADR-005 (Agent↔DeepStream IPC — **binding, not re-decided here**), §9.2 Runtime Boundaries, §11 Logical View ("Detection Ingest Handler", "Alert Manager"), §13.6 (event/alert data flow) |
| Related ADRs | ADR-005 (Agent↔DeepStream IPC — Unix domain socket, no message broker); ADR-004 (SQLite local persistence); ADR-006 (DeepStream process supervision, unchanged); ADR-008 (filesystem layout) |
| Owner | Farhan Naeem |
| Dependencies | IP-06 (DeepStream Runtime Integration Phase 1 — delivered T-70–T-79): `DeepStreamProcessManager`, the generic profile-based asset layout, `AgentSettings` `WDA_DEEPSTREAM_*` fields, `OperationalComponent`/`OperationalStateCoordinator`/`AgentRuntimeSupervisor`. IP-02 (SQLite schema/repository pattern, T-35/T-36). |
| Fulfills | The "metadata extraction, detection events" work IP-06 explicitly excluded (IP-06 plan header, FS-04 §1.2) — the next phase of the "Jetson Agent Bootstrap & DeepStream Supervision" line of work. |
| Explicitly excluded | Central ASP.NET ingestion, SQL Server, Angular dashboard/notifications, snapshots, recordings, WebRTC, sirens, outbox/delivery-retry logic beyond a `pending` status column, object tracking, any change to activation/reactivation/credential-validation behavior, any change to `deepstream-rtsp-route.service` or the multicast route fix. |

---

## 1. Purpose and Scope

IP-06 gave the Agent supervision of a DeepStream child process producing a working RTSP output with
on-screen bounding boxes. Nothing in the Agent today reads what DeepStream actually detected. This
feature closes that gap, stopping at reliable Jetson-side event creation and SQLite persistence —
exactly the scope FS-04 §1.2 deferred.

```text
DeepStream detection (NvDsObjectMeta)
    ↓ serialize + send                    ◄── ADR-005: Unix domain socket, JSON, no broker
Agent: Detection Ingest Handler (new)
    ↓ validate + resolve class + clamp bbox
Agent: DetectionEvent (new domain model)
    ↓ deduplicate (cooldown, monotonic clock)
Agent: DetectionEventRepository (new, SQLite)
    ↓ persist (delivery_status='pending')
Diagnostics (CLI/SQLite query — see §8)
```

### 1.1 What This Feature Adds

| Capability | Basis |
|---|---|
| A **separate, independently deployed application** — the "DeepStream Bridge" — under `/opt/weapon-detection/deepstream-bridge/`, with its own Python 3.8 virtual environment, that replaces the current generic `deepstream-app` binary invocation for a detection-enabled deployment. It rebuilds the same proven pipeline shape and additionally reads real `NvDsObjectMeta` via `pyds` and a pad probe, then serializes **every detected object's raw inference facts** (`class_id`, `confidence`, `source_id`, frame number, frame dimensions, bounding box, `schema_version` — §5) as JSON and sends them over a Unix domain socket. The Bridge performs **no class-name resolution, no configured-class filtering, no confidence filtering, no validation, no cooldown, and no persistence** — it publishes every detection the model produces, unfiltered; all of that is exclusively the Agent's job (§4.6). | ADR-005 requires the *DeepStream process itself* to serialize and send JSON over a UDS — a fixed reference binary cannot do this; a pad probe is the NVIDIA-documented, supported way to reach `NvDsObjectMeta` (§4 below). Packaging it as a fully separate application with its own venv (§4.6) keeps `pyds`, `pgi`, and every DeepStream-Python dependency entirely out of the FastAPI Agent's own Python 3.11 environment — the Agent process must never import `pyds`. Keeping the Bridge filter-free avoids duplicating class/confidence policy in two places and two languages of trust boundary — the Agent is the single place that decides what counts as an event (§5). |
| A `DetectionIngestHandler` operational component: an `asyncio` Unix domain socket server, length-prefixed framing, bounded message size, schema-versioned payload, running only while `deepstream_enabled` and a new `detection_events_enabled` are both true. | ADR-005; §9.2 Runtime Boundaries ("no shared memory, no in-process calls" — the UDS is the only channel). Reuses the `OperationalComponent` protocol (IP-05 T-60) — no new lifecycle abstraction. |
| A `DetectionEvent` domain model with strict validation (finite/non-negative/clamped bbox, known class, confidence bounds, tz-aware UTC timestamp, UUID event id). | Task's event contract; mirrors the `DeviceIdentity`/`CachedConfiguration` dataclass + validation style already in `persistence/models.py`. |
| Deterministic, in-memory, monotonic-clock cooldown suppression keyed by (device id, camera id, class name). | Task's duplicate-suppression requirement; explicitly *not* full tracking. |
| `DetectionEventRepository` + a schema v2→v3 migration adding a `DetectionEvent` table, following the exact idempotent-migration pattern already in `persistence/schema.py`. | Task's persistence requirement; reuses the delivered migration/transaction machinery unchanged. |
| `WDA_DETECTION_*` settings on `AgentSettings`, following the exact `WDA_DEEPSTREAM_*` pattern (T-71). | Task's configuration requirement. |
| A read-only repository/CLI verification path (not a new HTTP route — see §8). | `app.py` already states the Agent defines **no** operational routes and no health endpoint is approved (OI-3, still open); adding a new route would contradict a standing architectural statement without a fresh decision to do so. |

### 1.2 What This Feature Does Not Touch

- No change to `DeepStreamProcessManager`, `OperationalStateCoordinator`, `AgentRuntimeSupervisor`, or the coordinator's lock/rollback semantics — `DetectionIngestHandler` is *one more* `OperationalComponent` registered alongside `DeepStreamProcessManager`, nothing more.
- No change to the generic profile-based model/config deployment (`deploy-engine.sh`, `manifest.env`, profile layout) — the pyds application still launches against a profile-agnostic config; §4.3 explains what does change.
- No Backend call, no alert, no snapshot, no recording.
- No change to `deepstream-rtsp-route.service` or `fix-rtsp-multicast-route.sh`.
- No object tracking (the disabled DeepStream tracker stays disabled).

## 2. Actors

| Actor | Description |
|---|---|
| DeepStream Bridge (revised, separate application) | A standalone Python application (package `deepstream_bridge`, its own Python 3.8 venv, §4.6) launched as a child of the Agent's existing `DeepStreamProcessManager` via a thin argv-translating wrapper (§4.3) — `DeepStreamProcessManager`'s own argv shape (`[executable, "-c", config]`) is unchanged. Owns the entire DeepStream/GStreamer/TensorRT surface: pipeline construction, inference, `pyds` metadata extraction, OSD, RTSP output, and non-blocking UDS publishing. Never persists anything, never knows the device identity, never validates/deduplicates — those are exclusively the Agent's job (§4.6 ownership split). |
| `DetectionIngestHandler` (new) | An `OperationalComponent` owning the UDS server: accept connections, frame/parse/bound/validate messages, hand well-formed payloads to the validation→dedup→persistence pipeline. Never blocks the caller on a slow/unavailable Agent — a full queue drops the newest message (§6). |
| `DetectionEventValidator` (new) | Pure function(s): raw payload → `DetectionEvent` or a typed rejection reason. No I/O. |
| `DetectionCooldownTracker` (new) | In-memory keyed cooldown using `time.monotonic()`. No persistence (documented behaviour, §7). |
| `DetectionEventRepository` (new) | The only code that writes/reads the `DetectionEvent` table, mirroring `DeviceIdentityRepository`'s construction and transaction style. |
| `AgentRuntimeSupervisor` / `OperationalStateCoordinator` | Unchanged (IP-05 T-60/T-61) — `DetectionIngestHandler` plugs into the existing `components=(...)` tuple exactly as `DeepStreamProcessManager` did (IP-06 T-74). |

## 3. Preconditions

- IP-06 is deployed and `WDA_DEEPSTREAM_ENABLED=true` on the target device (this feature is meaningless without a running DeepStream pipeline).
- The Agent has reached `Operational` (IP-02/IP-05 unchanged).
- The DeepStream Bridge application is deployed under `/opt/weapon-detection/deepstream-bridge/` with its own built Python 3.8 virtual environment containing `pyds` — a one-time, documented Jetson-side deployment step (§4.6), not Agent code, and never a dependency the Agent's own Python 3.11 environment carries.

## 4. Architectural Decision — How DeepStream Reaches the Socket

ADR-005 (frozen) already answers *whether* to use a broker (no) and *what channel* to use (a Unix
domain socket, DeepStream-side serialization). What FS-04/IP-06 left open is *how* the DeepStream
process — today the unmodified `deepstream-app` reference binary — gets access to `NvDsObjectMeta` at
all, since that binary has no hook for custom per-frame code.

### 4.1 Options Evaluated

| # | Option | Verdict |
|---|---|---|
| 1 | `nvmsgconv`/`nvmsgbroker` + a supported local transport | **Rejected.** Confirmed on the real Jetson (`/opt/nvidia/deepstream/deepstream-6.2/lib/`): only AMQP, Kafka, Azure, and Redis protocol adaptors ship with DS 6.2 — no MQTT, no local-socket adaptor. Every available adaptor is a message-broker client. ADR-005 already rejected message brokers by name (citing MQTT) "to avoid an unnecessary running component" — introducing RabbitMQ/Kafka/Azure/Redis here would directly contradict a frozen decision, not merely be a worse choice. |
| 2 | Custom DeepStream application using Python `pyds` + a pad probe | **Selected.** The NVIDIA-documented way to reach `NvDsObjectMeta` from application code (the `deepstream_python_apps` sample pattern: `Gst.Pipeline`, a probe on the OSD sink pad, `pyds.gst_buffer_get_nvds_batch_meta`). Runs the metadata extraction and the UDS `socket.send` **in the same process** DeepStream already is — exactly what ADR-005 describes ("DeepStream serializes... and sends it"). No compiled artifact beyond what DeepStream/pyds already ships; pure Python, consistent with the rest of the Agent's stack and the "maintainable for an MSc dissertation" requirement. |
| 3 | A custom compiled GStreamer/DeepStream plugin | **Rejected for this phase.** Also reaches real `NvDsObjectMeta` and would leave `deepstream-app` itself unmodified, but requires a C/C++ toolchain targeting the DeepStream SDK headers on aarch64, its own build/packaging step, and is materially harder to unit-test than a Python module — more engineering risk for no behavioural gain over option 2 given ADR-005 already fixes the transport. Recorded as a future option if the Python pipeline's overhead ever becomes a measured problem (§10). |
| 4 | An existing repository mechanism | **None exists.** IP-06 explicitly deferred all metadata/event work; grep of `agent/src/` confirms no detection-event code exists today. |

### 4.2 Why Option 2 Fits This Project

- **Preserves the proven pipeline shape.** The Python application reconstructs the exact validated
  element chain from `deployment/jetson/deepstream/deepstream-app.txt`/`profiles/yolov4-fp16/infer-config.txt`
  (same `nvstreammux` → `nvinfer` (profile's `infer-config.txt`, unchanged) → `nvvideoconvert` →
  `nvdsosd` → RTSP output branch), so the FP16 engine, its manifest-verified checksum, and the
  multicast RTSP route fix are all untouched. Only the *host process* changes from a prebuilt binary
  to a Python application that assembles the equivalent pipeline via `Gst`/`pyds`.
- **No new infrastructure beyond the deployable application itself.** No broker, no new systemd unit
  beyond the Agent-supervised child that already exists (`DeepStreamProcessManager` launches whichever
  executable `deepstream_executable_path` names — today `/usr/bin/deepstream-app`, and for a
  deployment with this feature enabled, a wrapper that launches the Bridge, §4.3). Zero change to
  `DeepStreamProcessManager`'s code (T-72's genericness requirement is preserved — it still knows only
  six things: executable, config, cwd, timeout, restart policy, log path).
- **Not on the Agent's Uvicorn process, and not in the Agent's Python environment at all.** The
  Bridge runs in a wholly separate OS process **and a wholly separate Python 3.8 virtual environment**
  (§4.6) — `pyds`/`pgi`/GStreamer bindings never touch the Agent's Python 3.11 venv, so a slow or
  misbehaving probe cannot block the Agent's own event loop (NFR-PRF-001/002), and a `pyds` version
  bump or DeepStream SDK upgrade can never break the Agent's own dependency resolution.
- **Testable without hardware.** The probe's parsing/serialization logic (batch meta → dict → JSON)
  is a small, pure function separable from `Gst`/`pyds` glue, so it is unit-testable on a dev machine
  with a fabricated `pyds`-shaped stub, mirroring how `DeepStreamProcessManager` tests fake the
  subprocess factory (T-75) rather than requiring real DeepStream.

### 4.3 What Changes Operationally (Not in Agent Code)

- A new committed application source tree, `deployment/jetson/deepstream/bridge/app/deepstream_bridge/`
  (package name `deepstream_bridge`) — Jetson-side, **not** imported by the Agent's Python package, no
  shared code with `agent/src/`, consistent with the Agent/DeepStream process boundary in §9.2. It
  reads the same profile-agnostic `deepstream-app.txt`-equivalent structure and the profile's
  `infer-config.txt`/`labels.txt` paths — never a hardcoded model name (same genericness discipline as
  T-72). Its entry point is `python -m deepstream_bridge.main --socket-path <path> --config <path>`
  (§4.6) — an explicit CLI, not a positional `-c` argument.
- **Argv translation, not a `DeepStreamProcessManager` change.** `DeepStreamProcessManager` still
  builds exactly `[executable_path, "-c", config_path]` (T-72, unchanged). Since the Bridge's real CLI
  shape (`--socket-path`/`--config`) does not match that, `WDA_DEEPSTREAM_EXECUTABLE_PATH` for a
  detection-enabled deployment points at a tiny shell wrapper shipped *inside* the Bridge's own
  directory (`/opt/weapon-detection/deepstream-bridge/run.sh`) that receives `("-c", config_path)` and
  re-invokes the Bridge's own venv interpreter with its real flags. The wrapper is deliberately
  defensive rather than a one-liner, since it is the one place a malformed deployment could silently
  launch the wrong thing:

  ```sh
  #!/bin/sh
  set -eu

  # DeepStreamProcessManager always calls this as: run.sh -c <config-path>
  if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then
    echo "run.sh: expected exactly '-c <config-path>', got: $*" >&2
    exit 2
  fi
  CONFIG_PATH="$2"

  BRIDGE_ROOT="/opt/weapon-detection/deepstream-bridge"
  PYTHON="$BRIDGE_ROOT/venv/bin/python"

  if [ ! -x "$PYTHON" ]; then
    echo "run.sh: Bridge venv interpreter not found or not executable: $PYTHON" >&2
    echo "run.sh: run deploy-bridge.sh to build it" >&2
    exit 3
  fi
  if [ ! -r "$CONFIG_PATH" ]; then
    echo "run.sh: config file not readable: $CONFIG_PATH" >&2
    exit 4
  fi

  # Inherited from the Agent/systemd environment, never hardcoded here — matches the same
  # ADR-008 path AgentSettings.detection_socket_path defaults to (config/paths.py's
  # detection_socket_file), so the two are never independently duplicated as literals.
  SOCKET_PATH="${WDA_DETECTION_SOCKET_PATH:-/opt/weapon-detection/runtime/detection.sock}"

  # The app package lives outside the venv (§4.6) — the venv holds only pyds/pgi. PYTHONPATH makes
  # deepstream_bridge importable without an install step, so newly staged source is always what
  # actually runs; no stale pip-installed copy can exist.
  export PYTHONPATH="$BRIDGE_ROOT/app${PYTHONPATH:+:$PYTHONPATH}"

  # exec (not a plain call) so the Bridge replaces this shell as DeepStreamProcessManager's tracked
  # child — signals (SIGTERM/SIGKILL from stop()) and the real exit code propagate directly, with no
  # intermediate shell process for the process manager's PID/wait() bookkeeping to lose track of.
  exec "$PYTHON" -m deepstream_bridge.main \
    --socket-path "$SOCKET_PATH" \
    --config "$CONFIG_PATH"
  ```

  This keeps `DeepStreamProcessManager` fully generic (no new argv concept, no knowledge of the
  Bridge's CLI) while giving the Bridge its own explicit, self-documenting flags, and it keeps the
  socket path as a single source of truth: whatever `WDA_DETECTION_SOCKET_PATH` resolves to for the
  Agent (inherited into the child's environment the same way any subprocess inherits its parent's
  environment — `DeepStreamProcessManager` sets no environment of its own, so this is the *ambient*
  environment `systemd`/the Agent process already runs in) is exactly what the Bridge uses, with the
  literal default only ever appearing once, as the `:-` fallback for a deployment that has not set
  the variable at all.
- **The venv holds dependencies only — never the application code.** `deploy-bridge.sh` (§4.6)
  installs `pyds`/`pgi` (and any future dependency) into the venv's own `site-packages`, but never
  `pip install`s the `deepstream_bridge` package itself (not even editable). The package is made
  importable purely via `run.sh`'s `PYTHONPATH` export above. This guarantees that redeploying
  updated source (`install.sh` restaging `app/`) is picked up on the Bridge's very next start with
  **no separate reinstall step** — there is no non-editable copy that could silently go stale while
  `install.sh` updates only the source tree. (An editable `pip install -e ./app` into the venv would
  satisfy the same guarantee and is an acceptable alternative implementation; the binding requirement
  is that newly staged source is provably what executes, not the specific mechanism.)
- `install.sh` gains no new responsibility for building the Bridge itself — a dedicated, operator-run
  `deploy-bridge.sh` (mirroring `deploy-engine.sh`'s posture) builds/rebuilds the Bridge's venv and
  installs its pinned dependencies (§4.6). `install.sh` stages the Bridge's *source* (`app/`,
  `requirements.lock`, `run.sh`, `deploy-bridge.sh`, `README.md`) into
  `/opt/weapon-detection/deepstream-bridge/`, the same rsync pattern already used for
  `deployment/jetson/deepstream/` — **with an explicit `venv/` exclusion** (`rsync --exclude venv/
  ...` or equivalent), so restaging updated source on an `update.sh` run never touches, deletes, or
  needs to rebuild the already-provisioned venv. The implementation plan adds an idempotency test
  proving this: staging source twice (simulating an update) leaves a pre-existing `venv/` directory
  and its contents byte-for-byte untouched.
- **Rollback runtime preserved.** `/usr/bin/deepstream-app` remains the default of
  `WDA_DEEPSTREAM_EXECUTABLE_PATH` and is never removed, modified, or made unusable by this feature.
  Enabling the Bridge is exactly one operator-made settings change
  (`WDA_DEEPSTREAM_EXECUTABLE_PATH=/opt/weapon-detection/deepstream-bridge/run.sh` alongside
  `WDA_DETECTION_EVENTS_ENABLED=true`); reverting either line alone returns the deployment to the
  IP-06 T-77-proven binary-only behavior with no further changes. The implementation plan's live
  validation does not retire the binary path — both coexist until the Bridge independently passes
  every acceptance check.
- **Incompatible-configuration preflight.** `WDA_DETECTION_EVENTS_ENABLED=true` together with
  `WDA_DEEPSTREAM_EXECUTABLE_PATH` still pointing at the reference `/usr/bin/deepstream-app` binary is
  a configuration that can never work — the binary has no way to publish detection events at all — and
  today would fail silently (the Agent starts, `DetectionIngestHandler` listens, nothing ever arrives,
  with no clear signal why). This combination must produce a clear, named failure rather than a silent
  no-op (§4.4/T-90).

### 4.4 Operational Dependencies and Failure Handling

- The Bridge's venv (with `pyds` installed inside it, §4.6) must exist and be buildable before
  `run.sh` can launch it — a documented manual step (mirroring how `deploy-engine.sh` is
  operator-run). Its absence is a launch failure, surfaced exactly like today's "DeepStream executable
  missing" case — `DeepStreamProcessManager`'s existing unexpected-exit detection (T-73) already
  handles a child that fails to start/exits immediately, unchanged.
- If the UDS has no listener (Agent's `DetectionIngestHandler` not started, or disabled), the Bridge's
  `socket.send` gets `ECONNREFUSED`/`BrokenPipeError`; the probe catches this, logs at a
  bounded rate (not per-frame), and continues running inference/RTSP output — **the inference
  pipeline and RTSP output are never blocked or stopped by a transport failure** (task requirement).
- If the Agent's `DetectionIngestHandler` is not running (feature disabled, or Agent down), the Bridge
  itself keeps running unaffected (`DeepStreamProcessManager` has no dependency on it) — satisfies "operate
  reliably when the backend is unavailable" generalized to "when the Agent-side handler is unavailable."
- Agent shutdown (`OperationalStateCoordinator`) stops `DetectionIngestHandler` the same way it stops
  `DeepStreamProcessManager` — close the listening socket, cancel any in-flight connection tasks, no
  hang (bounded by the same reasoning as T-72's watcher-task cancellation).
- `WDA_DETECTION_EVENTS_ENABLED=true` configured against `WDA_DEEPSTREAM_EXECUTABLE_PATH` still equal
  to the default `/usr/bin/deepstream-app` fails Agent startup with a clear `ConfigurationError`
  naming both variables (§4.3's preflight requirement) — enforced as a settings-level cross-field
  check (`AgentSettings`, T-90), not merely documented, so it cannot be silently misconfigured.

### 4.5 Security Considerations

- The UDS path lives under the existing `0700 database/`-sibling layout (a new `runtime/` directory,
  already reserved but undeployed per `config/paths.py`'s `DEFERRED_DIRECTORIES`) — filesystem-
  permission scoped to the `weapon-detection` user, not network-reachable at all (stronger than
  "local-only" — it is not IP-addressable).
  socket file mode `0600`, matching the database file's existing posture.
- Message size is bounded (`WDA_DETECTION_QUEUE_CAPACITY`-independent — a fixed max-frame-size
  constant) before any parsing occurs, so a malformed/oversized payload cannot exhaust memory.
  JSON parsing errors and schema-version mismatches are rejected without raising into the pipeline
  process or crashing the Agent (task requirement; mirrors the existing "safe rejection" posture of
  `AgentSettings`/repository error handling).
  No secrets, activation keys, or backend tokens ever appear in a detection message — the schema
  (§5) carries only detection facts.
- No arbitrary code execution: the payload is parsed as JSON via the standard library only, never
  `eval`/`pickle`/similar.

### 4.6 Environment Isolation and Ownership Split (binding)

**Deployment structure.** The DeepStream Bridge is a complete, independently deployed application,
not a script staged alongside the Agent's own config:

```text
/opt/weapon-detection/deepstream-bridge/
    app/                  # the deepstream_bridge package source — never pip-installed into venv/
    venv/                 # Python 3.8 virtualenv, --system-site-packages, dependencies only
    run.sh                # the argv-translating wrapper §4.3 describes
    deploy-bridge.sh      # operator-run venv build/rebuild script (never run by install.sh)
    requirements.lock     # pinned dependency versions/hashes (pyds, pgi, ...)
    README.md             # build/rebuild/rollback instructions for this application specifically
```

**Environment rules (binding, verified by the implementation plan's acceptance criteria):**

1. The Bridge's venv uses **system Python 3.8** — the same interpreter version DeepStream 6.2's
   `pyds` 1.1.6 is built against (confirmed compatible, IP-07 T-80) — never the Agent's Python 3.11.
2. The venv is created `--system-site-packages` so it can see the system-installed `gi`/GStreamer
   bindings (`python3-gi`, already present) without reinstalling them; `pyds` and its one transitive
   dependency (`pgi`) are installed **inside the venv itself** via its own `pip`, pinned per
   `requirements.lock`. **The venv holds dependencies only — the `deepstream_bridge` application
   package is never installed into it** (not even editably by default; §4.3 explains why and permits
   an editable install as an acceptable alternative provided the same "newly staged source is what
   runs" guarantee holds). `run.sh` makes `app/` importable via a process-local `PYTHONPATH` it sets
   for the Bridge child only — this is distinct from, and does not reintroduce, the global/ambient
   `PYTHONPATH` misuse item 3 below prohibits for satisfying `pyds` itself.
3. Nothing satisfying the `pyds`/`pgi` dependency is installed to `/home/*/.local`, the Agent's
   Python 3.11 venv, a global/ambient `PYTHONPATH`, or `/opt/weapon-detection/runtime/` — that
   directory (§9.2, ADR-008) is reserved **exclusively** for transient runtime objects (today:
   `detection.sock`), never persistent dependencies. This corrects the location used during IP-07
   T-80's initial `pyds` availability verification, which installed into
   `/opt/weapon-detection/runtime/python3.8/site-packages` — a location the implementation plan's
   revised T-80 task removes in favor of the venv above.
4. `install.sh` restaging `app/`, `requirements.lock`, `run.sh`, etc. on every deploy/update **must
   never delete or rebuild `venv/`** — the rsync (or equivalent) staging step explicitly excludes
   `venv/`, and the implementation plan adds an idempotency test proving a second staging run leaves
   an existing venv's contents untouched (§4.3).
5. **The FastAPI Agent must never import `pyds`, `pgi`, or any DeepStream Python binding.** This is
   enforced by construction (the Agent's `agent/src/` package has no dependency on them and no code
   path reaches them) and is asserted by a static test the implementation plan adds.
6. `WDA_DETECTION_EVENTS_ENABLED=true` combined with `WDA_DEEPSTREAM_EXECUTABLE_PATH` left at its
   default (`/usr/bin/deepstream-app`) fails Agent startup with a clear, named `ConfigurationError`
   (§4.3's preflight requirement) rather than starting a listener that can never receive anything.

**Ownership split (binding):**

| Owned by the DeepStream Bridge | Owned by the FastAPI Agent |
|---|---|
| GStreamer/DeepStream pipeline construction | Bridge **process** supervision (start/stop/exit-detection via `DeepStreamProcessManager`, unchanged) |
| TensorRT inference (via the profile's `infer-config.txt`, unchanged) | Message validation (schema, bounds, clamping — §5) |
| `pyds` metadata extraction (the pad probe) | Class-name resolution and confidence filtering (§5) |
| OSD (on-screen bounding boxes) | Cooldown/duplicate suppression (§6) |
| Processed RTSP output | Event ID generation and device identity attachment (§5) |
| Non-blocking Unix-socket publishing (raw detection facts only) | SQLite persistence (§7) and, in a future feature, Backend delivery |

Nothing on the left ever touches SQLite, the device identity, or the Backend; nothing on the right
ever imports `pyds` or constructs a `Gst` pipeline. This split is what makes the UDS message schema
(§5) the *entire* interface between the two applications — enforced structurally by them being
separate deployable units with separate environments, not merely by convention within one process.

## 5. Detection Event Contract

Python domain models in this repository use snake_case dataclasses (`persistence/models.py`), not
camelCase; the wire schema (DeepStream → Agent, JSON over the UDS) also uses snake_case for
consistency with the rest of the Agent's own conventions — the task's camelCase example is adapted
accordingly. A `schema_version` field is added per the transport requirement ("explicit
schema/versioning").

```json
{
  "schema_version": 1,
  "event_id": "3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f",
  "device_id": "158a7f4a-8b21-44d4-b466-383804e5e515",
  "camera_id": "camera1",
  "source_id": 0,
  "class_id": 0,
  "class_name": "gun",
  "confidence": 0.91,
  "frame_number": 12345,
  "detected_at_utc": "2026-07-24T18:30:00.000Z",
  "frame_width": 640,
  "frame_height": 640,
  "bbox_left": 210.0,
  "bbox_top": 130.0,
  "bbox_width": 95.0,
  "bbox_height": 70.0
}
```

Validation (`DetectionEventValidator`, pure, no I/O):

- `event_id`: UUID, generated Agent-side on receipt (not trusted from the wire) — the Agent, not the
  pipeline process, is the source of truth for event identity, so a malformed/duplicated upstream id
  cannot collide two real events.
- `device_id`: taken from the Agent's own loaded `DeviceIdentity`, never from the wire payload
  (the pipeline process has no device identity or secret — it never needs one).
- `camera_id`: from `WDA_DETECTION_CAMERA_ID` (task's suggested setting), not the wire payload, since
  Phase 1/2 has exactly one configured camera and trusting a client-supplied camera id would let a
  malformed message misattribute an event.
- `class_id`/`class_name`: `class_id` from the wire payload; `class_name` **resolved by the Agent**
  from the deployed profile's `labels.txt` (loaded once at `DetectionIngestHandler.start()`), never
  trusted verbatim from the wire — satisfies "resolve class names from the deployed label
  configuration instead of hardcoding class order." An unresolvable `class_id` is rejected.
- `confidence`: `0.0 <= value <= 1.0`, finite; below `WDA_DETECTION_MIN_CONFIDENCE` is a *rejection*
  reason distinct from "malformed" (diagnostics differ, §8).
- `frame_number`: non-negative integer.
- `detected_at_utc`: generated Agent-side (`datetime.now(timezone.utc)` at receipt), not trusted from
  the wire — avoids relying on clock sync between the two processes (they are the same host, but
  this keeps the trust boundary consistent with `device_id`/`camera_id` above).
- `frame_width`/`frame_height`: positive integers.
- `bbox_*`: finite, non-negative; clamped to `[0, frame_width]`/`[0, frame_height]` (task requirement)
  rather than rejected, since a clamp-worthy edge box is still a real detection.
- `class_name` not in the configured weapon set (`gun`, `knife` initially, from the profile's
  `labels.txt`) → rejected (`detection_event_rejected`, reason `unknown_class`).

## 6. Duplicate Suppression

- Key: `(device_id, camera_id, class_name)` — matches the task's policy exactly; different cameras
  and different classes never share a cooldown.
- First event for a key: accepted immediately, cooldown timer starts (`time.monotonic()`).
- Any equivalent key within `WDA_DETECTION_COOLDOWN_SECONDS`: suppressed, logged at
  `detection_event_suppressed` — aggregated (a periodic counter), never one INFO line per frame.
- After the cooldown elapses, the next detection for that key is accepted and restarts the timer.
- No bounding-box comparison of any kind gates suppression (task requirement — box values are not
  compared for equality; the key is identity-only).
- In-memory only (a plain dict on `DetectionIngestHandler`/its cooldown tracker) — **restarting the
  Agent process resets every cooldown**. This is the documented, accepted behaviour for this feature
  (task explicitly allows it): the existing architecture has no shared, durable, sub-second-latency
  store better suited to this than memory, and adding one (e.g., persisting cooldown state to SQLite
  on every detection) would add write load on the hot path for a purely-cosmetic dedup concern with
  no correctness requirement across restarts.

## 7. Local Persistence

New table, added as schema **v3** (current is v2, IP-05 §7), via the same idempotent
`initialize_schema` pattern in `persistence/schema.py` (forward-only migration, one transaction,
`SchemaVersion` bumped last):

```sql
CREATE TABLE DetectionEvent (
    EventId        TEXT PRIMARY KEY,
    DeviceId       TEXT    NOT NULL,
    CameraId       TEXT    NOT NULL,
    SourceId       INTEGER NOT NULL,
    ClassId        INTEGER NOT NULL,
    ClassName      TEXT    NOT NULL,
    Confidence     REAL    NOT NULL,
    FrameNumber    INTEGER NOT NULL,
    DetectedAtUtc  TEXT    NOT NULL,  -- ISO-8601 UTC
    FrameWidth     INTEGER NOT NULL,
    FrameHeight    INTEGER NOT NULL,
    BboxLeft       REAL    NOT NULL,
    BboxTop        REAL    NOT NULL,
    BboxWidth      REAL    NOT NULL,
    BboxHeight     REAL    NOT NULL,
    DeliveryStatus TEXT    NOT NULL DEFAULT 'pending' CHECK (DeliveryStatus IN ('pending')),
    CreatedAtUtc   TEXT    NOT NULL  -- ISO-8601 UTC
)
```

- `EventId` is the primary key (UUID text) — satisfies "unique event ID" without a separate index.
- `DeliveryStatus` is constrained to `'pending'` only (task: "do not implement backend delivery
  yet... the field exists to support the next feature") — a future feature widens the CHECK when it
  adds real states, following the same additive-migration discipline schema v1→v2 already used.
- `DetectionEventRepository.insert(event)` is a single-row, parameterized, transactional insert
  (`persistence/database.py`'s `transaction()`), mirroring `DeviceIdentityRepository.store`. A
  persistence failure (e.g. disk full) is caught at the `DetectionIngestHandler` layer, logged as
  `detection_event_persistence_failed`, and **does not propagate into DeepStream or crash the
  Agent** (task requirement) — the event is dropped, not retried (no outbox/retry machinery this
  phase, per explicit exclusion).
- Existing `DeviceIdentity`/`ConfigCache` data is untouched by the v2→v3 migration (additive only,
  no rebuild of either existing table).

## 8. Diagnostics

Structured log events (INFO for accepted events, DEBUG/aggregated for suppressed, WARNING for
rejected/failed — never one INFO per inference frame):

`detection_event_received`, `detection_event_rejected` (with a safe `reason` field — never raw
payload content), `detection_event_suppressed` (aggregated), `detection_event_persisted`,
`detection_event_persistence_failed`, `detection_transport_connected`, `detection_transport_disconnected`.

**Verification mechanism: a documented SQLite query, not a new HTTP route.** `app.py` states plainly
that the Agent defines no operational routes and that no health/status endpoint is approved (OI-3,
still open in FS-04). Adding `GET /api/v1/detections` would quietly reopen that closed item without
a decision to do so. Instead:

```bash
sqlite3 /opt/weapon-detection/database/agent.db \
  "SELECT EventId, ClassName, Confidence, DetectedAtUtc, DeliveryStatus \
   FROM DetectionEvent ORDER BY CreatedAtUtc DESC LIMIT 20;"
```

documented in `deployment/jetson/deepstream/README.md` alongside the existing `verify-deepstream.sh`
procedures — consistent with how the task itself offers "a documented SQLite query" as an accepted
option, and with `agent.db`'s existing `0600` permission model (only the `weapon-detection` user or
an operator with `sudo` can run it).

## 9. Configuration

Added to `AgentSettings` (`agent/src/weapon_detection_agent/config/settings.py`), `WDA_`-prefixed,
same validation style as `WDA_DEEPSTREAM_*` (T-71):

| Field | Env var | Type / default |
|---|---|---|
| `detection_events_enabled` | `WDA_DETECTION_EVENTS_ENABLED` | `bool`, default `False` |
| `detection_min_confidence` | `WDA_DETECTION_MIN_CONFIDENCE` | `float`, `Field(default=0.50, ge=0.0, le=1.0)` |
| `detection_cooldown_seconds` | `WDA_DETECTION_COOLDOWN_SECONDS` | `float`, `Field(default=5.0, gt=0)` |
| `detection_queue_capacity` | `WDA_DETECTION_QUEUE_CAPACITY` | `int`, `Field(default=1000, gt=0)` |
| `detection_camera_id` | `WDA_DETECTION_CAMERA_ID` | `str`, default `"camera1"`, non-blank when `detection_events_enabled` is `True` |
| `detection_socket_path` | `WDA_DETECTION_SOCKET_PATH` | `Path`, default `/opt/weapon-detection/runtime/detection.sock` |

- Same two-layer kill switch as DeepStream (T-71/T-74): `detection_events_enabled=False` (default)
  means `default_detection_components_factory` returns `()` — no `DetectionIngestHandler`
  constructed, no socket created, no listener bound. A fresh/updated deployment sends no detection
  traffic until an operator opts in, and a deployment with events disabled behaves exactly as today's
  IP-06-only deployment (RTSP output unaffected either way).
  `resolve_paths`/`config/paths.py` gains the `runtime/` directory to its provisioned set (it is
  already reserved in `DEFERRED_DIRECTORIES` — this is the first feature with a writer for it,
  matching the module's own documented deferral rule).
- Boolean parsing is pydantic-settings' existing case-insensitive handling (already exercised by
  `deepstream_enabled`) — no new parsing code.
- `deployment/jetson/agent.env.example` documents every new variable, with `WDA_DETECTION_EVENTS_ENABLED=false` as the shipped default (no behavior change on an existing deployment that redeploys this file unedited).

## 10. Known Limitations Carried Into the Implementation Plan

- Cooldown state does not survive an Agent restart (§6, documented, accepted).
- No delivery/outbox mechanism yet — `DeliveryStatus` exists only as a placeholder column (§7).
- The DeepStream-side Python pipeline is a new, unproven-on-this-exact-device artifact; the
  implementation plan requires a fresh live-Jetson FPS/behavior proof (mirroring IP-06 T-76/T-77),
  not a re-use of the existing binary's proof.
- A future phase may revisit option 3 (a compiled probe) if the Python pipeline's overhead is
  measured to be a problem — no such measurement exists today, so this stays a documented option,
  not a plan.

## 11. Open Items

| ID | Issue | Resolution proposed |
|----|-------|----------------------|
| OI-8 | `pyds` wheel availability/version for DS 6.2 + JetPack 5.1.2 + system Python 3.8 needs a live-Jetson confirmation pass before implementation starts (a install/import smoke test, not a code task). | **Resolved.** `pyds` 1.1.6 confirmed importable against DS 6.2/Python 3.8 on the real Jetson (IP-07 T-80). The *installation location* used during that confirmation (`/opt/weapon-detection/runtime/python3.8/site-packages`) is itself superseded by §4.6's dedicated Bridge venv — the implementation plan's revised T-80 migrates it. |
| OI-9 | The DeepStream side of this feature could equally be considered its own small "profile" of the pipeline (`yolov4-fp16` vs. a hypothetical `yolov4-fp16-with-events` variant) rather than a wholesale replacement of the executable. | Deferred to the implementation plan's task breakdown — functionally equivalent, this is a deployment/documentation naming choice, not an architectural one. |
| OI-10 | `run.sh`'s hardcoded `--socket-path` (§4.3) and `WDA_DETECTION_SOCKET_PATH`'s default (§9) must not drift independently — they name the same path by convention, not by a shared source of truth at runtime (the Bridge process has no `AgentSettings`). | The implementation plan's acceptance criteria include an explicit test/check that both resolve to the same `config/paths.py`-derived path at deployment time, so a future change to one is caught rather than silently diverging. |
