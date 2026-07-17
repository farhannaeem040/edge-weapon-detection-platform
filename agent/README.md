# Jetson Agent

The **control plane** for the Edge-Based Weapon Detection platform, running on the NVIDIA Jetson
Orin Nano as a FastAPI application (ARCH-001 §9, ADR-001). It is the Jetson-side counterpart to the
ASP.NET Core Backend.

## Status — IP-02 T-31 (scaffolding only)

This directory currently contains **only the project scaffold**: package metadata, development
tooling, and a minimal importable FastAPI application with no endpoints. None of the Agent's
behaviour exists yet.

Delivered by later IP-02 tasks, **not present now**:

- settings/configuration loading (T-32);
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
│       └── main.py                      # minimal FastAPI `app` (no endpoints yet)
└── tests/
    └── test_app_startup.py              # scaffold smoke tests
```

The broader module structure IP-02 §4 lays out (`config/`, `persistence/`, `activation/`,
`runtime/`, `logging/`) is introduced by the tasks that implement each part — not scaffolded ahead
of use (Engineering Principle 9).

## Dependencies

- **Runtime:** `fastapi`, `uvicorn[standard]` — only what the minimal control-plane scaffold needs.
- **Development:** `pytest`, `httpx` (FastAPI `TestClient` HTTP/API testing), `ruff` (lint +
  format), `mypy` (static type checking).

The Agent's own runtime HTTP client for calling the Backend is added by T-37, not here.

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
