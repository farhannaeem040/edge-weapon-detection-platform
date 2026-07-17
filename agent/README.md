# Jetson Agent

The **control plane** for the Edge-Based Weapon Detection platform, running on the NVIDIA Jetson
Orin Nano as a FastAPI application (ARCH-001 §9, ADR-001). It is the Jetson-side counterpart to the
ASP.NET Core Backend.

## Status — IP-02 T-31–T-32

Delivered so far: the project scaffold (T-31 — package metadata, tooling, a minimal importable
FastAPI application with no endpoints) and the **validated bootstrap-configuration foundation**
(T-32 — see [Configuration](#configuration)). None of the Agent's operational behaviour exists yet.

Delivered by later IP-02 tasks, **not present now**:

- logging with secret redaction (T-33);
- the `/opt/weapon-detection/` filesystem layout (T-34);
- the local SQLite store and device-identity/config-cache persistence (T-35, T-36);
- the `POST /api/v1/activate` Backend client and activation/reactivation workflow (T-37, T-38);
- the startup lifespan and single-worker Uvicorn runtime (T-39);
- the simulated-Backend and real-Backend test suites (T-40);
- the systemd unit and Jetson deployment (T-41).

There is **no** activation, device identity, shared-secret handling, SQLite, DeepStream supervision,
heartbeat, or command API here. See
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
│       └── config/
│           ├── __init__.py
│           └── settings.py              # AgentSettings + load_settings() (T-32)
└── tests/
    ├── test_app_startup.py              # scaffold smoke tests
    └── test_settings.py                 # settings/configuration tests (T-32)
```

The rest of the module structure IP-02 §4 lays out (`config/paths.py`, `persistence/`, `activation/`,
`runtime/`, `logging/`) is introduced by the tasks that implement each part — not scaffolded ahead
of use (Engineering Principle 9).

## Dependencies

- **Runtime:** `fastapi`, `uvicorn[standard]` (control-plane scaffold), `pydantic-settings` (the
  validated bootstrap-configuration model, T-32).
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
