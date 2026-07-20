# Jetson Agent

The **control plane** for the Edge-Based Weapon Detection platform, running on the NVIDIA Jetson
Orin Nano as a FastAPI application (ARCH-001 §9, ADR-001). It is the Jetson-side counterpart to the
ASP.NET Core Backend.

## Status — IP-02 T-31–T-39

Delivered so far: the project scaffold (T-31), the **validated bootstrap-configuration foundation**
(T-32 — see [Configuration](#configuration)), the **structured logging foundation with secret
redaction** (T-33 — see [Logging](#logging)), the **filesystem-layout provisioning**
(T-34 — see [Filesystem layout](#filesystem-layout)), the **SQLite store foundation and schema**
(T-35 — see [Local database](#local-database-sqlite)), the **device-identity and config-cache
repositories** (T-36 — see [Persistence repositories](#persistence-repositories)), the
**Backend activation HTTP client** (T-37 — see [Backend activation client](#backend-activation-client)),
the **activation/reactivation orchestration service** (T-38 — see
[Activation service](#activation-service)), and the **FastAPI startup workflow and runtime**
(T-39 — see [Startup workflow and runtime](#startup-workflow-and-runtime)). The Agent now **runs its
activation in the FastAPI lifespan** on startup and publishes an initialized runtime — the activation
foundation is wired end-to-end at the Agent level.

Delivered by later IP-02 tasks, **not present now**:

- the simulated-Backend and real-Backend contract test suites (T-40);
- the systemd unit and Jetson deployment (T-41).

There is **no** DeepStream supervision, detection handling, alerting, heartbeat, health monitoring,
command API, or configuration polling here, and the Agent defines **no operational HTTP endpoint** (no
health/status route — OI-3). Startup activation is exercised only against a **fake** Backend client in
tests; verifying it against the real (and a simulated) Backend is **T-40**, and the Jetson/systemd
deployment is **T-41** — so end-to-end Jetson activation is **not** complete yet. The `ConfigCache`
repository remains **load-only** (its writer deferred, OI-2): startup loads it (normally empty) but
nothing populates or consumes it. See
[`specs/implementation-plans/IP-02-jetson-agent-foundation.md`](../specs/implementation-plans/IP-02-jetson-agent-foundation.md)
for the full plan.

## Python baseline

`requires-python = ">=3.10"`. The Jetson Orin Nano target (JetPack 6 / Ubuntu 22.04) ships Python
3.10; development and the tooling target that floor so nothing incompatible with the device is
introduced. Development on newer 3.x (e.g. 3.13) is supported.

## Project layout

```
agent/
├── pyproject.toml                       # metadata, dependencies, and tool config (single source of truth)
├── README.md
├── .gitignore
├── src/
│   └── weapon_detection_agent/
│       ├── __init__.py                  # package marker + __version__
│       ├── main.py                      # Uvicorn entry point: app = create_app(); run() (T-39)
│       ├── app.py                       # create_app() FastAPI factory + lifespan (T-39)
│       ├── config/
│       │   ├── __init__.py
│       │   ├── settings.py              # AgentSettings + load_settings() (T-32)
│       │   └── paths.py                 # filesystem layout resolution + provisioning (T-34)
│       ├── logging/                     # structured logging + redaction (T-33)
│       │   ├── __init__.py
│       │   ├── configuration.py         # configure_logging()
│       │   ├── formatter.py             # JSON log formatter
│       │   └── redaction.py             # central Redactor
│       ├── persistence/                 # SQLite store, schema (T-35) + repositories (T-36)
│       │   ├── __init__.py
│       │   ├── database.py              # connection, PRAGMAs, transaction helper, file mode
│       │   ├── schema.py                # version-1 tables + idempotent initializer
│       │   ├── models.py               # DeviceIdentity / CachedConfiguration records (T-36)
│       │   ├── errors.py               # typed repository errors (T-36)
│       │   ├── device_identity_repository.py   # load / store / replace secret (T-36)
│       │   └── config_cache_repository.py      # load-only (T-36; writer deferred, OI-2)
│       ├── activation/                  # Backend client (T-37) + orchestration service (T-38)
│       │   ├── __init__.py
│       │   ├── backend_client.py        # BackendActivationClient — one request, no retry (T-37)
│       │   ├── models.py               # ActivationResult (deviceId/sharedSecret/branchId) (T-37)
│       │   ├── errors.py               # typed, message-safe client + service errors (T-37/T-38)
│       │   ├── key_resolver.py          # ActivationKeyResolver — env-over-file key (T-38)
│       │   └── service.py               # ActivationService — first activation / reactivation (T-38)
│       └── runtime/                     # FastAPI startup workflow + published state (T-39)
│           ├── __init__.py
│           ├── startup.py               # create_lifespan + startup/shutdown orchestration
│           └── state.py                 # AgentRuntime + get_runtime (app.state.runtime)
└── tests/
    ├── test_app_startup.py              # scaffold smoke tests
    ├── test_settings.py                 # settings/configuration tests (T-32)
    ├── test_logging.py                  # logging + redaction tests (T-33)
    ├── test_paths.py                    # filesystem layout tests (T-34)
    ├── test_database.py                 # SQLite connection tests (T-35)
    ├── test_schema.py                   # schema + versioning tests (T-35)
    ├── test_device_identity_repository.py      # device identity repo tests (T-36)
    ├── test_config_cache_repository.py         # config cache load tests (T-36)
    ├── test_repository_integration.py          # cross-repository tests (T-36)
    ├── test_activation_client.py               # activation client tests (T-37)
    ├── test_activation_models.py               # activation result model tests (T-37)
    ├── test_activation_key_resolver.py         # key resolver tests (T-38)
    ├── test_activation_service.py              # activation service tests (T-38)
    ├── test_activation_service_integration.py  # activation lifecycle integration (T-38)
    ├── test_runtime_state.py                   # AgentRuntime state tests (T-39)
    ├── test_app_lifespan.py                    # app factory + lifespan tests (T-39)
    └── test_runtime_startup.py                 # startup workflow + lifecycle tests (T-39)
```

The `logging/` subpackage realizes IP-02 §4's single `logging/setup.py` sketch as a small package
(`configuration.py` + `formatter.py` + `redaction.py`) for a clear separation between wiring, format,
and the central redaction utility. It is named `logging` but never shadows the standard library —
all imports are absolute, so `import logging` anywhere resolves to the stdlib module while this
package is always addressed as `weapon_detection_agent.logging`.

The remaining module IP-02 §4 lays out (`runtime/`) is introduced by the task that implements it
(T-39) — not scaffolded ahead of use (Engineering Principle 9).

## Dependencies

- **Runtime:** `fastapi`, `uvicorn[standard]` (control-plane scaffold), `pydantic-settings` (the
  validated bootstrap-configuration model, T-32), and `httpx` (the Backend activation HTTP client,
  T-37). Logging (T-33) and SQLite persistence (T-35) use only the standard library (`logging`,
  `json`, `datetime`, `traceback`; `sqlite3`) — no ORM or migration framework (IP-02 D-6), and no
  retry/backoff library (activation is one-shot; see below).
- **Development:** `pytest`, `ruff` (lint + format), `mypy` (static type checking). `httpx` is a
  runtime dependency, so FastAPI's `TestClient` and the activation-client tests use that same httpx
  — it is not listed again under dev.

## Configuration

The Agent's bootstrap configuration (IP-02 §6) is read from `WDA_`-prefixed environment variables
into one **immutable, validated** settings object
(`weapon_detection_agent.config.settings.AgentSettings`). Loading is explicit — call
`load_settings()`; **merely importing the package or the FastAPI app reads no configuration and has
no side effects**, so a missing variable never breaks an import.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `WDA_BACKEND_BASE_URL` | **Yes** | — | The Backend's base URL. Must be a syntactically valid `http`/`https` URL **with a host**. Not contacted, and not path-normalized — only surrounding whitespace is trimmed. |
| `WDA_ACTIVATION_KEY` | No (env source) | — | The complete plaintext Activation Key, held as a redacted secret. Never logged or shown in a `repr`/error. IP-02 §6.1 also allows a key **file** with the environment taking precedence; that file source is deferred to a later task (it needs the resolved filesystem root and reads a file, whereas settings loading here does no filesystem I/O). |
| `WDA_ROOT_PATH` | No | `/opt/weapon-detection` | Agent filesystem root. Overridable only so tests/workstation runs avoid `/opt` (IP-02 §8.2). No directory is created here (T-34). |
| `WDA_HTTP_TIMEOUT_SECONDS` | No | `10` | Activation-request timeout; must be positive. |
| `WDA_LOG_LEVEL` | No | `INFO` | One of `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (case-insensitive). Consumed by the logging foundation (T-33). |

**Accepted URL schemes:** `http` and `https` only. Trusted-LAN HTTP is the prototype posture
(ADR-002/CON-005); HTTPS is accepted so a hardened deployment needs no change here.

**Configuration precedence** (pydantic-settings source order): explicit constructor arguments (used
by application code and tests) → `WDA_` environment variables → declared defaults for optional
settings. No `.env` file is read — provisioning is out-of-band (IP-02 §6, ASM-006).

**Missing or invalid configuration fails fast.** `load_settings()` raises a `ConfigurationError`
whose message names the offending `WDA_` variable and the rule it broke — and never the provided
value, so no Activation Key, URL, or other configured value can leak through an error.

Set the required variable:

- **Linux / macOS (bash):**

  ```bash
  export WDA_BACKEND_BASE_URL="http://localhost:5230"
  ```

- **Windows (PowerShell):**

  ```powershell
  $env:WDA_BACKEND_BASE_URL = "http://localhost:5230"
  ```

Run the settings tests:

```bash
python -m pytest tests/test_settings.py
```

> **Never commit** a `.env` file, an Activation Key, a device shared secret, or any real
> configuration value. See [`.gitignore`](.gitignore).

## Filesystem layout

The Agent keeps its on-disk state under a single root — `/opt/weapon-detection/` by default
(ADR-008), overridable via `WDA_ROOT_PATH` **solely** so tests and workstation runs avoid `/opt`
(IP-02 §8.2); the systemd deployment pins the real path (T-41). The layout is resolved and
provisioned by `weapon_detection_agent.config.paths`.

Only the directories this milestone actually uses are created, each with its ADR-008 mode:

| Directory | Mode | Purpose |
|-----------|------|---------|
| `<root>/` | `0750` | Root |
| `<root>/config/` | `0700` | Activation-key file (a later task; §6.1) |
| `<root>/database/` | `0700` | SQLite store `agent.db` (T-35) |
| `<root>/logs/` | `0750` | Agent log files (see [Logging](#logging)) |

- **Resolution is pure.** `resolve_paths(root)` returns an immutable `AgentPaths` whose members
  (`config_dir`, `database_dir`, `logs_dir`, and the resolved `database_file`, `activation_key_file`,
  and `log_file` paths) are computed without any filesystem access. **Creating** the layout is a
  separate, explicit step: `AgentPaths.provision()`.
- **Provisioning is idempotent and self-healing.** It is safe to run on every startup — existing
  directories are preserved, and their modes are re-applied so a drifted permission is corrected.
- **Only directories are created — never files.** The database file (T-35), activation-key file
  (§6.1), and log file are written by the tasks that own them.
- **Deferred directories are not created.** `snapshots/`, `recordings/`, `models/`, `pipeline/`, and
  `runtime/` are part of the approved ADR-008 layout but have no writer in this milestone, so they
  are not created here.
- **Ownership** (the unprivileged `weapon-detection` service user, IP-02 D-2) is assigned by the
  installer on the Jetson (T-41); this module sets permission *modes* only.
- **Windows:** POSIX modes are not representable, so mode enforcement is skipped there while
  directory creation still happens (IP-02 §17); the modes are exercised for real on Linux/Jetson.
  The mode-assertion tests skip on Windows accordingly.

Run the filesystem-layout tests:

```bash
python -m pytest tests/test_paths.py
```

## Local database (SQLite)

The Agent's local store is a single SQLite database at `<root>/database/agent.db` (ADR-004, IP-02
§7), holding **metadata only** — never binary images or video (BR-006). Its path is the resolved
`AgentPaths.database_file`; the persistence layer never hard-codes the production path. Managed by
`weapon_detection_agent.persistence` (`database.py` for connections, `schema.py` for the schema).

**Provisioning order.** The database directory must exist before the database is created — the
persistence layer never calls `mkdir`. The order a later startup task (T-39) will follow, and the
order tests follow, is:

```text
resolve_paths(root)  →  AgentPaths.provision()  →  initialize_database(paths.database_file)
```

Opening a connection whose parent `database/` directory is missing fails with a clear
`DatabaseInitializationError` rather than silently creating it.

**Schema (version 1, IP-02 §7).** Three tables, created together:

| Table | Purpose |
|-------|---------|
| `SchemaVersion` | Single integer `Version` guarding the schema version. |
| `DeviceIdentity` | The persistent Device ID and protected shared secret (one row, `SingletonGuard = 1`). |
| `ConfigCache` | The last synchronized configuration for offline startup (one row, `SingletonGuard = 1`). |

`DeviceIdentity` and `ConfigCache` each use `SingletonGuard INTEGER PRIMARY KEY CHECK (SingletonGuard
= 1)`, giving the "exactly one row" invariant a schema-level guarantee — a second row is rejected by
SQLite, not merely by application code. **No table stores binary media**, and creating the schema
seeds **no** rows: `DeviceIdentity` and `ConfigCache` start empty (writing them is T-36's work).

- **Idempotent initialization.** `initialize_database(path)` (or `initialize_schema(connection)`)
  creates the tables and records version 1 on a fresh database, and is a safe no-op on an
  already-current one — existing rows are preserved. Safe to run on every startup.
- **Transactional and atomic.** Schema creation runs inside one explicit transaction; a failure
  part-way rolls the whole thing back, so the database is never left with only some tables or a
  version row without its tables.
- **Version safety.** A database recording a **newer** version raises `UnsupportedSchemaVersionError`
  (the Agent never downgrades or modifies it); an impossible `SchemaVersion` state (no row, multiple
  rows, or a non-positive version) raises `InvalidSchemaStateError`. Neither error, and no log line,
  ever contains a stored row value or secret.
- **Connection lifecycle.** `connect(path)` returns a configured `sqlite3.Connection`
  (`row_factory = sqlite3.Row`, `PRAGMA foreign_keys = ON`, manual transaction control); the caller
  closes it. `open_connection(path)` is a context manager that closes predictably on exit. There is
  **no** module-level/global connection, and **importing the persistence modules performs no I/O** —
  no file, database, or socket is touched until an explicit call.
- **File permissions.** On POSIX the database file is set to mode `0600` (owner read/write only)
  after creation and reapplied on each connect (IP-02 §7, ARCH-001 §13.3); ownership is left to the
  installer (T-41). On Windows, POSIX modes are not representable, so the mode step is skipped and
  the mode-assertion tests skip accordingly (IP-02 §17).

Run the schema/connection tests:

```bash
python -m pytest tests/test_database.py tests/test_schema.py
```

## Persistence repositories

Two concrete repositories read and write the singleton records, in
`weapon_detection_agent.persistence`. Each is constructed with the **explicit database path**
(`AgentPaths.database_file`) — they never resolve `WDA_ROOT_PATH`, create directories, initialize the
schema, retain a global connection, or perform any import-time I/O. The caller resolves paths (T-34),
provisions, initializes the schema (T-35), and then constructs the repository:

```text
AgentSettings → AgentPaths → AgentPaths.database_file → initialize schema → repositories
```

Records are immutable dataclasses (`persistence.models`). The device shared secret is a `SecretStr`,
so it is redacted in every `repr`/`str`, log line, and error; timestamps are timezone-aware UTC
`datetime`s serialized as ISO-8601 UTC. "Protected" on the Jetson means **file-permission-protected**
(the `0600` database, D-4) — there is **no application-layer encryption**, and the code claims none.

### `DeviceIdentityRepository`

- **`load()`** → the stored `DeviceIdentity`, or **`None` when the Agent has not activated** (no row).
- **`store(identity)`** — persists the identity from a **first activation**, in one transaction.
  A second store is **rejected** with `IdentityAlreadyExistsError`: the repository never overwrites a
  stored identity, so the **Device ID is permanent** (it cannot be changed or cleared here).
- **`replace_shared_secret(shared_secret=…, last_activated_at=…)`** — the reactivation write. It
  atomically updates **only** `ProtectedSharedSecret` and `LastActivatedAt`, **retaining the Device
  ID and the original `ActivatedAt`** (§10, ADR-015). A failure mid-write rolls back, leaving the
  previously committed secret intact (never torn or empty); replacing when no identity exists raises
  `InvalidIdentityStateError`.

Deciding **whether** to (re)activate, and what to do if the Backend returns a *different* Device ID,
is **not** this layer's job — that policy belongs to the activation orchestration (T-38).

### `ConfigCacheRepository`

- **`load()`** → the stored `CachedConfiguration` (raw `ConfigJson` text + UTC `updated_at`), or
  **`None` when no cache exists** (the normal state in this milestone).

This repository is **load-only by design**: IP-02 defers the `ConfigCache` **writer** (OI-2) — this
milestone has no configuration source to populate it, and nothing consumes the cache at runtime yet.
There is no replace, clear, refresh, or polling operation. `ConfigJson` is returned as raw text (no
configuration schema exists yet, OI-2, so it is not parsed) and is never logged or placed in an error.

Both repositories use the T-35 `transaction()` helper for writes and a fresh short-lived connection
per operation. Errors carry only safe structural facts — never a secret, configuration payload, SQL
parameter, or full row.

Run the repository tests:

```bash
python -m pytest tests/test_device_identity_repository.py \
  tests/test_config_cache_repository.py tests/test_repository_integration.py
```

## Backend activation client

`weapon_detection_agent.activation.BackendActivationClient` calls the Backend's device-activation
endpoint and returns a typed result. That is its **entire** responsibility:

```text
Activation Key → one POST /api/v1/activate → validate the response → ActivationResult (or a typed error)
```

**Endpoint / contract** (verified against the delivered Backend, unchanged by T-37):

- **`POST /api/v1/activate`**, no `Authorization` header (the endpoint is `[AllowAnonymous]` — the
  Activation Key is itself the credential).
- Request body: `{"activationKey": "<keyId.secret>"}` (`application/json`, camelCase). The key travels
  **only** in the body — never the URL, a query string, or a header.
- Success `200`: envelope `{"success": true, "data": {"deviceId", "sharedSecret", "branchId"}}`. The
  response carries **no configuration** (OI-2) — the client neither expects nor invents any.
- Rejection `401`: `{"success": false, "errorCode": "INVALID_ACTIVATION_KEY"}` — the same body for
  every reason (malformed / unknown / wrong secret / consumed / invalidated).

**Base URL and timeout are passed in explicitly** — the caller (the T-38/T-39 layer) supplies
`settings.backend_base_url` and `settings.http_timeout_seconds`. The client never reads settings, the
environment, or a key file. The URL join preserves a base-path prefix and tolerates trailing slashes.

**Typed result.** `ActivationResult(device_id, shared_secret, branch_id)` — an immutable record. The
shared secret is a `SecretStr`, redacted in every `repr`/`str` and by the logger; it cannot be leaked
through a generic `asdict`/JSON dump.

**Exactly one request, no automatic retry.** `POST /api/v1/activate` consumes a one-time key and is
**not idempotent**: a timeout or transport failure may occur *after* the Backend commits activation
but *before* the Agent sees the response, so retrying the same key could return
`INVALID_ACTIVATION_KEY` and hide that the original attempt succeeded. Automatic retries for this
endpoint are therefore **prohibited** (IP-02 §14, amended). Each outcome maps to a distinct,
message-safe error — `ActivationTimeoutError`, `ActivationTransportError`, `ActivationRejectedError`
(status + error code), `ActivationServerError` (5xx / unexpected status), or
`InvalidActivationResponseError` (malformed body). No message ever contains the key, the shared
secret, or a raw request/response body. Surfacing an ambiguous timeout outcome — and requiring a
**new** Activation Key or an approved recovery workflow — is the activation orchestration's job
(**T-38**), not the client's.

**What T-37 does not do:** it does not decide whether activation is required, read the key from
env/file, persist Device Identity, replace a secret, compare Device IDs, write the `ConfigCache`, or
wire into startup. Those are **T-38** (activation/reactivation policy) and **T-39** (runtime
startup). The client is async (`httpx.AsyncClient`) to fit the future lifespan; an injected client is
caller-owned (not closed), an owned client is closed by `aclose()` or `async with`. Importing the
activation modules performs no I/O and constructs no HTTP client. **Activation is not end-to-end
functional yet, and T-37 saves no Device credentials.**

Run the activation-client tests:

```bash
python -m pytest tests/test_activation_client.py tests/test_activation_models.py
```

## Activation service

`weapon_detection_agent.activation.ActivationService` orchestrates the whole activation decision —
resolving the Activation Key, calling the client **once**, persisting via the Device Identity
repository, and cleaning up a file key. It is the piece a later startup task (**T-39**) will invoke;
**`main.py` does not run it, so the Agent does not activate on boot yet.** Dependencies are injected
(the client, the repository, a key resolver, and a clock) — the service constructs nothing globally,
reads no settings or environment, initializes no schema, provisions no directory, and writes no
`ConfigCache`.

### Activation Key resolution (`ActivationKeyResolver`)

The key comes from one of two approved sources, in precedence order (IP-02 §6.1):

1. the `WDA_ACTIVATION_KEY` environment variable — already validated into `AgentSettings` and passed
   in as a `SecretStr`. **It wins, and the key file is not read.**
2. a single-line file at `<root>/config/activation-key` (`AgentPaths.activation_key_file`), read
   only through an explicit call.

Surrounding whitespace (including a normal trailing newline) is stripped; an empty or whitespace-only
file is rejected (`ActivationKeyFileError`). The key is always a `SecretStr`, and the resolver records
whether it came from the environment or the file — only a **file** key is ever deleted after success.

### `ActivationService.activate()`

Runs the startup decision (IP-02 §12.1) once and returns a safe, immutable `ActivationServiceResult`
(`outcome`, `device_id`, timestamps, and `branch_id` when the Backend returned one — **never** the
shared secret):

- **First activation** (no stored identity + a key): call the Backend once; on success store a new
  `DeviceIdentity` with the returned `deviceId`/`sharedSecret` and one injected UTC timestamp for both
  `ActivatedAt` and `LastActivatedAt`; then delete a file-sourced key. No key at all →
  `ActivationKeyMissingError` (no Backend call). The `branchId` is returned in the result only — the
  schema stores no branch id.
- **Already activated / no key** (stored identity + no key): a **no-op** — no Backend call, no SQLite
  change, timestamps untouched. This is the ordinary restart path.
- **Reactivation** (stored identity + a key): call the Backend once; if the returned `deviceId`
  **differs** from the stored one, raise `DeviceIdentityMismatchError` and change nothing (the Device
  ID is permanent, OI-4); otherwise atomically replace the shared secret, **retaining the Device ID
  and the original `ActivatedAt`** and advancing `LastActivatedAt` to the clock value; then delete a
  file-sourced key.

**Environment keys never delete the file.** A file key is deleted **only after** the complete local
operation succeeds (Backend success **and** committed persistence). It is never deleted after a key
read failure, `401`, timeout, transport failure, `5xx`, malformed response, Device-ID mismatch, or a
persistence failure.

**One request, no retry, ambiguous timeout.** The service makes exactly one Backend request and never
retries (the one-time key is not idempotent; IP-02 §14). A **timeout** becomes
`ActivationOutcomeAmbiguousError` — the Backend may have activated the device, but no reliable result
arrived, so the local state is left unchanged and a **new key / operator recovery** is required.
Transport, `5xx`, `401`, and malformed responses propagate the client's typed errors, leaving the
database and key file unchanged.

**Backend success then local failure.** If the Backend succeeds but storing (first activation) or
replacing the secret (reactivation) fails, the service raises `ActivationPersistenceError` — it does
**not** retry the Backend, does **not** delete the key file, and the prior local state is preserved by
the repository transaction. If persistence commits but removing the file key then fails, the service
raises `ActivationKeyCleanupError` **without** rolling back the committed identity (only the leftover
file needs operator attention). No error, and no log line, ever contains the key, the shared secret,
or configuration content.

Run the activation-service tests:

```bash
python -m pytest tests/test_activation_key_resolver.py tests/test_activation_service.py \
  tests/test_activation_service_integration.py
```

## Startup workflow and runtime

The application is built by `weapon_detection_agent.app.create_app()` and served as
`weapon_detection_agent.main:app`. All of the Agent's real work runs inside the **FastAPI lifespan**
on startup — **not** at import: importing `main` builds only the `FastAPI` object (no settings, no
directories, no database, no logging handlers, no HTTP client, no activation). `create_app()` takes
injectable dependencies (a settings loader, a clock, and a Backend client factory) so tests exercise
startup without a real Backend, network, or `/opt`; production uses the real defaults.

### Startup order (IP-02 §12.1)

```text
load settings → resolve paths → provision layout → configure logging → initialize SQLite schema
  → construct repositories → construct the Backend client → construct the key resolver + service
  → run activation (first / reactivate / no-op) → load ConfigCache (may be empty, OI-2)
  → publish app.state.runtime → serve
```

Logging is configured **after** the layout is provisioned (so the `logs/` directory exists for the
`<root>/logs/agent.log` file, alongside stdout), with the environment Activation Key registered as a
redacted literal. The activation call runs **exactly once** and is never retried (IP-02 §14).

### The four startup branches

- **First activation** (no stored identity + a key): the Backend is called once, the identity is
  persisted, and a file-sourced key is removed — then startup continues.
- **Reactivation** (a stored identity + a key): the Backend is called once; the returned Device ID
  must match the stored one; the secret is replaced (keeping the Device ID and original
  `ActivatedAt`, advancing `LastActivatedAt`); a file key is removed — then startup continues.
- **Already activated / no key** (a stored identity + no key): a **no-op** — no Backend request, no
  change. This is the ordinary restart, and it starts successfully even with the Backend unreachable
  (offline startup).
- **Unactivated with no key**: startup **fails** clearly (`ActivationKeyMissingError`) — the Agent
  does not enter the serving state.

Any activation failure (401, timeout → ambiguous, transport, 5xx, malformed response, Device-ID
mismatch, persistence failure, or key-cleanup failure) **fails startup**: the app does not serve, the
owned Backend client is closed, and `app.state.runtime` is **not** published. Nothing is retried, and
local state / the key file are preserved per the [Activation service](#activation-service) contract.

### Runtime state

On success the lifespan publishes a small, immutable `AgentRuntime` on **`app.state.runtime`** (read
it with `weapon_detection_agent.runtime.get_runtime(app)`), holding the validated settings, resolved
paths, both repositories, and the activation result. It carries **no** secret — the Activation Key
lives only inside the settings' `SecretStr`, and the result holds no shared secret — so it cannot leak
one through a `repr`. It is **not** exposed through any HTTP endpoint. Before startup and after
shutdown, `get_runtime(app)` returns `None`.

### Shutdown

The lifespan shutdown closes the owned Backend HTTP client and clears the runtime reference. It is
idempotent, makes **no** Backend call, and deletes **nothing** — not the Device Identity, the
ConfigCache, the database, the key file, the logs, or any directory.

### One worker; no endpoints

The Agent must run under **exactly one** Uvicorn worker (ADR-010) — process-local singleton state and
later DeepStream supervision require a single process. `main.run()` pins `workers=1`, and the
documented command uses `--workers 1`; systemd enforcement is T-41. The app defines **no** operational
routes (no `/health`, `/status`, `/ready`, `/live`, `/activate` — no Agent health endpoint is approved,
OI-3); only FastAPI's built-in `/docs`/`/openapi.json` remain.

### Not here yet

Startup activation is verified only against a **fake** Backend client in tests. Verifying it against
the real (and a simulated) Backend is **T-40**; the systemd unit and Jetson deployment are **T-41**.
There is no DeepStream, detection, alert, heartbeat, health-monitoring, or command handling, and the
`ConfigCache` is loaded at startup but **never populated** (OI-2).

Run the runtime tests:

```bash
python -m pytest tests/test_runtime_state.py tests/test_app_lifespan.py tests/test_runtime_startup.py
```

## Logging

The Agent logs as **newline-delimited JSON** (IP-02 §15) — one object per line, suitable for the
systemd journal and for log ingestion. Configuration is explicit: call
`weapon_detection_agent.logging.configuration.configure_logging(...)`. **Importing the package or
the FastAPI app configures no logging and creates no file** — a missing environment variable never
breaks an import (this is wired into runtime startup later, by T-39).

Each record carries at least `timestamp`, `level`, `logger`, and `message`, plus any structured
`extra` fields you attach (`event`, `component`, `operation`, `device_id`, `branch_id`,
`camera_id`, `context`, …) and, on error, a safe `exception` object (type, redacted message, redacted
traceback). **Timestamps are UTC, ISO-8601** with a `+00:00` offset.

- **Destinations:** stdout always; an **explicit** log-file path optionally. Both carry the same
  structured records.
- **Level:** taken from `WDA_LOG_LEVEL` (see [Configuration](#configuration)) — the same validated
  set of level names, with no second, conflicting definition.
- **Log-directory ownership:** this task writes to a log file whose parent directory **already
  exists**; it never creates the directory or the `/opt/weapon-detection/` layout. Provisioning that
  layout is **T-34's** responsibility. A file path whose parent is missing raises a clear
  `LoggingConfigurationError` rather than creating the directory or falling back elsewhere.
- **No rotation/retention** policy is committed in this task (IP-02 §24 defers it); a plain file
  handler is used.

### Redaction (never log a secret)

A **central redactor** scrubs every record — message, structured fields, and exception text alike —
so the Activation Key, device shared secret, and other credentials cannot reach a log at any level,
including `DEBUG` (IP-02 §15, ARCH-001 §15.6):

- **Sensitive field names** (case- and separator-insensitive) have their whole value replaced with
  `[REDACTED]` — e.g. `activation_key`, `shared_secret`, `authorization`, `password`, `secret`,
  `token`, `access_token`, `refresh_token`, `jwt`, `api_key`, `credential`, `cookie`. Harmless
  metadata that merely *contains* such a word (e.g. `secret_status`, `token_count`,
  `credential_identifier`) is **not** redacted.
- **Sensitive values** are removed wherever they appear: any secret literal registered via
  `sensitive_values=[...]`, any `Bearer <token>` span in a string, and any pydantic `SecretStr`.
- Redaction is recursive (dicts, lists, tuples, sets), **never mutates** the caller's objects, and
  non-serializable values are coerced to a safe redacted string rather than crashing logging.

Run the logging tests:

```bash
python -m pytest tests/test_logging.py
```

## Developer commands

All commands are run from this `agent/` directory.

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate it

**Linux / macOS (bash):**

```bash
source .venv/bin/activate
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install the package with its development tooling

Editable install so source edits take effect without reinstalling; `[dev]` adds the test/lint/type
tooling:

```bash
pip install -e ".[dev]"
```

### 4. Run the Agent locally

Running the Agent **activates it** in the startup lifespan (see
[Startup workflow and runtime](#startup-workflow-and-runtime)), so it needs its settings configured
first — at minimum `WDA_BACKEND_BASE_URL`, plus an Activation Key (`WDA_ACTIVATION_KEY` or the
key file) the first time. A **single** Uvicorn worker is used deliberately — Agent state and later
process supervision are process-local (ADR-010):

```bash
uvicorn weapon_detection_agent.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Equivalently, `python -m weapon_detection_agent.main` (or `weapon_detection_agent.main:run()`) runs
the app pinned to one worker. Do **not** use `--reload` or `--workers N > 1`. The application exposes
**no operational endpoints** — only FastAPI's built-in `/docs` and `/openapi.json`.

### 5. Run the tests

```bash
python -m pytest
```

### 6. Lint

```bash
python -m ruff check .
```

### 7. Check formatting

```bash
python -m ruff format --check .
```

### 8. Static type checking

```bash
python -m mypy src
```

### Import smoke check

```bash
python -c "from weapon_detection_agent.main import app; print(app.title)"
```

## Security

No secret, Activation Key, device shared secret, `.env` file, local database, or virtual
environment is ever committed — see [`.gitignore`](.gitignore). The Activation Key and Backend URL
are provisioned out-of-band at deployment time (IP-02 §6, §15) and never enter the repository.
