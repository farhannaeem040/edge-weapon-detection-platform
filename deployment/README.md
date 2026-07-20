# Running the central platform

The central platform — the Angular dashboard, the ASP.NET Core Backend, SQL Server, and the EF Core
migrations — runs as a single Docker Compose stack.

**Docker Desktop is the only prerequisite.** You do not need Node.js, the Angular CLI, the .NET SDK,
SQL Server, or the EF Core tools installed locally.

> The Jetson Agent is **not** part of this stack. It runs natively on the Jetson under systemd,
> because it needs NVIDIA, DeepStream, camera, and subprocess access. See [Connecting a Jetson
> Agent](#connecting-a-jetson-agent).

---

## Start

```powershell
powershell -ExecutionPolicy Bypass -File deployment/start.ps1
```

The first run downloads images and builds the applications, which takes several minutes. Later runs
start in seconds.

Equivalently, once a `.env` exists:

```bash
docker compose up --build -d
```

## Open

<http://localhost:8080>

Sign in with the `ADMIN_IDENTIFIER` and `ADMIN_PASSWORD` values from your `.env` file.

## Status

```bash
docker compose ps
```

## Logs

```powershell
powershell -ExecutionPolicy Bypass -File deployment/logs.ps1
powershell -ExecutionPolicy Bypass -File deployment/logs.ps1 -Service backend
```

## Stop without deleting data

```powershell
powershell -ExecutionPolicy Bypass -File deployment/stop.ps1
```

## Reset and delete data

```powershell
powershell -ExecutionPolicy Bypass -File deployment/reset.ps1
```

Destructive — requires typing `DELETE` to confirm.

---

## Prerequisites

- **Docker Desktop** — <https://www.docker.com/products/docker-desktop/>
- **Linux containers mode.** This stack uses Linux images (SQL Server, .NET, Nginx). Docker Desktop
  defaults to Linux containers; if you previously switched to Windows containers, switch back via
  the Docker Desktop tray icon → *Switch to Linux containers*.
- Docker Desktop must be **running** before you start. `start.ps1` checks this and tells you if the
  daemon is not up.

## Configuration and `.env`

All configuration lives in a single `.env` file at the repository root. It is **git-ignored** and
must never be committed. `.env.example` is the committed template and contains placeholders only.

`start.ps1` creates `.env` on first run and **never overwrites an existing one** — the credentials in
it are tied to the data already in the database volume, so replacing them would lock the stack out
of its own database.

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_PORT` | `8080` | Host port for the single public origin |
| `SQL_DATABASE` | `WeaponDetection` | Database created by the migration job |
| `SQL_SERVER_PASSWORD` | *generated* | SQL Server `sa` password |
| `JWT_SIGNING_KEY` | *generated* | Signs Admin session tokens (must be ≥ 32 bytes) |
| `ADMIN_IDENTIFIER` | `admin` | Bootstrap Admin sign-in name |
| `ADMIN_PASSWORD` | *generated* | Bootstrap Admin password |

### How secrets are generated

`start.ps1` generates each value with a cryptographically secure random number generator, from an
alphabet deliberately chosen to exclude characters that would corrupt a `.env` file (`#`), Compose
interpolation (`$`, `{`, `}`), or a SQL connection string (`;`, `=`, quotes). The SQL and Admin
passwords are regenerated until they satisfy SQL Server's complexity rules (upper, lower, digit, and
non-alphanumeric).

**Generated values are never printed** — not by the scripts, not in logs. Read them from `.env` when
you need to sign in.

### Choosing another port

Edit `APP_PORT` in `.env`, then restart:

```powershell
powershell -ExecutionPolicy Bypass -File deployment/stop.ps1
powershell -ExecutionPolicy Bypass -File deployment/start.ps1
```

---

## Architecture

```
                    host :${APP_PORT}
                          │
                  ┌───────▼────────┐
                  │    frontend    │   Nginx
                  │  Angular SPA   │
                  └───┬────────┬───┘
             /        │        │   /api/
        (SPA files)   │        │
                      │   ┌────▼─────┐
                      │   │ backend  │   ASP.NET Core :8080
                      │   └────┬─────┘
                      │        │
                      │   ┌────▼──────┐
                      │   │ sqlserver │   :1433  (internal only)
                      │   └────┬──────┘
                      │        │
                      │   ┌────▼───────┐
                      │   │ migrations │   one-shot, exits 0
                      └───┴────────────┘
```

Everything runs on an internal Compose network. **Only the frontend publishes a host port.** SQL
Server and the Backend are unreachable from the host or the LAN except through the Nginx proxy.

### Startup order

Compose enforces this with health- and completion-based conditions, not sleeps:

1. **sqlserver** becomes healthy — proven by a real `SELECT 1` query through `sqlcmd`, not merely by
   the process existing.
2. **migrations** runs `dotnet ef database update` and exits 0. The Backend does not migrate on
   startup, and its Admin bootstrap needs an existing schema, so this must finish first.
3. **backend** starts and becomes healthy via the existing `GET /api/v1/health` route.
4. **frontend** starts and becomes healthy.

A migration failure exits non-zero and stops the Backend from ever starting.

### Nginx routing

| Request | Handled as |
|---------|-----------|
| `/api/...` | Reverse-proxied to `http://backend:8080`, **path preserved** |
| `/branches`, `/login`, any non-file path | Angular SPA fallback to `index.html` |
| `*.js`, `*.css`, images | Served from disk, cached hard (build output is hashed) |

The `proxy_pass` directive intentionally has no URI path component, which is the form that forwards
the original request URI unchanged — so `/api/v1/activate` reaches the Backend as `/api/v1/activate`
rather than having `/api` stripped.

Because the SPA and the API share **one origin**, the browser only ever makes same-origin requests
and no CORS policy is required (the Backend configures none, and with this design it does not need
to).

### Persistent data

SQL Server data lives in the named volume `sqlserver-data`.

- `stop.ps1` / `docker compose down` — containers removed, **data kept**.
- `reset.ps1` / `docker compose down -v` — **data destroyed**.

---

## Connecting a Jetson Agent

The Agent is not containerized. Point it at the central server's single public origin:

```bash
WDA_BACKEND_BASE_URL=http://<server-ip>:8080
```

Replace `<server-ip>` with the LAN IP of the machine running this stack. Verify from the Jetson:

```bash
curl http://<server-ip>:8080/api/v1/health
```

Activation then flows through the same Nginx proxy:

```
POST http://<server-ip>:8080/api/v1/activate
```

Do **not** use `http://backend:8080` outside Docker — `backend` is an internal Compose service name
and resolves only inside the stack's network.

Native Jetson installation (systemd unit, service user, install procedure) is **T-41**.

---

## Troubleshooting

### Docker Desktop is not running

`start.ps1` fails with a clear message. Start Docker Desktop, wait until it reports *Engine running*,
and run the script again.

### Host port already in use

`docker compose up` reports a bind failure on the port. Either free it, or set a different
`APP_PORT` in `.env` and restart. To find the occupant on Windows:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Get-Process -Id $_.OwningProcess }
```

Note that a locally installed SQL Server commonly holds port 1433 — that is unrelated to this stack,
which does not publish 1433 at all.

### SQL Server unhealthy

```powershell
powershell -ExecutionPolicy Bypass -File deployment/logs.ps1 -Service sqlserver
```

Common causes:

- **Password rejected.** `SQL_SERVER_PASSWORD` must satisfy SQL Server complexity rules. If you
  hand-edited `.env`, check it.
- **Changed password against an existing volume.** The `sa` password is set when the volume is first
  initialised; editing it afterwards does not change the stored password. Either restore the
  original value or run `reset.ps1`.
- **Insufficient memory.** SQL Server needs ~2 GB. Raise Docker Desktop's memory limit in
  *Settings → Resources*.

### Migrations failed

```powershell
powershell -ExecutionPolicy Bypass -File deployment/logs.ps1 -Service migrations
```

The Backend will not start until migrations succeed — this is deliberate. Re-run after fixing:

```bash
docker compose up -d
```

### Rebuilding after code changes

```bash
docker compose up --build -d
```

To force a completely fresh build:

```bash
docker compose build --no-cache
```

### Clean slate

```powershell
powershell -ExecutionPolicy Bypass -File deployment/reset.ps1
powershell -ExecutionPolicy Bypass -File deployment/start.ps1
```

Add `-RemoveEnv` to `reset.ps1` to regenerate secrets as well.

---

## Security limitations

This stack is a **trusted-LAN prototype**, consistent with ARCH-001 §9.1/§15.6 (ADR-002):

- **HTTP only, no TLS.** Traffic between the browser, the Jetson, and the server is unencrypted.
  HTTPS is deferred to future production hardening (ARCH-001 §28.2).
- **`.env` holds plaintext secrets.** Appropriate for a local prototype; a real deployment should use
  a managed secret store. The file is git-ignored and never enters an image.
- **SQL `sa` is used directly.** Acceptable for an isolated container whose port is not published;
  a production deployment should use a least-privilege application login.
- **No authentication on the SQL or Backend containers from within the Compose network.** They are
  protected by network isolation rather than by their own credentials boundary.

What the stack does get right, and should keep: no secret is baked into any image, no secret is
committed, the frontend container receives no database or JWT secret at all, no container is
privileged, no Docker socket is mounted, and neither SQL Server nor the Backend is exposed to the
host or LAN.
