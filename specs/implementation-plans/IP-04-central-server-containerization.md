# Implementation Plan: Central Server Containerization

| Field | Value |
|-------|-------|
| Plan ID | IP-04 |
| Title | Central Server Containerization |
| Status | **COMPLETE (delivered)** |
| Milestone | Central-server deployment — one-command startup of the Dashboard, Backend, database, and migrations |
| Realizes | ARCH-001 §12.1 (co-located central server), ARCH-ASM-001; the deployment substrate IP-01 assumed but never packaged |
| Governing Documents | SRS-001 (frozen), ARCH-001 (Final), FS-01/FS-02 (Final), Engineering Principles, Development Workflow |
| Depends On | IP-01 (T-01–T-30, complete), IP-02 T-40 (complete, `b768662`) |
| Owner | Farhan Naeem |
| Explicitly Excluded | Jetson Agent containerization, DeepStream, NVIDIA runtime, camera device mounts, systemd-in-Docker, Kubernetes/Helm/Terraform, CI/CD pipelines, TLS/HTTPS, production secret management, any Backend or Angular production-code change |

This plan changes no frozen document. It packages the already-delivered IP-01 applications; it adds
no feature, alters no contract, and modifies no application source file.

---

## 1. Objective

Make the central platform — Angular dashboard, ASP.NET Core Backend, SQL Server, and EF Core
migrations — start together with one command on a Windows workstation with Docker Desktop, so that
evaluating or demonstrating the system requires no local Node.js, Angular CLI, .NET SDK, SQL Server,
or EF Core tooling.

The resulting origin is also what a Jetson Agent activates against over the LAN, which is what makes
this a prerequisite for T-41 rather than a convenience.

## 2. Scope

### 2.1 In Scope

| # | Item |
|---|------|
| 1 | A Compose stack: `sqlserver`, `migrations`, `backend`, `frontend` |
| 2 | Multi-stage Backend image (SDK build → publish → ASP.NET runtime) plus a one-shot migrations image |
| 3 | Multi-stage Angular image (Node build → Nginx runtime) |
| 4 | Nginx serving the SPA and reverse-proxying `/api/` to the Backend on one origin |
| 5 | Health- and completion-based startup ordering |
| 6 | A persistent named volume for SQL Server data |
| 7 | `.env`-based configuration with generated local-development secrets |
| 8 | PowerShell start/stop/logs/reset scripts |
| 9 | Deployment documentation |

### 2.2 Out of Scope

- **The Jetson Agent.** Not containerized, by direction and by architecture: it requires NVIDIA,
  DeepStream, camera, and subprocess access, and ARCH-CON-002 places it under systemd on the Jetson
  (T-41).
- TLS/HTTPS — deferred to future hardening (ARCH-001 §28.2, ADR-002 amended: HTTP for the
  trusted-LAN prototype).
- Production secret management, orchestration platforms, CI/CD.
- Any change to Backend or Angular production code.

## 3. Discovered facts the design depends on

Established by reading the applications, not assumed:

| Fact | Value |
|------|-------|
| Target framework | `net10.0` (all four projects) |
| API project / DLL | `src/WeaponDetection.Api` → `WeaponDetection.Api.dll` |
| Project graph | Api → Application, Infrastructure; Application → Domain |
| Health route | `GET /api/v1/health`, `[AllowAnonymous]` (pre-existing; none was added) |
| Connection string key | `ConnectionStrings__DefaultConnection` — startup throws if blank |
| JWT keys | `Jwt__SigningKey` required, **≥ 32 UTF-8 bytes** (`JwtOptionsValidator`, `ValidateOnStart`). Issuer/Audience/lifetime already in `appsettings.json` |
| Admin bootstrap | `BootstrapAdmin__CredentialIdentifier` / `BootstrapAdmin__Password` — `AdminBootstrapper` fails startup if absent when no Admin exists |
| Auto-migration | **None.** `Program.cs` never migrates; the bootstrap needs an existing schema |
| EF design-time seam | `EFCORE_DESIGNTIME_CONNECTION` (`WeaponDetectionDbContextFactory`) |
| Tool manifest | `backend/dotnet-tools.json` (non-standard location; must be named explicitly), pins `dotnet-ef` 10.0.9 |
| Listening config | Honours `ASPNETCORE_URLS`; HTTPS redirection intentionally absent |
| Forwarded headers | Not required — no HTTPS redirect, no absolute-URL generation |
| CORS | None configured — which the single-origin design makes unnecessary |
| Angular | 20.3, builder `@angular/build:application`, npm + `package-lock.json` |
| Node | 22 LTS (Angular 20 supports ^20.19 ‖ ^22.12 ‖ ^24) |
| Build output | **`dist/frontend/browser/`** — confirmed by running the build, not inferred |
| API base URL | `apiBaseUrl: '/api/v1'` in **both** environments — already host-relative |
| SQL image `sqlcmd` | **`/opt/mssql-tools18/bin/sqlcmd`** only; `/opt/mssql-tools` is absent — verified by inspecting the image |

The Angular finding is load-bearing: because the frontend already calls `/api/v1` relative, the
containerized same-origin design required **zero Angular source changes**, and `ng serve` with
`proxy.conf.json` continues to work untouched.

## 4. Architecture

```
                    host :${APP_PORT}          <- only published port
                          │
                  ┌───────▼────────┐
                  │    frontend    │  Nginx + compiled SPA
                  └───┬────────┬───┘
             /        │        │   /api/  (path preserved)
                      │   ┌────▼─────┐
                      │   │ backend  │  ASP.NET Core :8080 (internal)
                      │   └────┬─────┘
                      │   ┌────▼──────┐
                      │   │ sqlserver │  :1433 (internal)
                      │   └────┬──────┘
                      │   ┌────▼───────┐
                      └───┤ migrations │  one-shot, exits 0
                          └────────────┘
```

## 5. Compose services

| Service | Image / build | Published | Restart |
|---------|---------------|-----------|---------|
| `sqlserver` | `mcr.microsoft.com/mssql/server:2022-CU21-ubuntu-22.04` (pinned CU, not floating) | none | `unless-stopped` |
| `migrations` | `backend/Dockerfile` target `migrations` | none | `no` (one-shot) |
| `backend` | `backend/Dockerfile` target `runtime` | none | `unless-stopped` |
| `frontend` | `frontend/Dockerfile` target `runtime` | `${APP_PORT:-8080}:80` | `unless-stopped` |

Network: one internal bridge. Volume: `sqlserver-data`. No `version:` key (obsolete).

## 6. Startup order

Enforced by health and completion conditions, never by sleeps:

```
sqlserver healthy  ->  migrations exited 0  ->  backend healthy  ->  frontend healthy
```

- `sqlserver` health runs a real `SELECT 1` through `sqlcmd -C` (tools 18 default to encryption), so
  it proves the engine accepts connections rather than that the process exists. 60 s grace period.
- `backend` depends on `migrations: service_completed_successfully`, because the Backend neither
  migrates nor tolerates a missing schema during Admin bootstrap.
- `backend` health uses the pre-existing `/api/v1/health`; `curl` is the only package installed into
  the runtime image, with the apt cache removed in the same layer.
- `frontend` health probes `127.0.0.1` explicitly — `listen 80` binds IPv4 only, and `localhost`
  resolves to `::1` first inside the container, which would fail against a healthy server.

## 7. Configuration and secrets

Supplied entirely through `.env` (git-ignored) and injected as environment variables:

| Variable | Consumed as |
|----------|-------------|
| `APP_PORT` | host port mapping |
| `SQL_DATABASE` | database name in both connection strings |
| `SQL_SERVER_PASSWORD` | `MSSQL_SA_PASSWORD`, and the password in both connection strings |
| `JWT_SIGNING_KEY` | `Jwt__SigningKey` |
| `ADMIN_IDENTIFIER` / `ADMIN_PASSWORD` | `BootstrapAdmin__*` |

Required variables use `${VAR:?message}`, so a missing value fails fast with a readable error rather
than silently starting a misconfigured stack.

`start.ps1` generates secrets with a cryptographically secure RNG from an alphabet excluding `$ { }`
(Compose interpolation), `; =` (connection strings), `#` (`.env` comments), quotes, and backslash.
SQL and Admin passwords are regenerated until they satisfy SQL Server complexity. **No generated
value is ever printed.** `.env` is never overwritten, because its credentials are bound to the data
already in the volume.

`.gitignore` gained exactly one line — `!.env.example` — because the pre-existing `.env.*` rule
would otherwise have excluded the committed template.

## 8. Migration strategy

A one-shot `migrations` service built from the SDK stage (dotnet-ef must build the projects to
discover the `DbContext`, which is why it cannot use the runtime image). It restores the pinned local
tool via `--tool-manifest dotnet-tools.json`, then runs `dotnet ef database update` against
`src/WeaponDetection.Infrastructure` with `src/WeaponDetection.Api` as the startup project.

The connection string arrives through `EFCORE_DESIGNTIME_CONNECTION` — the Backend's **existing**
design-time seam — never as a command-line argument, so no credential appears in a process listing.
No Backend production code was changed to accommodate migrations.

## 9. Nginx routing

`proxy_pass http://backend:8080;` deliberately carries **no URI path component**, the form that
forwards the request URI unchanged, so `/api/v1/activate` is not rewritten. SPA fallback
(`try_files $uri $uri/ /index.html`) sits after the `/api/` block so API calls are proxied rather
than swallowed. `server_tokens off` and `proxy_redirect off` keep the Nginx version and internal
service name off the wire.

## 10. Windows developer workflow

```powershell
powershell -ExecutionPolicy Bypass -File deployment/start.ps1   # create .env, build, start, wait
powershell -ExecutionPolicy Bypass -File deployment/logs.ps1    # follow logs (optional -Service)
powershell -ExecutionPolicy Bypass -File deployment/stop.ps1    # down, data preserved
powershell -ExecutionPolicy Bypass -File deployment/reset.ps1   # down -v, requires typing DELETE
```

All four resolve the repository root from `$PSScriptRoot`, so they work from any directory.
`start.ps1` uses `RNGCryptoServiceProvider` rather than `RandomNumberGenerator.Fill`, because the
latter does not exist on the .NET Framework that Windows PowerShell 5.1 runs on.

## 11. Verification evidence

All of the following actually ran on Windows 11 with Docker Desktop (engine 29.4.3, Linux
containers); nothing below is projected.

| Check | Result |
|-------|--------|
| `docker compose config` | Valid. 4 services, no Agent service, only `frontend` publishes a port |
| Image build | All images built; `--build` via `start.ps1` |
| `sqlserver` | **healthy** |
| `migrations` | **exited (0)** |
| `backend` | **healthy** |
| `frontend` | **healthy** |
| `GET /api/v1/health` through Nginx | `200` — `{"success":true,"data":{"status":"Healthy"}}` |
| `GET /` | `200`, Angular index (`LJMU AI Security Platform`), hashed JS/CSS served |
| SPA fallback | `/login`, `/branches`, `/branches/new`, `/dashboard` all `200` with the SPA shell |
| `POST /api/v1/activate` (fake key) | `401` `INVALID_ACTIVATION_KEY` with the exact uniform message — proving `/api` was preserved end to end |
| Admin sign-in | `200`, token issued |
| Branch list | `200` |
| Branch creation | `201`, 1 camera, Activation Key issued, device `Unactivated` |
| Activation Key regeneration | `200`, new key issued |
| Persistence across `down` → `up` | Branch, camera, and Admin credentials all survived (volume retained) |
| Host port exposure | `frontend` 80→8080 only; `sqlserver` `1433/tcp: null`, `backend` `8080/tcp: null` — neither published |
| Backend build | 0 warnings, 0 errors |
| Backend tests | **246 unit + 248 integration = 494 passed** |
| Angular build | Succeeded, output `dist/frontend/browser` |
| Angular tests | **321 passed** (ChromeHeadless) |

Secret handling during verification: no generated password, JWT key, or Activation Key was printed
at any point; the workflow script asserted key *presence* as a boolean only.

## 12. Rollback / reset

- Stop, keep data: `stop.ps1` (`docker compose down`).
- Destroy data: `reset.ps1` (`docker compose down -v`), gated behind typing `DELETE`; `-RemoveEnv`
  additionally discards generated secrets.
- Revert the containerization entirely: remove the added files; no application source was modified,
  so nothing else needs undoing.

## 13. Security limitations

Accepted for this trusted-LAN prototype, recorded rather than hidden: HTTP without TLS; plaintext
secrets in a local `.env`; direct use of the SQL `sa` login; services protected by network isolation
rather than their own credential boundary. Countervailing controls that must be preserved: no secret
in any image layer, no secret committed, the frontend container receives no database or JWT secret,
no privileged container, no Docker socket mount, no `network_mode: host`, and no host exposure for
SQL Server or the Backend.

## 14. Jetson integration

```
WDA_BACKEND_BASE_URL=http://<server-ip>:${APP_PORT}
```

The Agent reaches `/api/v1/activate` through the same Nginx origin, unchanged. `http://backend:8080`
must never be used outside Docker — it resolves only inside the Compose network. Native Jetson
installation remains **T-41**.

## 15. Completion status

**COMPLETE.** Delivered as `chore(deploy): containerize central platform`.

Files added: `compose.yaml`, `.env.example`, `backend/Dockerfile`, `backend/.dockerignore`,
`frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`, `deployment/README.md`,
`deployment/start.ps1`, `deployment/stop.ps1`, `deployment/logs.ps1`, `deployment/reset.ps1`, this
plan. Files modified: `.gitignore` (one negation line), `README.md`, `backend/README.md`,
`frontend/README.md` (documentation only).

**No Backend, Angular, or Agent production source file was modified.** The Agent was not
containerized. IP-02 T-41 (Jetson deployment and systemd service) was not started and is unchanged.
