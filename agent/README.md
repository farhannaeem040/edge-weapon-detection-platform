# Jetson Agent

The **control plane** for the Edge-Based Weapon Detection platform, running on the NVIDIA Jetson
Orin Nano as a FastAPI application (ARCH-001 §9, ADR-001). It is the Jetson-side counterpart to the
ASP.NET Core Backend.

## Status — IP-02 T-31–T-36

Delivered so far: the project scaffold (T-31 — package metadata, tooling, a minimal importable
FastAPI application with no endpoints), the **validated bootstrap-configuration foundation**
(T-32 — see [Configuration](#configuration)), the **structured logging foundation with secret
redaction** (T-33 — see [Logging](#logging)), the **filesystem-layout provisioning**
(T-34 — see [Filesystem layout](#filesystem-layout)), the **SQLite store foundation and schema**
(T-35 — see [Local database](#local-database-sqlite)), and the **device-identity and config-cache
repositories** (T-36 — see [Persistence repositories](#persistence-repositories)). None of the
Agent's operational behaviour exists yet — the repositories can persist and read the local records,
but nothing activates, contacts the Backend, or consumes them at runtime.

Delivered by later IP-02 tasks, **not present now**:

- the `POST /api/v1/activate` Backend client (T-37);
- the activation/reactivation workflow and Device-ID mismatch policy (T-38);
- the startup lifespan and single-worker Uvicorn runtime that wires the repositories in (T-39);
- the simulated-Backend and real-Backend test suites (T-40);
- the systemd unit and Jetson deployment (T-41).

There is **no** activation, Backend communication, DeepStream supervision, heartbeat, or command API
here. The `ConfigCache` repository is **load-only** — its writer is deferred (OI-2), and nothing
populates or consumes the cache at runtime. See
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
│       ├── main.py                      # minimal FastAPI `app` (no endpoints yet)
│       ├── config/
│       │   ├── __init__.py
│       │   ├── settings.py              # AgentSettings + load_settings() (T-32)
│       │   └── paths.py                 # filesystem layout resolution + provisioning (T-34)
│       ├── logging/                     # structured logging + redaction (T-33)
│       │   ├── __init__.py
│       │   ├── configuration.py         # configure_logging()
│       │   ├── formatter.py             # JSON log formatter
│       │   └── redaction.py             # central Redactor
│       └── persistence/                 # SQLite store, schema (T-35) + repositories (T-36)
│           ├── __init__.py
│           ├── database.py              # connection, PRAGMAs, transaction helper, file mode
│           ├── schema.py                # version-1 tables + idempotent initializer
│           ├── models.py               # DeviceIdentity / CachedConfiguration records (T-36)
│           ├── errors.py               # typed repository errors (T-36)
│           ├── device_identity_repository.py   # load / store / replace secret (T-36)
│           └── config_cache_repository.py      # load-only (T-36; writer deferred, OI-2)
└── tests/
    ├── test_app_startup.py              # scaffold smoke tests
    ├── test_settings.py                 # settings/configuration tests (T-32)
    ├── test_logging.py                  # logging + redaction tests (T-33)
    ├── test_paths.py                    # filesystem layout tests (T-34)
    ├── test_database.py                 # SQLite connection tests (T-35)
    ├── test_schema.py                   # schema + versioning tests (T-35)
    ├── test_device_identity_repository.py      # device identity repo tests (T-36)
    ├── test_config_cache_repository.py         # config cache load tests (T-36)
    └── test_repository_integration.py          # cross-repository tests (T-36)
```

The `logging/` subpackage realizes IP-02 §4's single `logging/setup.py` sketch as a small package
(`configuration.py` + `formatter.py` + `redaction.py`) for a clear separation between wiring, format,
and the central redaction utility. It is named `logging` but never shadows the standard library —
all imports are absolute, so `import logging` anywhere resolves to the stdlib module while this
package is always addressed as `weapon_detection_agent.logging`.

The rest of the module structure IP-02 §4 lays out (`activation/`, `runtime/`) is introduced by the
tasks that implement each part — not scaffolded ahead of use (Engineering Principle 9). The
`persistence/` package created here holds connection and schema management only; its
device-identity/config-cache **repositories** arrive with T-36.

## Dependencies

- **Runtime:** `fastapi`, `uvicorn[standard]` (control-plane scaffold), `pydantic-settings` (the
  validated bootstrap-configuration model, T-32). Logging (T-33) and SQLite persistence (T-35) use
  only the standard library (`logging`, `json`, `datetime`, `traceback`; `sqlite3`) — no new
  dependency. In particular, no ORM or migration framework is used (IP-02 D-6).
- **Development:** `pytest`, `httpx` (FastAPI `TestClient` HTTP/API testing), `ruff` (lint +
  format), `mypy` (static type checking).

The Agent's own runtime HTTP client for calling the Backend is added by T-37, not here.

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

The application currently exposes no endpoints, so this serves an empty app (plus FastAPI's built-in
`/docs`). A single Uvicorn worker is used deliberately — later Agent state and process supervision
require a single process (ADR-010):

```bash
uvicorn weapon_detection_agent.main:app --workers 1
```

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
