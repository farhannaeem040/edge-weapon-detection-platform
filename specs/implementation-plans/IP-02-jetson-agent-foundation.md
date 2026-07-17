# Implementation Plan: Jetson Agent Foundation

| Field | Value |
|-------|-------|
| Plan ID | IP-02 |
| Title | Jetson Agent Foundation |
| Status | Draft — awaiting approval |
| Milestone | Workstream B — Jetson Agent Foundation (Agent scaffolding, local persistence, device identity, first activation) |
| Realizes | FS-02 Increment B (Agent-side activation, reactivation, and local identity persistence) |
| Feature Specification Links | [`specs/features/FS-02-branch-device-onboarding.md`](../features/FS-02-branch-device-onboarding.md) §1.2, §5.5–§5.9, §8, §9, §10.4, §11, §12, §13, §14 (AC-3, AC-4, AC-7, AC-9, AC-10, AC-12, AC-13, AC-15, AC-16), §15 (T-07–T-18), §16 |
| Governing Documents | SRS-001 (frozen), ARCH-001 (Final), FS-02 (Final), Engineering Principles, Development Workflow |
| Depends On | IP-01 (T-01–T-30, complete — Backend `POST /api/v1/activate` delivered and verified) |
| Owner | Farhan Naeem |
| Explicitly Excluded From This Plan | DeepStream supervision behavior, YOLO/TensorRT inference, pipeline configuration, WebRTC, event upload, snapshot sync, siren commands, live monitoring, offline event synchronization, heartbeat/health reporting, Agent command API, Angular changes, Backend production changes |

This plan does not modify SRS-001, ARCH-001, or FS-02. It breaks already-approved FS-02 Increment B behavior into implementable, independently committable tasks, within the structure ARCH-001 fixes for the Jetson Agent. Where this plan settles a low-level detail (package choices, module names, file names), that detail sits inside the space FS-02 §19 and ARCH-001 explicitly leave to the Implementation Plan. Where the approved documents are genuinely silent on something this milestone needs, it is recorded in §21 (Open Items and Decisions) rather than silently chosen.

---

## 1. Milestone Objective

Establish the Jetson-side Agent as a running, testable FastAPI application that can perform its **first activation** against the real Backend contract delivered in IP-01, persist the resulting device identity durably and securely, and start correctly on every subsequent restart without re-activating.

This milestone is the Agent-side counterpart to IP-01's Backend work. IP-01 built and verified `POST /api/v1/activate` using test HTTP clients; IP-02 delivers the real FastAPI Agent as a caller of that already-complete, unchanged contract (FS-02 §1.1, §16). It deliberately stops before DeepStream, detection, and all ongoing operational traffic.

At the end of this milestone, a Jetson Agent can be installed, configured out-of-band with an Activation Key (ASM-006), started, and observed to activate exactly once — with its `DeviceId` and shared secret surviving restarts, and its shared secret rotating correctly on reactivation.

## 2. Scope

### 2.1 In Scope

| # | Item | Approved By |
|---|------|-------------|
| 1 | Python/FastAPI Agent project scaffolding and dependency management | ARCH-001 §9.1, §25, ADR-001; CON-006 (corrected) |
| 2 | Single-process, single-Uvicorn-worker runtime with coordinated async startup | ARCH-001 §11.2, ADR-010 |
| 3 | The `/opt/weapon-detection/` filesystem layout (only the directories this milestone uses) | ARCH-001 §13.2, ADR-008 |
| 4 | SQLite local store foundation: `DeviceIdentity` and `ConfigCache` tables, schema creation/migration approach | ARCH-001 §13.2, ADR-004; FS-02 §9 |
| 5 | Persistent local Device ID and protected shared-secret storage, with restrictive file permissions and atomic replacement | ARCH-001 §13.3, §16.4; FS-02 §8, §11, §12 |
| 6 | Agent first-activation client calling `POST /api/v1/activate` with the complete plaintext `keyId.secret` | FS-02 §5.5, §8, §10.4; ARCH-001 §16.2 |
| 7 | Reactivation behavior: re-call activation with a new key, retain `DeviceId`, atomically replace the shared secret | FS-02 §5.8, §8; ARCH-001 §16.4, ADR-015 |
| 8 | Startup behavior for both activated and unactivated Agents (activate once; never re-activate on a normal restart) | FS-02 §5.9, §8; ARCH-001 §16.3 |
| 9 | Backend connectivity/error handling for the activation call (timeouts, transport failure, non-2xx, envelope parsing) | ARCH-001 §23; FS-02 §13 |
| 10 | Cached-configuration **loading** at startup from `ConfigCache` (see OI-2 on population) | ARCH-001 §16.3, §18.3; FS-02 §5.9 |
| 11 | Logging foundation with mandatory secret redaction | ARCH-001 §15.6, §24; FS-02 §8, §11 |
| 12 | Test strategy using a simulated Backend, plus contract tests against the real Backend build | FS-02 §1.2, §15 T-18, AC-13; NFR-TST-001 |
| 13 | Jetson deployment and the systemd boundary (systemd manages only the Agent) | ARCH-001 §12.1, ARCH-CON-002, ADR-006 |

### 2.2 Out of Scope

Excluded because a later, not-yet-written Feature Specification governs it (FS-02 §1, §17 defer Agent bootstrap and DeepStream supervision explicitly):

- **DeepStream subprocess supervision behavior** — starting, stopping, restarting, monitoring, or crash detection of the DeepStream process (see OI-1). No Pipeline Supervisor is implemented in this milestone.
- **Agent health/status reporting behavior** and any Agent health endpoint (FR-HLT-001–005 belong to the health-monitoring Feature Specification; ARCH-001 §14.2 defines no Agent health route — inventing one is forbidden by ARCH-001 §2.2). See OI-3.
- DeepStream pipeline configuration or `pipeline/config.txt` generation (ARCH-001 §17.3).

Excluded because they belong to other approved-but-unplanned features:

- YOLO inference, TensorRT conversion, model files.
- WebRTC signaling, stream authorization, live monitoring (FR-MON-001).
- Detection ingest, the Unix domain socket listener, alert creation, event upload, snapshot synchronization.
- Heartbeat dispatch, offline event synchronization, configuration polling (`GET /api/v1/config`).
- The Agent's inbound Command API (siren, restart, config reload) and its shared-secret validation.
- Hardware abstraction layer / siren actuation.
- Recording, retention, and media cleanup.

Excluded by direction and by the frozen baseline:

- Any Angular change.
- Any Backend production change — including adding the operational-configuration payload to the activation response (see OI-2).
- Any modification to SRS-001, ARCH-001, FS-01, or FS-02.

## 3. Dependencies on IP-01

| Dependency | Status | Consumed How |
|------------|--------|--------------|
| `POST /api/v1/activate` fully implemented, enveloped, and integration-tested against real SQL Server | Complete (IP-01 T-20, T-21) | Called by the Agent unchanged; no Backend edit is permitted by this plan |
| Two-part Activation Key format `keyId.secret` | Complete (IP-01 §8, T-14) | The Agent transmits the complete plaintext key verbatim; it never parses or interprets the two parts |
| Uniform response envelope `{success, message, data}` / `{success, message, errorCode}` | Complete (IP-01 §11) | The Agent parses the envelope, not a bare body |
| Persistent `DeviceId` assignment/retention and shared-secret rotation | Complete (IP-01 T-19) | Relied upon; the Agent stores what the Backend returns and never derives identity itself |
| Branch creation and Activation Key regeneration via the Dashboard | Complete (IP-01 T-16, T-18, T-27, T-28) | Used to obtain keys for manual verification (§20) |

The Agent depends on the Backend contract only. It depends on no Backend internal type, database, or source file — the dependency direction is Agent → HTTP contract, never Agent → Backend code.

## 4. Agent Folder / Project Structure

```
edge-weapon-detection-platform/
├── backend/                                (existing — untouched)
├── frontend/                               (existing — untouched)
└── agent/
    ├── pyproject.toml                      (project metadata, dependencies, tool config)
    ├── README.md                           (how to install, configure, run, test)
    ├── src/weapon_detection_agent/
    │   ├── __init__.py
    │   ├── main.py                         (Uvicorn entrypoint — single worker, ADR-010)
    │   ├── app.py                          (FastAPI application factory + lifespan)
    │   ├── config/
    │   │   ├── settings.py                 (configuration model, §6)
    │   │   └── paths.py                    (filesystem layout resolution, §8)
    │   ├── persistence/
    │   │   ├── database.py                 (SQLite connection and schema management, §7)
    │   │   ├── schema.py                   (DDL + schema version, §7)
    │   │   ├── device_identity_repository.py
    │   │   └── config_cache_repository.py
    │   ├── activation/
    │   │   ├── backend_client.py           (POST /api/v1/activate client, §11)
    │   │   ├── activation_service.py       (activation/reactivation orchestration, §9)
    │   │   └── models.py                   (typed request/response/result models)
    │   ├── runtime/
    │   │   └── startup.py                  (startup decision workflow, §12)
    │   └── logging/
    │       └── setup.py                    (logging config + redaction filter, §15)
    └── tests/
        ├── unit/
        ├── integration/                    (against the simulated Backend, §16)
        └── contract/                       (against the real Backend build, §16)
```

This mirrors ARCH-001 §10.2's named Agent components (Local Persistence Manager, Backend Sync Client, Config Manager) at the module level, and introduces no component ARCH-001 does not name. Components ARCH-001 §10.2 lists but this milestone excludes (Pipeline Supervisor, Detection Ingest Handler, Alert Manager, Command API, Health Reporter, Hardware Abstraction Layer, WebRTC Signaling) get **no** module, package, or placeholder file here — they arrive with the plans that implement them (Engineering Principle 9; no speculative structure).

### 4.1 Dependency Direction

```
runtime → activation → persistence → config
                    ↘ logging ↙
```

`persistence` never imports `activation`; `config` and `logging` import nothing from the layers above them. The FastAPI layer (`app.py`) depends on `runtime`, not the reverse. HTTP concerns never reach into SQLite access directly.

## 5. Runtime Architecture

Fixed by ARCH-001 §11.2 / ADR-010 and preserved exactly:

- **One** FastAPI application under **one** Uvicorn worker. Multiple workers are never configured, because they would duplicate singleton device-level responsibilities. This is enforced explicitly in `main.py` (`workers=1`) and in the systemd unit (§17), and asserted by a test (§16).
- Startup work runs in the FastAPI **lifespan** handler, not at import time — so the module can be imported by tests without performing I/O, network calls, or activation.
- The activation exchange is performed **once** during startup, as an awaited async step, before the application reports ready.
- FastAPI is the control plane. No data-plane work exists in this milestone (no DeepStream, no media, no inference).

### 5.1 Why Activation Runs In Lifespan Rather Than On Demand

ARCH-001 §16.2/§16.3 places activation and identity loading at Agent startup; FS-02 §5.5 step 2 states the Agent calls `POST /api/v1/activate` "on startup". Lifespan is the FastAPI-native equivalent of that startup hook, and keeps the single-worker guarantee meaningful: with one worker, lifespan runs exactly once per process.

## 6. Configuration Model

The Agent's own bootstrap configuration (distinct from the Backend-synchronized operational configuration of FR-SYN-005/006, which this milestone does not implement — OI-2).

| Setting | Source | Required | Default | Notes |
|---------|--------|----------|---------|-------|
| `WDA_BACKEND_BASE_URL` | Environment | Yes | none | e.g. `http://<server-host>:<port>`. Trusted-LAN HTTP per ADR-002/CON-005 (§18). |
| `WDA_ACTIVATION_KEY` | Environment or the activation-key file (§6.1) | Only when unactivated | none | The complete plaintext `keyId.secret`. Never logged (§15). |
| `WDA_ROOT_PATH` | Environment | No | `/opt/weapon-detection` | Overridable **solely** so tests and workstation runs do not require write access to `/opt` (§8.2). |
| `WDA_HTTP_TIMEOUT_SECONDS` | Environment | No | `10` | Activation request timeout (§14). |
| `WDA_LOG_LEVEL` | Environment | No | `INFO` | |

Rules:

- Configuration is read once, at startup, into an immutable, validated settings object. No module reads `os.environ` directly.
- Missing `WDA_BACKEND_BASE_URL` fails startup with a clear, actionable error — the Agent never guesses a Backend address.
- The Activation Key is **not** a member of any object that is logged, formatted into a message, or serialized into an error.
- No secret is ever committed: `.env` files and the activation-key file are git-ignored and are never added to the repository.

### 6.1 Activation Key Provisioning (Decision D-1)

ASM-006 fixes that the installer configures the key out-of-band, and ARCH-001 ADR-008 approves a `config/` directory, but no approved document fixes the exact provisioning mechanism. **Decision:** the Agent accepts the key from either the `WDA_ACTIVATION_KEY` environment variable or a single-line file at `<root>/config/activation-key` (mode `0600`, owned by the Agent's service user), with the environment variable taking precedence. Recorded as a decision, not a requirement.

After a successful activation, the Agent **deletes** the activation-key file if it was the source, and logs that it did so — the key is single-use (BR-003) and retaining it on disk after consumption serves no purpose while widening exposure. If the key came from the environment, the Agent cannot remove it and does not attempt to; the deployment documentation (§17) instructs the installer to clear it.

## 7. SQLite Schema and Migration Approach

SQLite stores metadata only — never binary images or video (ADR-004, ADR-008, BR-006). This milestone stores no media of any kind.

Database file: `<root>/database/agent.db`, mode `0600`.

```sql
-- Schema version 1

CREATE TABLE IF NOT EXISTS SchemaVersion (
    Version     INTEGER NOT NULL
);

-- ARCH-001 §13.2: the Jetson's persistent local source for its Device ID and protected shared
-- secret only. Exactly one row ever exists; SingletonGuard enforces that structurally.
CREATE TABLE IF NOT EXISTS DeviceIdentity (
    SingletonGuard          INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
    DeviceId                TEXT    NOT NULL,
    ProtectedSharedSecret   TEXT    NOT NULL,
    ActivatedAt             TEXT    NOT NULL,   -- ISO-8601 UTC
    LastActivatedAt         TEXT    NOT NULL    -- ISO-8601 UTC; updated on each reactivation
);

-- ARCH-001 §13.2: the persistent local source for the last successfully synchronized
-- configuration, used during offline startup. Created here; populated by the configuration
-- feature (OI-2). Exactly one row ever exists.
CREATE TABLE IF NOT EXISTS ConfigCache (
    SingletonGuard          INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
    ConfigJson              TEXT    NOT NULL,
    UpdatedAt               TEXT    NOT NULL    -- ISO-8601 UTC
);
```

Notes and rationale:

- `SingletonGuard` gives the "exactly one device identity" invariant a schema-level guarantee, the same way IP-01 §4 gave "one Device per Branch" a unique constraint rather than trusting application code.
- `DeviceId` is stored as `TEXT`: the Backend's delivered `DeviceId` is a GUID (`ActivateResponseDto`), and SQLite has no native GUID type. The Agent treats it as an opaque identifier and never parses, validates, or reconstructs it — FS-02 §19 leaves the format open, so the Agent must not depend on it.
- `PendingEvents` (ARCH-001 §13.2) is **not** created here. It belongs to the detection/offline-sync features; creating it now would be speculative structure with no writer.

**Migration approach.** A single forward-only, idempotent schema module applies versioned DDL inside a transaction, guarded by `SchemaVersion`. No third-party migration framework is introduced: one table version and two tables do not justify a dependency (Engineering Principle 9), and the Backend's EF Core migrations are a separate concern on a separate tier. Schema creation runs at startup, is safe to run repeatedly, and each future version appends a step rather than editing a shipped one.

## 8. Filesystem Paths and Ownership

Fixed by ADR-008. Only the directories this milestone actually uses are created:

| Path | Mode | Purpose in This Milestone |
|------|------|---------------------------|
| `/opt/weapon-detection/` | `0750` | Root |
| `/opt/weapon-detection/config/` | `0700` | Activation-key file (§6.1) |
| `/opt/weapon-detection/database/` | `0700` | `agent.db` (`0600`) |
| `/opt/weapon-detection/logs/` | `0750` | Agent log files (§15) |

`snapshots/`, `recordings/`, `models/`, `pipeline/`, and `runtime/` are part of the approved ADR-008 layout but are **not** created by this milestone — nothing writes to them yet. The plans that introduce their writers create them.

Ownership: all paths owned by a dedicated, unprivileged service user and group (see D-2). The Agent never runs as root.

### 8.1 Decision D-2 — Service User

ARCH-001 does not name the Agent's OS user. **Decision:** a dedicated system user `weapon-detection` owns the layout and runs the Agent. Rationale: ARCH-001 §13.3 requires the shared secret be protected by restrictive OS file permissions, which is only meaningful if a specific, unprivileged principal owns the files. Recorded as a decision.

### 8.2 Root-Path Override

`WDA_ROOT_PATH` exists so that unit/integration tests and workstation development runs use a temporary directory instead of `/opt`. This is a testability affordance (ARCH-001 §2.1), not a deployment option: the systemd unit (§17) pins the root to `/opt/weapon-detection`. Permission modes are applied identically under an overridden root, so the permission tests are real tests.

## 9. Activation Workflow (Agent Side)

Implements FS-02 §5.5 steps 1–2 and 9–10, §5.8, and §8. Backend-side steps 3–8 are IP-01's and are not re-implemented, re-validated, or second-guessed here.

### 9.1 First Activation

1. Startup determines there is no local `DeviceIdentity` row (§12).
2. The Agent resolves the Activation Key (§6.1). Absent → startup fails with a clear, actionable error naming the missing configuration (never the key value).
3. The Agent `POST`s the **complete plaintext key** to `/api/v1/activate` as `{"activationKey": "<keyId.secret>"}`. It sends the key verbatim and never splits, inspects, or re-encodes it — parsing is exclusively the Backend's job (FS-02 §5.5 step 3).
4. On `200` with `success: true`, the Agent reads `data.deviceId`, `data.sharedSecret`, and `data.branchId`.
5. The Agent persists `DeviceId` and the protected shared secret to `DeviceIdentity` in a single SQLite transaction (§10).
6. If the key came from a file, the Agent deletes it (§6.1).
7. Activation is complete. The Agent is now able to authenticate future operational requests with `X-Device-Id`/`X-Device-Secret` — **using** those headers is a later feature's work and is not implemented here.
8. On any rejection or transport failure, §14 governs.

### 9.2 Reactivation

Per FS-02 §5.8 and ADR-015, reactivation occurs when a **new** Activation Key is configured on an Agent that already holds a Device identity.

1. Startup finds an existing `DeviceIdentity` row **and** an Activation Key present in configuration (§6.1) — this combination, and only this combination, is what distinguishes a reactivation from a normal restart. See D-3.
2. The Agent calls `POST /api/v1/activate` with the new key, exactly as in §9.1.
3. On success, the Agent compares the returned `deviceId` with the stored one. They are expected to match (FS-02 §8; ADR-015 retains the `DeviceId`). A mismatch is not silently accepted: the Agent logs a clear error identifying an unexpected identity change and fails startup, because a device silently adopting a new identity would break the historical correlation FR-BRN-007 guarantees. See OI-4.
4. The Agent **atomically replaces** the stored shared secret in one transaction; `DeviceId` is left untouched (FS-02 §5.8 step 8).
5. The key file is deleted as in §9.1 step 6.

### 9.3 Decision D-3 — Reactivation Trigger

FS-02 §5.8 states reactivation happens when "a new Activation Key is configured after a prior successful activation" but does not fix how the Agent detects it. **Decision:** the presence of an Activation Key alongside an existing `DeviceIdentity` triggers a reactivation attempt. Rationale: it is the only signal available to the Agent that is entirely within the installer's out-of-band control (ASM-006), and it makes §6.1's post-activation key deletion load-bearing — after a successful activation the key is gone, so an ordinary restart presents no key and correctly performs no activation (FS-02 §5.9 step 2). Recorded as a decision; it introduces no new endpoint, credential, or Backend behavior.

## 10. Device Identity and Secret-Storage Rules

| Rule | Source |
|------|--------|
| The `DeviceId` is persistent: assigned by the Backend, stored locally, never generated, derived, or altered by the Agent. | FR-BRN-007; FS-02 §1.3 |
| The `DeviceId` survives restarts and reactivations unchanged. | ADR-015; FS-02 §5.8, AC-7 |
| The shared secret is replaced **atomically** on every successful activation/reactivation — a crash mid-write must never leave a torn or empty secret. | ARCH-001 §16.4; FS-02 §5.8 step 8 |
| The shared secret is stored protected by restrictive OS file permissions (`agent.db` mode `0600`, `database/` mode `0700`, unprivileged owner). | ARCH-001 §13.3; FS-02 §11 |
| The Activation Key, its `secret` portion, and the shared secret are never written to logs, error messages, exception text, or tracebacks. | ARCH-001 §15.6; FS-02 §8, §11 |
| No secret is ever committed to the repository, in any form, including test fixtures using realistic-looking values. | IP-01 §17.4 precedent |

### 10.1 Decision D-4 — Meaning of "Protected" On the Jetson

ARCH-001 §13.3 specifies the Jetson stores the secret "in the local SQLite/configuration store **protected by restrictive OS file permissions**" — this is the approved protection mechanism, and it differs deliberately from the Backend's Data Protection approach (IP-01 §7), which exists because the Backend must re-present the secret for outbound command authentication.

**Decision:** the Agent stores the shared secret as its plain value inside a `0600` SQLite database owned by an unprivileged service user, and adds no application-layer encryption. Rationale: an encryption key stored on the same device, readable by the same user, protects against nothing while implying a security property the prototype does not have — a false claim is worse than an accurate limitation (ARCH-001 §2.3, §15.6). The column is named `ProtectedSharedSecret` for consistency with ARCH-001 §13.2's schema naming; "protected" means file-permission-protected, and the code documents that explicitly so no future reader assumes encryption at rest. This is the trusted-LAN prototype posture CON-005/§15.6 already accepts, and is listed among the security limitations in §18.

**Atomicity** is delivered by a single SQLite transaction (`UPDATE DeviceIdentity SET ProtectedSharedSecret = ?, LastActivatedAt = ? WHERE SingletonGuard = 1`), committed or rolled back as one unit — the local equivalent of the guarantee IP-01 §8 step 6 gives on the Backend.

## 11. Backend API Contracts Consumed

Exactly one endpoint is consumed by this milestone. It is reproduced here from the delivered contract; this plan does not alter it.

### `POST /api/v1/activate`

| Aspect | Detail |
|--------|--------|
| Auth | None. The Activation Key is the credential (FS-02 §10.4). No JWT, no device headers. |
| Request | `{"activationKey": "<keyId>.<secret>"}` — the complete plaintext key |
| Success | `200` — `{"success": true, "message": null, "data": {"deviceId": "<guid>", "sharedSecret": "<string>", "branchId": "<guid>"}}` |
| Failure | `401` — `{"success": false, "message": "...", "errorCode": "INVALID_ACTIVATION_KEY"}` for **every** rejection reason: malformed, unknown `keyId`, incorrect secret, consumed, invalidated (FS-02 §5.6, AC-15) |
| Casing | camelCase JSON; `null` members omitted from responses |

The response carries **no operational configuration** (see OI-2). The Agent must not require, assume, or fabricate configuration fields, and must not fail if they are absent.

No other Backend endpoint is called. The Agent defines no endpoint of its own in this milestone (ARCH-001 §14.2's Agent routes all belong to excluded features).

## 12. Startup and Shutdown Workflows

### 12.1 Startup Decision (FS-02 §5.9; ARCH-001 §16.3)

```
load settings → configure logging → ensure filesystem layout → open SQLite, apply schema
                                                                      │
                                                    read DeviceIdentity (0 or 1 row)
                                                                      │
        ┌─────────────────────────────────┬───────────────────────────┴──────────────────┐
   no identity                       identity + key present                      identity, no key
        │                                 │                                              │
  key present? ──no──► FAIL startup   REACTIVATE (§9.2)                   NORMAL RESTART (§12.2)
        │ yes                             │                                              │
   ACTIVATE (§9.1)                        └──────────────┬───────────────────────────────┘
        │                                                │
        └────────────────────────────────────────────────┴──► load ConfigCache (may be empty, OI-2)
                                                                          │
                                                              application reports ready
```

### 12.2 Normal Restart (Activated, No Key)

Per FS-02 §5.9 and ARCH-001 §16.3: the Agent loads its `DeviceId`, shared secret, and cached configuration from local storage, **does not** call `POST /api/v1/activate`, and requires no Backend contact to start. Backend unavailability at this point is not an error and must not fail startup — this is the offline-startup guarantee, and it is tested (§16).

Resuming authenticated operational communication and starting DeepStream (FS-02 §5.9 step 3; ARCH-001 §16.3 steps 2–4) are **excluded** — they belong to the later features that implement that traffic and that supervision.

### 12.3 Unactivated With No Key

Startup fails with a clear, actionable error stating that an Activation Key must be provisioned. Rationale: ARCH-001 §16.3 states initial activation requires Backend connectivity and that only *subsequent* startups proceed from local state; an Agent with neither identity nor key has nothing to do. Failing loudly beats running in a silently useless state (IP-01 §6 precedent, T-06).

### 12.4 Shutdown

The lifespan shutdown handler closes the SQLite connection and the HTTP client cleanly, and logs a shutdown line. No DeepStream subprocess exists to terminate in this milestone. Shutdown is idempotent and must not error when startup failed partway.

## 13. DeepStream Subprocess Boundary

**No DeepStream code, abstraction, interface, module, or placeholder is created by this milestone.**

The boundary itself is already fixed and is preserved by this plan through what it declines to build:

- DeepStream is the data plane; the Agent is the control plane (ARCH-001 §6.1, §8.1, ADR-001).
- The Agent supervises DeepStream as an OS subprocess; systemd manages **only** the Agent (ARCH-CON-002, ADR-006).
- All Agent↔DeepStream communication is exclusively via the Unix domain socket (ADR-005, ARCH-001 §11.2).

Why nothing is built: FS-02 §1 and §17 explicitly defer "starting or supervising the DeepStream pipeline" to a later Feature Specification which does not yet exist in this repository. The Development Workflow places Specification before Implementation Planning, and Engineering Principle 2 requires requirements before implementation. Authoring a supervision abstraction here would mean inventing that specification inside an implementation plan — precisely what ARCH-001 §2.2 and CLAUDE.md forbid. See OI-1.

This milestone's contribution to that future work is negative and deliberate: it ensures the runtime (§5), persistence (§7), and configuration (§6) foundations the Pipeline Supervisor will sit on exist, without pre-judging its design.

## 14. Error and Retry Behavior

ARCH-001 §23 fixes the *shape* (caller-side timeout/retry with backoff) and states that exact retry counts, backoff intervals, and timeout values are Feature Specification detail. FS-02 does not fix them for the activation call. **Decision D-5**, within that explicitly delegated space:

| Condition | Behavior |
|-----------|----------|
| Transport failure / timeout / connection refused | Retry with exponential backoff: 3 attempts, ~1s/2s/4s, jittered. Then fail startup with a clear error. Rationale: ARCH-001 §16.3 makes initial activation *require* Backend connectivity — an Agent that cannot activate has no identity and cannot function, so silently continuing is wrong. |
| `401` with `errorCode: INVALID_ACTIVATION_KEY` | **Never retried.** Fail startup immediately with an actionable error ("the configured Activation Key was rejected; regenerate it via the Dashboard and reprovision"). Retrying a deterministic credential rejection cannot succeed. |
| `5xx` | Retried under the same backoff as a transport failure — a server-side fault may be transient. |
| Other non-2xx, or `200` with `success: false`, or an unparseable/incomplete envelope | Not retried; fail startup with a clear error. The Agent does not guess at a contract it does not recognize. |
| Success | No retry. The key is now consumed (BR-003); a blind retry would present a consumed key and be correctly rejected. |

The Agent is **idempotent-safe by construction**: it attempts activation at most once per startup per outcome, and never retries after a success or a credential rejection. Retry counts and intervals are configuration-free constants in this milestone; making them tunable would be speculative (Engineering Principle 9).

## 15. Logging and Secret-Redaction Requirements

Realizes ARCH-001 §15.6 and §24, and FS-02 §8/§11.

- Agent logs are written to `<root>/logs/` (ADR-008) and to stdout (which systemd's journal captures, §17).
- Log lines include the `DeviceId` once known, as ARCH-001 §24's lightweight correlation mechanism. The `DeviceId` is an identifier, not a credential — it travels in a header on every operational request (§11) and is displayed in the Dashboard (FS-02 §5.4); logging it is correct.
- **Never logged, at any level, including `DEBUG`:** the Activation Key, its `keyId` or `secret` portions, and the device shared secret.
- Redaction is **structural, not a matter of care**: a logging filter is installed at configuration time that redacts known secret values and secret-shaped patterns from every record — message, arguments, and exception text alike. Rationale: relying on every future call site to remember is exactly the failure mode ARCH-001 §15.6 cannot tolerate, and a filter is verifiable by test where discipline is not.
- Secret-carrying models define `__repr__`/`__str__` that render a redaction placeholder, so an accidental interpolation or an unhandled exception carrying the object cannot leak the value.
- Log rotation thresholds are deferred (ARCH-001 §24 defers them to Feature Specifications); this milestone writes plain rotating files with a conservative size cap and does not commit to a retention policy.
- A test asserts that a full activation run — success **and** every failure path — produces no log output containing the key or the secret (§16).

## 16. Testing Strategy

Realizes NFR-TST-001 and FS-02 §1.2/AC-13. The organizing rule is IP-01 §9's: **a test may only claim what its substrate can actually prove.**

### 16.1 Unit Tests

| Target | Cases |
|--------|-------|
| Settings/configuration model | Required values present → valid object; missing `WDA_BACKEND_BASE_URL` → clear startup failure; defaults applied; key resolution precedence (env over file) |
| Filesystem layout | Directories created with the specified modes; idempotent on re-run; only this milestone's directories created (no `snapshots/`, `recordings/`, `models/`, `pipeline/`) |
| SQLite schema | Tables created; schema application idempotent across repeated runs; `SingletonGuard` rejects a second `DeviceIdentity` row |
| `DeviceIdentityRepository` | Store/load round-trip; atomic secret replacement leaves `DeviceId` unchanged; a simulated mid-transaction failure leaves the prior secret intact (never torn/empty) |
| `ConfigCacheRepository` | Absent cache returns empty, not an error (OI-2); round-trip when present |
| `BackendActivationClient` | Sends the complete key verbatim; parses a success envelope; maps `401`/`INVALID_ACTIVATION_KEY` to a typed rejection; maps transport failure, `5xx`, and malformed envelopes to their distinct typed results |
| Retry policy (§14) | Transport failure retries 3× then fails; `401` never retried; success never retried; `5xx` retried |
| `ActivationService` | First activation persists identity; reactivation retains `DeviceId` and replaces the secret; a returned `DeviceId` mismatch fails startup (§9.2 step 3) |
| Startup decision (§12.1) | All four branches: no identity + key → activate; no identity + no key → fail; identity + key → reactivate; identity + no key → normal restart with no HTTP call whatsoever |
| Logging redaction | Key and secret absent from output across success and every failure path; secret-carrying models redact under `repr()`/`str()` and inside exception text |
| Runtime | Uvicorn is invoked with exactly one worker (ADR-010) |

### 16.2 Integration Tests — Simulated Backend

A simulated Backend (an in-process ASGI/HTTP stub) serves `POST /api/v1/activate` with the **exact delivered contract** of §11 — same field names, same envelope, same status codes, same uniform `401`. It is a test double for the *Backend*, never for the Agent: the Agent under test is the real Agent, exercising its real client, its real SQLite store, and its real startup path against a temporary root (§8.2).

| Scenario | Verifies (FS-02) |
|----------|------------------|
| Fresh Agent + valid key → activates; identity persisted; `data` consumed correctly | AC-3, AC-12; T-07 |
| Restart after activation, key removed → **no** HTTP request is made; identity loaded from SQLite | AC-7; T-16; §5.9 |
| Restart with Backend unreachable, already activated → starts successfully anyway | ARCH-001 §16.3 |
| Consumed key (simulated `401`) → startup fails, no retry, identity untouched | AC-4, AC-9; T-08 |
| Malformed key, unknown `keyId`, incorrect secret (all simulated `401`) → identical Agent-observable handling; no identity written | AC-15; T-09, T-10, T-11 |
| Invalidated key (simulated `401`) → startup fails cleanly | T-12 |
| Reactivation with a new key → same `DeviceId` retained, new secret stored, old secret gone | AC-7; T-14; §5.8 |
| Returned `DeviceId` differs from stored → startup fails loudly (OI-4) | FR-BRN-007 guard |
| Transport failure → retries then fails with an actionable error | §14 |
| No secret or key appears in any log across every scenario above | §15; FS-02 §11 |

### 16.3 Contract Tests — Real Backend

The simulated Backend proves the Agent's behavior; it cannot prove the contract is real — a stub that mis-encodes the envelope would pass every test above while failing against the actual server. So a contract suite runs the real Agent against a **real, running IP-01 Backend build with real SQL Server**, using a branch and Activation Key created through the real Dashboard API:

| Scenario | Verifies |
|----------|----------|
| Real key → real activation → `DeviceId`/secret persisted locally; the Dashboard's device view flips to `Activated` with that same `DeviceId` | AC-3, AC-13; T-07, T-18 |
| Reuse of the now-consumed key → real `401`; Agent fails startup; Backend Device record unchanged | AC-4, AC-9; T-08 |
| Regenerate via the real endpoint → old key rejected; new key activates; `DeviceId` retained; secret rotated | AC-5, AC-7; T-13, T-14 |

This is the AC-13/T-18 obligation discharged: the real Agent produces Backend behavior identical to IP-01's simulated callers, against the same Backend build, with no simulator-specific accommodation. These tests are workstation-runnable (no Jetson required) and are marked so they can be skipped when no Backend/SQL Server is available, exactly as IP-01's SQL-Server-dependent suites are.

### 16.4 Not Tested Here

Concurrent activation (AC-16) is a **Backend** guarantee, already verified against real SQL Server by IP-01 T-21. A single Agent activating once at startup cannot exercise it and this plan does not restate it as an Agent test.

## 17. Jetson Deployment Strategy

| Aspect | Decision | Source |
|--------|----------|--------|
| Process manager | systemd manages **only** the Agent. It never manages DeepStream. | ARCH-CON-002, ADR-006 |
| Unit | One `weapon-detection-agent.service`, `Type=simple`, `Restart=on-failure`, `User=weapon-detection`, `WorkingDirectory=/opt/weapon-detection` | ARCH-001 §12.1 |
| Restart semantics | `Restart=on-failure` is what makes ADR-007's future Restart-Agent command work (the Agent exits; systemd brings it back). The command itself is **not** implemented here. | ADR-007 |
| Workers | One Uvicorn worker, pinned in the unit and in `main.py` | ADR-010 |
| Environment | `WDA_BACKEND_BASE_URL` and (first boot only) `WDA_ACTIVATION_KEY` supplied via a `0600` root-owned `EnvironmentFile`, or the key file per §6.1. Never baked into the unit, never committed. | §6.1, §15 |
| Python runtime | A virtual environment under the Agent installation, using the Jetson's system Python (ARCH-ASM-002 assumes compatible runtimes). No container: DeepStream's future GPU/hardware access is a real complication and this milestone must not pre-commit to a packaging model it cannot yet validate. |
| Installation | A documented, ordered manual procedure in `agent/README.md`: create the user, create the layout with the §8 modes, install the venv, install the unit, provision the key, start, verify. |

**Jetson-dependent vs. workstation-testable.** Only T-41 requires the Jetson. Everything the Agent does in this milestone — HTTP, SQLite, filesystem, async startup — is platform-neutral, so T-31–T-40 are fully developed and tested on Windows or standard Linux. The path layout and permission model are POSIX; under Windows they are exercised against an overridden root (§8.2), with the permission-mode assertions skipped where the OS has no equivalent, and validated for real on Linux/Jetson in T-41. This is stated plainly rather than papered over: mode bits are not meaningfully verifiable on Windows.

## 18. Security Constraints

Preserved, not reinterpreted:

- **Trusted-LAN HTTP.** The Agent calls the Backend over HTTP, not HTTPS (ADR-002, ARCH-CON-006, CON-005). The Activation Key and shared secret are therefore interceptable on the LAN in principle — an accepted prototype risk (ARCH-001 §15.6), not a claim of production security. The client does not disable, weaken, or fake TLS; it simply does not use it, and HTTPS remains future work (§28.2).
- **No new credentials, endpoints, or mechanisms.** No certificates, mTLS, OAuth/OIDC, HMAC signing, refresh tokens, or key rotation beyond the reactivation replacement ADR-015 already approves.
- **The Activation Key is single-use.** The Agent presents it once and deletes it on success (§6.1).
- **Secrets never in logs** (§15), never in URLs or query strings (the key travels in the request body), never in the repository, never in a committed test fixture.
- **Least privilege.** Unprivileged service user; `0600` on the database and key file; `0700` on their directories.
- **No secret is echoed back.** No Agent endpoint, log line, or error message exposes the `DeviceId`'s secret or the Activation Key. The Agent exposes no endpoint at all in this milestone.
- **Known limitation, stated honestly:** the shared secret is protected by file permissions only, with no encryption at rest on the Jetson (D-4). This is ARCH-001 §13.3's approved mechanism; a root compromise of the Jetson exposes it. Recorded, not hidden.

## 19. Acceptance Criteria

The milestone is accepted when each of the following is demonstrated by a passing automated test, except where noted as manual:

| # | Criterion | Traces To |
|---|-----------|-----------|
| MAC-1 | A fresh Agent configured with a valid, unconsumed Activation Key activates successfully at startup against the real Backend and receives `deviceId`, `sharedSecret`, `branchId`. | FS-02 AC-3, AC-12; T-07 |
| MAC-2 | The `DeviceId` and shared secret persist to local SQLite and survive a restart. | FS-02 AC-7; ARCH-001 §13.2 |
| MAC-3 | A restart of an already-activated Agent makes **no** call to `POST /api/v1/activate`. | FS-02 AC-7, §5.9; T-16 |
| MAC-4 | An already-activated Agent starts successfully with the Backend unreachable. | ARCH-001 §16.3 |
| MAC-5 | Reuse of a consumed key is rejected; the Agent fails startup, does not retry, and leaves its stored identity untouched. | FS-02 AC-4, AC-9; T-08 |
| MAC-6 | Malformed, unknown-`keyId`, incorrect-secret, and invalidated keys are all handled identically by the Agent, with no local identity written. | FS-02 AC-15; T-09–T-12 |
| MAC-7 | Reactivation with a regenerated key retains the same `DeviceId` and atomically replaces the shared secret. | FS-02 AC-7; ADR-015; T-14 |
| MAC-8 | The real Agent produces Backend behavior identical to a simulated caller against the same Backend build, with no simulator-specific Backend accommodation. | FS-02 AC-13; T-18 |
| MAC-9 | No Activation Key, key secret, or shared secret appears in any log line, on any path, at any level. | ARCH-001 §15.6; FS-02 §11 |
| MAC-10 | The Agent runs under exactly one Uvicorn worker. | ADR-010 |
| MAC-11 | The `/opt/weapon-detection/` layout is created with the specified ownership and modes, and only the directories this milestone uses. | ADR-008; ARCH-001 §13.3 |
| MAC-12 | Under systemd on the real Jetson, the Agent starts on boot, activates on first boot, and restarts on failure. | ARCH-001 §12.1; ADR-006 — **manual, Jetson-only (T-41)** |
| MAC-13 | No DeepStream, inference, WebRTC, heartbeat, event-upload, siren, or command code exists in the Agent. | §2.2 — verified by review |
| MAC-14 | No Backend, Angular, SRS, ARCH-001, or FS-02 file is modified by this milestone. | §2.2 — verified by `git diff` |

## 20. Definition of Done

The "Jetson Agent Foundation" milestone is done when:

1. All tasks T-31 through T-41 are complete, each independently committed.
2. Every criterion in §19 is demonstrated — MAC-1–MAC-11 by passing automated tests, MAC-12 by the manual Jetson verification, MAC-13/MAC-14 by review.
3. The manual verification script (§21) runs end-to-end against a locally running IP-01 Backend and a real Jetson, with no manual database manipulation on either tier.
4. No plaintext Activation Key, key secret, or device shared secret appears in any committed file, test fixture, log output, or source-controlled configuration.
5. The Agent's unit and simulated-Backend suites pass on a workstation with **no Jetson, no DeepStream, no camera, and no GPU** present.
6. The contract suite (§16.3) passes against the real IP-01 Backend build with real SQL Server, unmodified.
7. `agent/README.md` documents installation, configuration, running, testing, and the Jetson deployment procedure (Engineering Principle 10).
8. No SRS-001, ARCH-001, FS-01, or FS-02 content was modified; any ambiguity found during implementation was raised for clarification rather than silently resolved in code.
9. Every open item in §22 is either resolved by an approved document or still recorded as open — none has been silently decided in code.

## 21. Manual Verification Script

Prerequisites: the IP-01 Backend and SQL Server running; the Angular Dashboard running; a Jetson (or a Linux host standing in, for steps 1–11) reachable on the same LAN.

1. On the Dashboard, log in as Admin and create a branch with one camera. Record the plaintext Activation Key shown exactly once.
2. Confirm the branch's Device shows **Unactivated** with no Device ID.
3. On the Jetson, create the service user and the `/opt/weapon-detection/` layout per `agent/README.md`. Confirm the modes match §8.
4. Provision the Activation Key to `/opt/weapon-detection/config/activation-key` (mode `0600`) and set `WDA_BACKEND_BASE_URL`.
5. Start the Agent. Confirm it logs a successful activation and exits startup healthy.
6. Confirm the activation-key file has been deleted (§6.1).
7. Inspect `/opt/weapon-detection/logs/` — confirm **no** Activation Key and **no** shared secret appear anywhere.
8. Refresh the Dashboard's branch detail — confirm the Device now shows **Activated** with a Device ID.
9. Query the Agent's SQLite (`sqlite3 /opt/weapon-detection/database/agent.db "SELECT DeviceId FROM DeviceIdentity"`) — confirm it matches the Dashboard's Device ID exactly. Confirm the file is mode `0600`.
10. Restart the Agent. Confirm it starts successfully and logs **no** activation attempt.
11. Stop the Backend, then restart the Agent. Confirm it still starts successfully (offline startup, ARCH-001 §16.3). Restart the Backend.
12. On the Dashboard, regenerate the branch's Activation Key. Provision the new key to the Agent and restart it.
13. Confirm the Agent logs a reactivation, and confirm via SQLite that `DeviceId` is **unchanged** while `LastActivatedAt` has advanced.
14. Provision a deliberately malformed key (no `.` separator) to a fresh Agent (empty database) and start it → confirm startup fails with a clear error, no retry storm, and no identity row written.
15. Re-provision the key consumed in step 5 to that fresh Agent → confirm startup fails identically to step 14, with no observable difference.
16. **Jetson-only:** install the systemd unit, `systemctl enable --now weapon-detection-agent`, reboot the Jetson, and confirm the Agent starts automatically and reaches the same activated state. Confirm `systemctl` manages the Agent and that **no** DeepStream unit exists.

A successful run of steps 1–16 demonstrates the milestone against real, production API contracts on real hardware, with no simulator-only path.

## 22. Open Items and Decisions

### 22.1 Open Items (Require a Decision Outside This Plan)

| ID | Open Item | Impact | Recommended Resolution |
|----|-----------|--------|------------------------|
| **OI-1** | **No Feature Specification exists for Agent bootstrap / DeepStream supervision.** FS-02 §1 and §17 defer it explicitly to "a later Feature Specification"; none is in the repository. ARCH-001 (§10.2 Pipeline Supervisor, §11.1 scenario 1, ADR-006) fixes the *architecture*, but not the feature behavior an implementation plan needs. | DeepStream supervision, the Pipeline Supervisor, and the pipeline lifecycle are **excluded** from IP-02 (§2.2, §13). This is why this milestone stops where it does. | Author **FS-03 — Jetson Agent Bootstrap & DeepStream Supervision** before the plan that implements it. IP-03 should follow that spec, not this plan. |
| **OI-2** | **The delivered activation response carries no operational configuration.** FS-02 §10.4/§5.5 step 8 anticipate branch/camera/operational configuration; the delivered `ActivateResponseDto` returns `deviceId`, `sharedSecret`, `branchId` only, because IP-01 modeled no Configuration entity (IP-01 §4; README "Known Limitations"). Backend production changes are excluded from IP-02. | `ConfigCache` is **created but never populated** by this milestone. §12.1's "load cached configuration" step therefore resolves to an empty cache, and must not error. The offline-startup guarantee (ARCH-001 §16.3) is structurally in place but not yet meaningful. | Resolve in the configuration Feature Specification: it must decide whether the activation response is extended to carry configuration (FS-02's stated intent, requiring a Backend change) or configuration arrives solely via `GET /api/v1/config`. **Do not decide this inside IP-02.** |
| **OI-3** | **No approved Agent health/status endpoint exists.** ARCH-001 §14.2 lists the Agent's routes; none is a health/status route. FR-HLT-001–005 specify a *heartbeat to the Backend*, not an Agent-exposed endpoint, and belong to the health-monitoring feature. | No Agent health/status endpoint or reporting is built (§2.2). Adding one would be an invented route (ARCH-001 §2.2). | Address in the health-monitoring Feature Specification. If an Agent-side health route is wanted, ARCH-001 §14.2 needs a controlled amendment first. |
| **OI-4** | **Behavior on a `DeviceId` mismatch at reactivation is unspecified.** FS-02 §8 says the `DeviceId` "is expected to remain the same"; ADR-015 guarantees the Backend retains it. Neither states what the Agent does if the returned value differs. | §9.2 step 3 fails startup loudly rather than silently adopting the new identity, since silent adoption would break FR-BRN-007's historical correlation. This is a **defensive guard against a state the Backend should never produce**, chosen as the safest reading — flagged rather than presented as settled. | Confirm in the Agent bootstrap Feature Specification (OI-1). If the intended behavior is "trust the Backend and overwrite", the guard changes to a warning. |

### 22.2 Decisions Recorded In This Plan

Each sits inside space an approved document explicitly left open; none contradicts ARCH-001, SRS-001, or FS-02.

| ID | Decision | Space It Occupies |
|----|----------|-------------------|
| **D-1** | Activation Key provisioned via `WDA_ACTIVATION_KEY` or a `0600` `<root>/config/activation-key` file (env wins); deleted after successful activation. | ASM-006 fixes "out-of-band installer action" but no mechanism; ADR-008 approves `config/`. |
| **D-2** | A dedicated unprivileged `weapon-detection` system user owns the layout and runs the Agent. | ARCH-001 §13.3 requires restrictive file permissions but names no user. |
| **D-3** | Reactivation is triggered by an Activation Key present alongside an existing `DeviceIdentity`. | FS-02 §5.8 states the condition but not the detection mechanism. |
| **D-4** | On the Jetson, "protected" means file-permission-protected (`0600`, unprivileged owner), with no application-layer encryption; atomicity via a single SQLite transaction. | ARCH-001 §13.3 specifies exactly this mechanism for the Jetson; §15.6 accepts the residual risk. |
| **D-5** | Activation retry policy: 3 attempts with jittered exponential backoff for transport/`5xx` failures; never retry a `401` or a success. | ARCH-001 §23 delegates "exact retry counts, backoff intervals, and timeout values" to Feature-Specification/plan level. |
| **D-6** | A hand-rolled versioned SQLite schema module rather than a migration framework. | ARCH-001/ADR-004 fix SQLite but no tooling; Engineering Principle 9. |
| **D-7** | A virtual environment on the Jetson's system Python; no container. | ARCH-ASM-002 assumes compatible runtimes; packaging is unspecified. |

## 23. Implementation Tasks (Dependency Order)

Numbering continues from IP-01's T-30 so task IDs remain globally unique across the project. Each task is sized to be implemented, verified, and committed independently (Engineering Principle 3).

**Platform split:** T-31–T-40 are fully workstation-testable on Windows or standard Linux — no Jetson, GPU, camera, or DeepStream required. **T-41 alone requires the Jetson.**

---

**T-31 — Agent project scaffolding and tooling**
- Objective: Create the `agent/` Python project — `pyproject.toml`, package layout per §4, dependency pinning (FastAPI, Uvicorn, an HTTP client, test tooling), lint/format/type-check configuration, and a `.gitignore` covering `.env`, the activation-key file, `*.db`, and virtual environments.
- Dependencies: None (IP-01 complete)
- Files/Components: `agent/pyproject.toml`, `agent/src/weapon_detection_agent/__init__.py`, `agent/README.md`, `agent/.gitignore`
- Serves: None directly (scaffolding)
- Expected Output: The package installs into a clean virtual environment; the test suite runs green (empty); lint and type-check pass.
- Tests: None (scaffolding)
- Completion Evidence: A clean `pip install -e .` plus a test/lint/type-check run succeeds on a workstation with no Jetson present.
- Documentation: `agent/README.md` — install and run instructions; root `README.md` — note the new `agent/` component.
- Exclusions: No FastAPI app, no endpoints, no business logic, no DeepStream, no placeholder modules for excluded components.

**T-32 — Configuration model and settings loading**
- Objective: Implement the immutable, validated settings object of §6, including key resolution precedence (§6.1) and fail-fast on a missing `WDA_BACKEND_BASE_URL`.
- Dependencies: T-31
- Files/Components: `config/settings.py`
- Serves: NFR-CFG-001 (Agent bootstrap configuration is not hardcoded)
- Expected Output: Settings load from the environment/key file; missing required values fail with a clear, actionable error; no module reads `os.environ` directly.
- Tests: Unit — §16.1 (settings/configuration model)
- Completion Evidence: Unit tests pass, including the key-precedence and fail-fast cases.
- Documentation: `agent/README.md` — the settings table.
- Exclusions: No Backend-synchronized operational configuration (OI-2); no config polling.

**T-33 — Logging foundation with secret redaction**
- Objective: Implement §15 — log configuration to `<root>/logs/` and stdout, plus the structural redaction filter and the redacting `__repr__`/`__str__` convention.
- Dependencies: T-32
- Files/Components: `logging/setup.py`
- Serves: ARCH-001 §15.6, §24; FS-02 §11; MAC-9
- Expected Output: Logging is configured once at startup; secrets and secret-shaped values are redacted from messages, arguments, and exception text.
- Tests: Unit — §16.1 (logging redaction), including redaction inside exception tracebacks.
- Completion Evidence: Unit tests pass, including a test asserting a deliberately leaked secret is redacted rather than emitted.
- Documentation: none (rules stated in this plan)
- Exclusions: No log rotation/retention policy commitment (ARCH-001 §24 defers it).

**T-34 — Filesystem layout provisioning**
- Objective: Implement §8 — resolve the root (honoring `WDA_ROOT_PATH`), create only this milestone's directories with the specified modes, idempotently.
- Dependencies: T-32
- Files/Components: `config/paths.py`
- Serves: ADR-008; ARCH-001 §13.3; MAC-11
- Expected Output: The layout is created with correct modes and is safe to re-run; excluded directories are not created.
- Tests: Unit — §16.1 (filesystem layout), including the assertion that `snapshots/`/`recordings/`/`models/`/`pipeline/` are absent. Mode assertions skip on Windows (§17).
- Completion Evidence: Unit tests pass on the workstation; modes verified for real in T-41.
- Documentation: `agent/README.md` — the layout table.
- Exclusions: No media directories; no ownership changes (the installer creates the user, T-41).

**T-35 — SQLite foundation and schema**
- Objective: Implement §7 — connection management to `<root>/database/agent.db` (mode `0600`) and the idempotent, versioned schema module creating `SchemaVersion`, `DeviceIdentity`, and `ConfigCache`.
- Dependencies: T-34
- Files/Components: `persistence/database.py`, `persistence/schema.py`
- Serves: ADR-004; ARCH-001 §13.2; FS-02 §9
- Expected Output: The database and schema are created on first run; repeated application is a no-op; `SingletonGuard` rejects a second `DeviceIdentity` row at the schema level.
- Tests: Unit — §16.1 (SQLite schema), including idempotency and the singleton constraint.
- Completion Evidence: Unit tests pass; a fresh run produces a `0600` database file with the expected schema.
- Exclusions: No `PendingEvents` table; no media/binary storage of any kind (BR-006).
- Documentation: none (schema documented in this plan §7)

**T-36 — Device identity and config-cache repositories**
- Objective: Implement `DeviceIdentityRepository` (load, store-on-first-activation, atomic secret replacement retaining `DeviceId`) and `ConfigCacheRepository` (load; absent → empty, not an error).
- Dependencies: T-35
- Files/Components: `persistence/device_identity_repository.py`, `persistence/config_cache_repository.py`
- Serves: FS-02 AC-7, §9; ARCH-001 §13.2, §16.4; MAC-2, MAC-7
- Expected Output: Round-trip persistence; the secret replaces atomically in one transaction while `DeviceId` is untouched; a simulated mid-transaction failure leaves the prior secret intact.
- Tests: Unit — §16.1 (`DeviceIdentityRepository`, `ConfigCacheRepository`)
- Completion Evidence: Unit tests pass, including the torn-write/rollback case.
- Documentation: none
- Exclusions: No `ConfigCache` **writer** (OI-2); no event/alert persistence.

**T-37 — Backend activation client**
- Objective: Implement the `POST /api/v1/activate` HTTP client per §11 and the retry policy of §14 (D-5): send the complete key verbatim, parse the envelope, and map every outcome to a distinct typed result.
- Dependencies: T-32, T-33
- Files/Components: `activation/backend_client.py`, `activation/models.py`
- Serves: FS-02 AC-3, AC-15, §10.4; ARCH-001 §23
- Expected Output: Success parses `deviceId`/`sharedSecret`/`branchId`; `401` maps to a typed rejection and is never retried; transport/`5xx` failures retry with backoff then fail; malformed envelopes fail without guessing.
- Tests: Unit — §16.1 (`BackendActivationClient`, retry policy), with the transport layer stubbed.
- Completion Evidence: Unit tests pass across every outcome, including the never-retry-a-401 assertion.
- Documentation: none (contract stated in §11)
- Exclusions: No other Backend endpoint; no `X-Device-Id`/`X-Device-Secret` usage; no heartbeat/sync/config calls.

**T-38 — Activation service (first activation and reactivation)**
- Objective: Implement §9's orchestration — first activation persists identity and deletes the key file; reactivation retains `DeviceId`, atomically replaces the secret, and fails loudly on a `DeviceId` mismatch (OI-4).
- Dependencies: T-36, T-37
- Files/Components: `activation/activation_service.py`
- Serves: FS-02 AC-3, AC-4, AC-7, AC-9, AC-12, §5.5, §5.8; MAC-1, MAC-5, MAC-7
- Expected Output: Both flows behave per §9; the key file is removed after success; a mismatched `DeviceId` fails startup with a clear error.
- Tests: Unit — §16.1 (`ActivationService`)
- Completion Evidence: Unit tests pass, including the mismatch guard and post-success key deletion.
- Documentation: none
- Exclusions: No `POST /api/v1/activate` Backend change; no DeepStream start after activation (ARCH-001 §16.2's final step is excluded — OI-1).

**T-39 — Startup workflow and FastAPI runtime**
- Objective: Implement §12's four-branch startup decision inside the FastAPI lifespan, the application factory, and the single-worker Uvicorn entrypoint (§5).
- Dependencies: T-38
- Files/Components: `runtime/startup.py`, `app.py`, `main.py`
- Serves: ADR-010; ARCH-001 §11.2, §16.3; FS-02 §5.9; MAC-3, MAC-4, MAC-10
- Expected Output: All four branches behave per §12.1; an activated Agent starts with the Backend unreachable and makes no HTTP call; exactly one Uvicorn worker; clean, idempotent shutdown; importing the module performs no I/O.
- Tests: Unit — §16.1 (startup decision, runtime worker count)
- Completion Evidence: Unit tests pass across all four branches; the Agent starts locally against an overridden root.
- Documentation: `agent/README.md` — startup behavior and the run command.
- Exclusions: **No HTTP endpoints of any kind** — the Agent's approved routes (ARCH-001 §14.2) all belong to excluded features, and no health route is approved (OI-3). No DeepStream supervision (OI-1).

**T-40 — Simulated-Backend integration suite and real-Backend contract suite**
- Objective: Build the simulated Backend of §16.2 and author both suites — the full simulated matrix, plus the §16.3 contract tests against the real IP-01 Backend with real SQL Server.
- Dependencies: T-39
- Files/Components: `agent/tests/integration/*`, `agent/tests/contract/*`
- Serves: FS-02 AC-3, AC-4, AC-7, AC-9, AC-13, AC-15, T-07–T-16, T-18; MAC-1–MAC-9
- Expected Output: The whole simulated matrix (§16.2) passes with no Jetson present; the contract suite (§16.3) passes against the real Backend build, unmodified, proving AC-13/T-18.
- Tests: This task is itself the tests.
- Completion Evidence: A green simulated run on a bare workstation, plus a green contract run against a live Backend + SQL Server. Contract tests skip cleanly (not fail) when no Backend is available, as IP-01's SQL-Server-dependent suites do.
- Documentation: `agent/README.md` — how to run each suite and what each requires.
- Exclusions: No Backend modification to make a test pass; no simulator-only endpoint, parameter, header, or auth bypass (FS-02 §1.2, §11); no re-test of Backend concurrency (§16.4).

**T-41 — Jetson deployment, systemd unit, and milestone documentation *(Jetson-dependent)***
- Objective: Author the systemd unit and the installation procedure of §17; execute the manual verification script (§21) end-to-end on the real Jetson; update documentation for the delivered milestone.
- Dependencies: T-40
- Files/Components: `agent/deploy/weapon-detection-agent.service`, `agent/README.md`, root `README.md`
- Serves: ARCH-001 §12.1; ADR-006; ARCH-CON-002; MAC-11, MAC-12
- Expected Output: The Agent installs on the Jetson, activates on first boot, restarts on failure, starts on boot, and runs as the unprivileged user under exactly one worker. systemd manages the Agent and nothing else.
- Tests: No new automated tests (the full suite is re-run on the Jetson where feasible).
- Completion Evidence: A recorded §21 run (steps 1–16) against a live Backend and the real Jetson, including verified file modes, the `DeviceId` matching the Dashboard, the offline-restart case, reactivation retaining the `DeviceId`, and a clean log inspection showing no secrets. Any step not reproducible on the available hardware is reported as a limitation, not silently omitted (IP-01 T-30 precedent).
- Documentation: `agent/README.md` (deployment); root `README.md` (project status, the delivered milestone, known limitations, next milestone) — Engineering Principle 10.
- Exclusions: **No DeepStream unit, service, or supervision** (ARCH-CON-002 — systemd manages only the Agent). No camera, model, or GPU configuration.

---

## 24. Traceability

### 24.1 Task → Acceptance Criteria

| Task | Serves |
|------|--------|
| T-31 | — (scaffolding) |
| T-32 | NFR-CFG-001 |
| T-33 | MAC-9; ARCH-001 §15.6 |
| T-34 | MAC-11; ADR-008 |
| T-35 | ADR-004; ARCH-001 §13.2 |
| T-36 | MAC-2, MAC-7; FS-02 AC-7 |
| T-37 | FS-02 AC-3, AC-15 |
| T-38 | MAC-1, MAC-5, MAC-7; FS-02 AC-3, AC-4, AC-7, AC-9, AC-12 |
| T-39 | MAC-3, MAC-4, MAC-10; ADR-010 |
| T-40 | MAC-1–MAC-9; FS-02 AC-13 |
| T-41 | MAC-11, MAC-12 |
| Review gates | MAC-13, MAC-14 |

### 24.2 Requirement/Decision → Plan Section

| Source | Realized In |
|--------|-------------|
| FR-BRN-003 (activate with a key, receive configuration) | §9.1, §11 — identity portion only; configuration deferred (OI-2) |
| FR-BRN-004, BR-003 (single-use key) | §9.1, §14 (never retry a rejection), §6.1 (key deleted after use) |
| FR-BRN-007 (persistent device identity) | §7, §10, §9.2 (mismatch guard) |
| FR-SYN-006 (config survives restart, applied on startup) | §7 (`ConfigCache` created), §12.1 (loaded) — population deferred (OI-2) |
| NFR-SEC-002 (Activation Key → Device ID + shared secret) | §9, §10, §11 |
| NFR-CFG-001 | §6 |
| NFR-TST-001 | §16 |
| ASM-006 (out-of-band key provisioning) | §6.1 (D-1), §21 |
| ADR-001 (FastAPI control plane) | §4, §5 |
| ADR-004 (SQLite local persistence) | §7 |
| ADR-006 / ARCH-CON-002 (systemd manages only the Agent) | §13, §17, T-41 |
| ADR-008 (filesystem layout) | §8 |
| ADR-009 (envelope) | §11 |
| ADR-010 (single Uvicorn worker) | §5, §17, MAC-10 |
| ADR-015 (Device ID retained, secret rotated) | §9.2, §10 |
| ARCH-001 §16.3 (offline startup after activation) | §12.2, MAC-4 |
| ARCH-001 §15.6 (no secrets in logs) | §15, MAC-9 |
| ARCH-001 §23 (retry/backoff shape) | §14 (D-5) |
| FS-02 §1.2, AC-13 (simulator/real parity) | §16.2, §16.3, T-40 |

### 24.3 Documents Deliberately Not Treated As Authoritative

- `specs/features/FS-02-branch-device-onboarding-activation.md` is **superseded** (its own header, and FS-02's `Supersedes` field). Nothing in this plan derives from it. The authoritative FS-02 is `specs/features/FS-02-branch-device-onboarding.md` (Final).
- Charter §4/§6/§12's "Flask-based Agent" wording is historical and corrected by ADR-001/CON-006. This plan builds on **FastAPI**.

---

*IP-02 — Jetson Agent Foundation. Status: Draft, awaiting approval. No implementation task in this plan has been started.*
