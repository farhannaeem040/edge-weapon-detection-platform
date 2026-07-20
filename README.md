# Edge-Based Weapon Detection and Centralized Monitoring System

This repository contains the source code and documentation for a Software Engineering dissertation
focused on designing and implementing an edge-based weapon detection platform.

The platform combines AI-powered weapon detection running on NVIDIA Jetson devices with a centralized
web application for monitoring, configuration, and alert management.

The project follows a Spec-Driven Development workflow — documentation is the source of truth, and
implementation follows approved specifications. Claude Code is used as an AI engineering assistant.

---

## Project Status

**The "Backend and Angular Foundation" milestone (IP-01, tasks T-01–T-30) is complete.** It realizes
[FS-01](specs/features/FS-01-authentication-session-management.md) in full and
[FS-02](specs/features/FS-02-branch-device-onboarding.md) Increment A.

**Branch & Camera Management (edit / delete) — IP-03, tasks T-42–T-47 — is complete**, realizing
[FS-03](specs/features/FS-03-branch-camera-management.md) in full. (Task IDs T-31–T-41 are reserved
by IP-02 for the Jetson Agent and are not used here.)

This is a **prototype**, not a production deployment. Nothing below claims otherwise.

### Delivered and covered by automated tests

- Admin login, JWT issuance, and server-side session validation.
- Logout with server-side session revocation; expired/revoked-session rejection.
- Branch creation with one or more Cameras, in a single transaction.
- **Transactional Branch editing** (`PUT /api/v1/branches/{id}`): edit branch name/address/contact
  details, edit existing Cameras in place, add Cameras, and remove Cameras (at least one must remain),
  all in one transaction. Editing preserves the public Branch ID, the Device record and its public
  Device ID, activation status, the protected shared secret, and Activation Key history — it never
  regenerates a key, deactivates a Device, rotates the secret, or exposes any internal identifier.
- **Transactional Branch deletion** (`DELETE /api/v1/branches/{id}`): a hard delete that removes the
  branch's Activation Keys, Device, Cameras, and the Branch itself atomically, behind an explicit
  Admin confirmation. It invalidates Backend recognition of the deleted Device and contacts no remote
  Agent. (Future Alerts/Events/Reports and audit-retention needs may later require soft deletion or
  deletion restrictions; those entities do not exist in the current milestone.)
- One-time Activation Key issuance and regeneration.
- Device activation through the Agent-facing `POST /api/v1/activate`, including consumed-key replay
  rejection, Device ID retention across reactivation, and concurrent single-use key enforcement.
- The Angular Dashboard: login, protected routes, branch list/detail/create, Activation Key display,
  regeneration, and device status badge; plus the **branch edit form** (`/branches/:branchId/edit`),
  per-branch **Edit/Delete icon actions** on the list and detail views, and the accessible
  **delete-confirmation dialog** — Edit preserves device identity and shows no key/secret, and Delete
  requires explicit confirmation warning the action cannot be undone.

### Verified by live manual demonstration (API level)

Executed against a clean throwaway SQL Server database, the real Backend, and the real Angular dev
server, per IP-01 §18: clean startup and Admin bootstrap; idempotent bootstrap on restart; login and
generic invalid-credential rejection; protected route rejection without a token; branch list empty
state; branch creation with multiple cameras; one-time key disclosure; key absent from read
responses; unactivated status with no Device ID; regeneration; old-key rejection; activation;
Activated status with a persistent Device ID; reactivation retaining that Device ID while rotating
the shared secret; logout revoking the session server-side; and the revoked JWT receiving `401`.

The dev server starts, serves the app shell, and proxies `/api` to the real Backend.

**A visual browser walkthrough has not been performed** — no browser automation was available in this
environment. The steps left for a human pass are listed under
[Known limitations](#known-limitations-and-deferred-work).

### Scaffolded — Jetson Agent foundation in progress

The [`agent/`](agent/README.md) FastAPI project has been **scaffolded** (IP-02 T-31: `pyproject.toml`,
development tooling, a minimal importable FastAPI application with **no endpoints**), has its
**validated bootstrap-configuration foundation** (IP-02 T-32: `WDA_`-prefixed environment settings,
loaded into one immutable, validated object with fail-fast on a missing/invalid Backend URL), and its
**structured logging foundation with secret redaction** (IP-02 T-33: newline-delimited JSON to
stdout/optional file, with central redaction that keeps the Activation Key and device secret out of
logs at every level), its **filesystem-layout provisioning** (IP-02 T-34: the `/opt/weapon-detection/`
directories created with their ADR-008 modes, idempotently), and its **SQLite store foundation and
schema** (IP-02 T-35: connection management plus the idempotent, versioned `SchemaVersion`/
`DeviceIdentity`/`ConfigCache` schema at `<root>/database/agent.db`, mode `0600`), its
**persistence repositories** (IP-02 T-36: a `DeviceIdentityRepository` — load, first-activation
store, and atomic shared-secret replacement retaining the permanent Device ID — and a **load-only**
`ConfigCacheRepository`, its writer deferred under OI-2), and its **Backend activation HTTP client**
(IP-02 T-37: an async `BackendActivationClient` that performs exactly one `POST /api/v1/activate` per
call — **no automatic retry**, since the one-time key is not idempotent — and returns a typed,
secret-safe `ActivationResult`), and its **activation/reactivation orchestration service** (IP-02
T-38: an `ActivationService` that resolves the key env-over-file, calls the client once, persists the
identity — first activation stores it, reactivation replaces the secret while keeping the permanent
Device ID and original activation time, and a returned Device-ID mismatch fails loudly — and deletes
a file key only after success; a timeout is surfaced as an ambiguous outcome, never retried), and its
**FastAPI startup workflow and single-worker runtime** (IP-02 T-39: `create_app()` attaches a
lifespan that runs the §12.1 startup decision — provision → logging → schema → activation — and
publishes a secret-free `AgentRuntime` on `app.state.runtime` only on success, closing the owned
client and refusing to serve on any failure; no operational/health endpoint is defined, OI-3). The
Agent now **activates in its startup lifespan** and the activation foundation is wired end-to-end at
the Agent level. It is also **verified against the real Backend** (IP-02 T-40): a simulated-contract
layer exercises every Agent branch and failure mode in-memory, and an opt-in contract layer runs the
real Agent against the **actual** ASP.NET Core Backend over loopback HTTP, backed by a throwaway SQL
Server database, with the Branch and Activation Key created through the real authenticated API —
confirming first activation, one-time-key consumption, the uniform `401`, a restart making no
activation call, and reactivation retaining the Device ID while rotating the secret. What remains is
the Jetson/systemd deployment (**T-41**), so end-to-end activation *on the Jetson itself* is not yet
complete. That arrives with IP-02 task T-41 (see
[`specs/implementation-plans/IP-02-jetson-agent-foundation.md`](specs/implementation-plans/IP-02-jetson-agent-foundation.md)).

### Not started — future work

The Jetson Agent's activation/persistence/runtime behaviour (IP-02 T-32–T-41), DeepStream
integration, and the AI pipeline. `POST /api/v1/activate` is fully functional and independently
callable by any HTTP client, which is what proves the Agent-facing contract is ready before the
Agent implements its side.

---

## Architecture Summary

See [`docs/architecture/software-architecture-document.md`](docs/architecture/software-architecture-document.md)
(ARCH-001) for the authoritative design.

- **ASP.NET Core Backend** (`backend/`) in four layers per ARCH-001 §10.1 — `Api` (controllers,
  middleware, DI composition), `Application` (services, interfaces, DTO contracts), `Domain`
  (entities), `Infrastructure` (EF Core, SQL Server, hashing, JWT, Data Protection). The dependency
  direction is enforced by a test (`ProjectReferenceDirectionTests`).
- **SQL Server** for persistence, via EF Core migrations. No migration seeds data.
- **Angular Dashboard** (`frontend/`) with `core`/`auth`/`branches`/`shared` boundaries per
  ARCH-001 §10.5.
- **Authentication: JWT plus server-side `AdminSession` validation.** A request passes only if both
  the token's signature/issuer/audience/expiry are valid **and** the `AdminSession` named by its
  `jti` exists, belongs to the same Admin, is unexpired, and is not revoked. Discarding a token in
  the browser therefore does not invalidate it — only the server-side session record does.
- **Identity model:** `Branch` (1)→(many) `Camera`; `Branch` (1)→(1) `Device`; `Device`
  (1)→(many, historically) `ActivationKey`. `Device` splits an internal `DeviceRecordId` (never
  exposed by any API) from the external `DeviceId` (null until first activation, then retained
  forever).
- **One-time Activation Key exchange.** A key is two parts, `keyId.secret`. The Backend stores only
  the `keyId` and a salted hash of the `secret`, so it can never re-derive the key: the complete
  plaintext is shown exactly once, at generation. Lookup is a direct primary-key hit on `keyId`,
  never a scan across stored hashes.
- **Protected device shared-secret storage.** The Backend must re-present the device shared secret
  when authenticating outbound commands to an Agent, so it is stored recoverable-but-protected via
  ASP.NET Core Data Protection behind an `IDeviceSecretProtector` abstraction — deliberately not
  one-way hashed, unlike the Admin password and the Activation Key secret.
- **Anonymous Agent activation endpoint.** `POST /api/v1/activate` is exempt from Admin JWT
  authentication only — it is authenticated by the Activation Key itself, and still performs full
  request validation, error handling, and envelope wrapping like every other endpoint.

---

## Prerequisites

Versions this milestone was actually built and verified on:

| Tool | Version used |
|------|--------------|
| .NET SDK | 10.0.102 (projects target `net10.0`) |
| SQL Server | 2025 Express (any SQL Server-compatible instance works; 2022 via Docker also documented below) |
| Node.js | 24.11.1 |
| npm | 11.6.2 |
| Angular | 20.3.x, CLI 20.3.32 — a **local** dev dependency, so no global install is needed |

> The workspace targets Angular 20 deliberately: Angular 21+/CLI 22 require Node.js ≥ 24.15, so on a
> Node 24.11 toolchain Angular 20 (which supports Node ^24.0) is the compatible line. Upgrade Angular
> alongside a Node upgrade, not independently.

---

## Local Backend Setup

### SQL Server

Any SQL Server-compatible database works:

**Option A — Docker:**

```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=<your-strong-password>" \
  -p 1433:1433 --name weapondetection-sql --hostname weapondetection-sql \
  -d mcr.microsoft.com/mssql/server:2022-latest
```

**Option B — a local SQL Server / SQL Server Express instance** already installed on the machine.

### Configuration (user-secrets — never committed)

All three values below are secrets. **No default username or password is committed anywhere**, and
the application will not start without them. Every value shown is a placeholder.

```bash
cd backend
dotnet user-secrets set "ConnectionStrings:DefaultConnection" "Server=localhost\SQLEXPRESS;Database=WeaponDetectionDev;Trusted_Connection=True;TrustServerCertificate=True;" --project src/WeaponDetection.Api
dotnet user-secrets set "BootstrapAdmin:CredentialIdentifier" "<admin-identifier>" --project src/WeaponDetection.Api
dotnet user-secrets set "BootstrapAdmin:Password" "<strong-password>" --project src/WeaponDetection.Api
dotnet user-secrets set "Jwt:SigningKey" "<strong-random-signing-key>" --project src/WeaponDetection.Api
```

For a SQL-authenticated instance (e.g. the Docker container above), the connection string takes the
form `Server=localhost,1433;Database=WeaponDetectionDev;User Id=sa;Password=<your-strong-password>;TrustServerCertificate=True;`.

The signing key must be at least 32 bytes (UTF-8) — a random 32+ character string suffices for this
prototype's HMAC-SHA256 signing. Only the signing key is secret; the JWT issuer, audience, and
token lifetime have non-secret defaults committed in `appsettings.json`.

In non-development environments, set the equivalent double-underscore environment variables, which
ASP.NET Core's configuration system maps to the same keys:

```
ConnectionStrings__DefaultConnection
BootstrapAdmin__CredentialIdentifier
BootstrapAdmin__Password
Jwt__Issuer   Jwt__Audience   Jwt__SigningKey   Jwt__AccessTokenLifetimeMinutes
```

If a required value is missing, the API fails to start with a clear error naming the missing key,
rather than starting in a broken state.

### Migrations and startup behavior

**Migrations are not applied automatically at startup** — apply them explicitly. `dotnet-ef` is a
repo-local tool; restore it once per clone.

```bash
cd backend
dotnet tool restore
EFCORE_DESIGNTIME_CONNECTION="<same-value-as-your-connection-string>" \
  dotnet ef database update --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api
```

`EFCORE_DESIGNTIME_CONNECTION` is required for `database update`: CLI discovery does not need a real
database, so `WeaponDetectionDbContextFactory` supplies a design-time placeholder connection string.
That placeholder is intentional for `migrations add`/`dbcontext info`, but it must be overridden for
`database update` to reach your real database.

**Admin bootstrap runs at startup**, not as a migration. On startup the Backend checks whether any
`AdminUser` exists; if none does, it hashes the configured bootstrap password and creates exactly one
row. If one already exists it does nothing — a second startup never creates a duplicate and never
overwrites the existing account. Changing the bootstrap values after the first run has no effect: it
does not reset the existing credential or password. The plaintext bootstrap password is never logged,
never persisted, and never committed — only its hash is stored.

### Run

```bash
cd backend
dotnet run --project src/WeaponDetection.Api
```

The API listens on the URL shown in the console (see
`src/WeaponDetection.Api/Properties/launchSettings.json`; the development profile uses
`http://localhost:5230`). Health check:

```
GET /api/v1/health   ->   {"success":true,"data":{"status":"Healthy"}}
```

This probe is T-01 scaffolding — a fixed `"Healthy"` literal, exposing no data. Real health/status
reporting is a later feature.

---

## Local Angular Setup

```bash
cd frontend
npm ci
npm start
npm test -- --watch=false
npm run build
npm audit
```

- `npm start` serves the Dashboard at `http://localhost:4200/`. Start the Backend first — the dev
  server **proxies `/api` to `http://localhost:5230`** (`frontend/proxy.conf.json`), so the browser
  issues only same-origin requests and no CORS policy is needed.
- In production the Dashboard uses the **host-relative `/api/v1`**, since its static assets are served
  from the same host as the Backend in the co-located prototype deployment.
- **Token storage:** the JWT is held in `sessionStorage` under exactly one key
  (`weapon-detection.auth-token`). `sessionStorage`, not `localStorage`, is deliberate — the token is
  scoped to the browser session and must not outlive the tab, and there is no refresh-token flow. It
  is never logged, never rendered, and never decoded — no client-side claim is treated as
  authoritative.
- **No secrets belong in `src/environments/`.** Those files hold only the non-secret base URL; they
  are compiled into the browser bundle and readable by anyone who loads the page.
- `npm test` requires a Chrome/Chromium browser.

---

## User Workflow

1. **Log in** with the bootstrapped Admin credential.
2. **Create a Branch** with one or more Cameras.
3. **Copy the one-time Activation Key**, shown in complete plaintext exactly once. The Backend cannot
   re-derive it and no endpoint will return it again — if it is lost, regenerate.
4. **View the Branch and its Device status.** An unactivated Device shows no Device ID.
5. **Regenerate the key** when required. This invalidates the previous key immediately, whether or not
   it had been used.
6. **Activate** by supplying the key to the Agent-facing `POST /api/v1/activate`. Until a real Agent
   exists, any HTTP client can perform this step.
7. **Refresh** the Branch detail to observe the Device as `Activated` with its persistent Device ID.
   Status does not update on its own — see known limitations.

---

## API Summary

| Endpoint | Method | Auth |
|----------|--------|------|
| `/api/v1/health` | GET | None (`[AllowAnonymous]`) |
| `/api/v1/auth/login` | POST | None — it is what issues a session, so it cannot require one |
| `/api/v1/auth/logout` | POST | JWT + server-side session |
| `/api/v1/branches` | POST | JWT + server-side session |
| `/api/v1/branches` | GET | JWT + server-side session |
| `/api/v1/branches/{id}` | GET | JWT + server-side session |
| `/api/v1/branches/{id}` | PUT | JWT + server-side session |
| `/api/v1/branches/{id}` | DELETE | JWT + server-side session |
| `/api/v1/devices/{id}` | GET | JWT + server-side session |
| `/api/v1/devices/{id}/activation-key/regenerate` | POST | JWT + server-side session |
| `/api/v1/activate` | POST | None for Admin-JWT purposes — authenticated by the Activation Key itself |

Every endpoint is protected **by default** through a fallback authorization policy; the three
exemptions above each opt out explicitly with `[AllowAnonymous]`, so a forgotten `[Authorize]` cannot
silently expose a route. All responses — the anonymous ones included — use the uniform envelope
(`{success, message, data}` / `{success, message, errorCode}`) applied centrally, per ADR-009.

Every authentication failure (no header, malformed or wrongly-signed token, expired token, no `jti`,
unknown or mismatched session, revoked session) returns `401` with the same generic envelope. The
response never reveals which check failed.

> **`PUT /api/v1/branches/{id}`** edits the branch and reconciles its cameras in one transaction and
> returns the same safe read shape as `GET` (`200`); it never returns an Activation Key, key hash,
> `DeviceRecordId`, or secret, and never touches the Device or Activation Key records. A camera in
> the request carries its existing `cameraId` to be updated in place, or no id to be added; an omitted
> existing camera is removed, and at least one camera must remain. Invalid input — a blank/oversized
> field, an empty camera list, a malformed RTSP URL, or a `cameraId` that is unknown, duplicated, or
> belongs to another branch — is a generic `400`; an unknown branch is `404`.
>
> **`DELETE /api/v1/branches/{id}`** hard-deletes the branch and its Activation Keys, Device, and
> Cameras in one transaction and returns an empty success envelope (`200`, `null` data) — no deleted
> entity and no credential. An unknown branch is `404`. A failure at any step rolls the whole
> operation back. No remote Agent is contacted.

> **`/api/v1/devices/{id}/activation-key/regenerate` takes a _Branch_ ID, despite its `/devices/`
> path.** This is the current approved contract (IP-01 §10, resolving FS-02 §19's open item), not a
> pattern to copy for new clients. The reason is structural: regeneration must work for a Device that
> has never activated, whose `DeviceId` is still null and cannot address it, while the internal
> `DeviceRecordId` is never exposed to any client. The Branch ID is the only always-present,
> client-visible handle to a Branch's single Device.

Logout takes no request body: the session to revoke is identified by the token's own `jti`, so a
client can never ask the Backend to revoke someone else's session. Only the presented session is
revoked; any other active session for the Admin continues to work.

---

## Testing

Totals from the final clean run of this milestone:

| Suite | Result |
|-------|--------|
| Backend unit (`WeaponDetection.UnitTests`) | **234 passed**, 0 failed, 0 skipped |
| Backend integration (`WeaponDetection.IntegrationTests`, real SQL Server) | **216 passed**, 0 failed, 0 skipped |
| Angular (Karma + Jasmine) | **250 passed**, 0 failed |
| **Total** | **700 passed, 0 failed** |

| Gate | Result |
|------|--------|
| `dotnet build ./backend/WeaponDetection.slnx` | Succeeded — **0 warnings, 0 errors** |
| `dotnet list ./backend/WeaponDetection.slnx package --vulnerable --include-transitive` | No vulnerable packages in any project |
| `dotnet ef migrations has-pending-model-changes` | "No changes have been made to the model since the last migration" |
| Migrations M1–M3 against a clean throwaway database | All applied successfully |
| `npm run build` (production) and `ng build --configuration development` | Both succeeded |
| `npm audit` | **0 vulnerabilities** |

Reproduce with:

```bash
dotnet restore ./backend/WeaponDetection.slnx
dotnet build ./backend/WeaponDetection.slnx --no-restore
dotnet test ./backend/WeaponDetection.slnx --no-build
cd frontend && npm ci && npm test -- --watch=false && npm run build
```

Integration tests create and drop their own uniquely-named throwaway SQL Server databases on
`localhost\SQLEXPRESS`. Per IP-01 §9, anything claiming relational/transactional behavior — migrations,
the filtered unique index, transactional key consumption, and concurrent activation — runs against
real SQL Server, never an in-memory provider whose simplified locking semantics would make the claim
hollow.

### Acceptance-criteria traceability

**FS-01 — Authentication & Session Management**

| Criterion | Where it is verified |
|-----------|----------------------|
| Login success (AC-1) | `AuthServiceTests`, `AuthLoginApiTests`; Angular `auth.service.spec.ts`, `login.spec.ts` |
| Generic invalid-credential failure (AC-2) | `AuthLoginApiTests` (same 401 + `INVALID_CREDENTIALS` for unknown identifier and wrong password) |
| JWT issuance and storage | `JwtIssuerTests`, `JwtOptionsValidatorTests`, `JwtOptionsStartupValidationTests`; Angular `auth.service.spec.ts` (single `sessionStorage` key) |
| Protected-route behavior (AC-3, AC-7) | `SessionAuthorizationApiTests`; Angular `auth.guard.spec.ts`, `auth.interceptor.spec.ts` — the guard is a UX control only |
| Backend session validation | `AdminSessionValidatorTests`, `SessionAuthorizationApiTests` (missing `jti`, unknown session, mismatched user, expired independent of revocation) |
| Logout revocation (AC-5, AC-6) | `AuthLogoutApiTests` (token copied before logout is rejected after it; second logout rejected) |
| Expired/revoked-session handling | `SessionAuthorizationApiTests`; Angular `session-expiry.interceptor.spec.ts` (global 401 → clear state → `/login`) |
| Admin bootstrap precondition | `AdminBootstrapperTests` (create / no-op / fail-fast) |

**FS-02 Increment A — Branch & Device Onboarding**

| Criterion | Where it is verified |
|-----------|----------------------|
| Branch creation with one or more Cameras (AC-1) | `BranchServiceTests`, `BranchServiceValidationTests`, `BranchApiTests`; Angular `branch-create.spec.ts` (FormArray, ≥1 camera) |
| Initial one-time Activation Key disclosure (AC-2) | `BranchApiTests`, `ActivationKeyGeneratorTests`; Angular `activation-key-display.spec.ts` |
| Branch list/detail (AC-1, §7) | `BranchApiTests`, `DeviceApiTests` (key and `DeviceRecordId` never present in read responses); Angular `branch-list.spec.ts`, `branch-detail.spec.ts` |
| Activation Key regeneration (AC-5) | `DeviceServiceRegenerationTests`, `RegenerateActivationKeyApiTests`; Angular `branch-detail.spec.ts` |
| Unactivated/activated status (AC-7) | `DeviceApiTests`; Angular `device-status-badge.spec.ts` (no `deviceId` rendered while unactivated) |
| Device activation endpoint (AC-3, AC-12) | `DeviceServiceActivationTests`, `ActivateApiTests` |
| Consumed-key replay rejection (AC-4, AC-9) | `ActivateApiTests`, `DeviceServiceActivationTests` |
| Device ID retention during reactivation (AC-7) | `DeviceServiceActivationTests`, `ActivateApiTests` (regenerate → reactivate → same `DeviceId`, rotated secret) |
| Concurrent single-use key enforcement (AC-16) | `ConcurrentActivationTests` — real SQL Server |
| Indistinguishable rejections (AC-15) | `ActivateApiTests` (malformed / unknown `keyId` / wrong secret) |
| Indexed lookup; no recoverable plaintext (AC-14) | `ActivationKeyGeneratorTests`, `Pbkdf2PasswordHasherTests`, `DataProtectionDeviceSecretProtectorTests` |
| No self-registration path for an Agent (AC-6, AC-8) | `BranchApiTests`, `SessionAuthorizationApiTests` (creation requires an Admin session) |
| Schema-level one-Device-per-Branch (§1.3) | `DeviceActivationKeySchemaSqlServerTests` (unique `BranchId`; filtered unique `DeviceId`) |

**FS-03 — Branch & Camera Management (Edit / Delete)**

| Criterion | Where it is verified |
|-----------|----------------------|
| Edit branch scalar fields (AC-1) | `BranchUpdateServiceTests`, `BranchUpdateApiTests`; Angular `branch-edit.spec.ts` |
| Edit Cameras in place, preserving Camera ID (AC-2, AC-9) | `BranchUpdateServiceTests`, `BranchUpdateApiTests`; Angular `branch-edit.spec.ts` (hidden `cameraId` retained; exact PUT payload) |
| Add / remove Cameras; at least one remains (AC-3, AC-4, AC-5) | `BranchUpdateServiceTests`, `BranchUpdateApiTests`; Angular `branch-edit.spec.ts` |
| Transactional rollback on failed edit (AC-6) | `BranchUpdateServiceTests` (real SQL Server) |
| Device identity + activation + key preserved during edit (AC-7, AC-8) | `BranchUpdateServiceTests`, `BranchUpdateApiTests` (`DeviceRecordId`, public `DeviceId`, activation status, protected secret, Activation Keys all unchanged) |
| Complete dependent-data removal on delete (AC-10) | `BranchDeleteApiTests`, `BranchDeletionServiceTests`; Cameras, Device, Activation Keys, Branch all removed |
| Transactional rollback on failed delete (AC-11) | `BranchDeletionServiceTests` (forced mid-delete FK failure, real SQL Server) |
| Deleted Device no longer recognised; other branches unaffected (AC-11, AC-12) | `BranchDeleteApiTests` |
| Authorization on edit/delete (AC-13) | `BranchUpdateApiTests`, `BranchDeleteApiTests` (`401` without a session) |
| Safe responses — no secret/internal id (AC-14) | `BranchUpdateApiTests`, `BranchDeleteApiTests`; Angular specs assert no key/secret/`DeviceRecordId` rendered |
| Confirmation before deletion (AC-13) | Angular `branch-delete-confirm.spec.ts`, `branch-list.spec.ts`, `branch-detail.spec.ts` (cancel sends no request; confirm calls the endpoint once) |
| Edit/Delete icons beside each branch; accessibility (AC-15, AC-16) | Angular `branch-list.spec.ts`, `branch-detail.spec.ts` (aria-label/title naming the branch, keyboard-focusable controls) |

---

## Security Notes

- **No default Admin credential exists.** Nothing is seeded, and no registration flow exists. The
  single Admin is provisioned only by the startup bootstrap, from configuration you supply.
- **Credentials are configured locally through user-secrets** (or environment variables outside
  development). No connection string, password, signing key, or secret is committed — see
  `.gitignore`.
- **The JWT is validated against server-side session state**, not on its own. Revocation is enforced
  by the `AdminSession` record, which is what makes logout real.
- **Passwords are hashed** with a salted adaptive hash (PBKDF2) and are never stored or logged in
  plaintext.
- **Activation Keys are stored only as `keyId` plus a salted hash of the `secret`.** The complete
  plaintext key is never persisted, so it cannot be recovered from the database or through any read
  route — verified during milestone demonstration against a live database.
- **Device shared secrets are stored protected** (Data Protection), never plaintext and never logged.
  They are protected rather than hashed because the Backend must re-present them to authenticate
  outbound commands to an Agent.
- **One-time plaintext disclosure.** The Activation Key appears only in the create/regenerate
  response; the device shared secret only in the activation response. Neither is ever returned again.
- **RTSP URLs may embed credentials**, so they are sanitized before appearing in any error and are
  never logged (`RtspUrlSanitizer`).
- **Trusted-LAN HTTP limitation.** The prototype runs over plain HTTP on a trusted LAN
  (ARCH-ASM-001). Bearer tokens, Activation Keys, and shared secrets therefore cross the network
  unencrypted. **A production deployment requires TLS** and proper secret/key management — Data
  Protection keys currently persist to the local file-system key ring on the single Server Host,
  with enterprise key management, rotation, and HSM integration explicitly out of scope.

---

## Known Limitations and Deferred Work

- **No real Jetson Agent yet.** `POST /api/v1/activate` is exercised by test HTTP clients only.
- **No DeepStream integration or AI pipeline yet.**
- **No live streaming, alert processing, health reporting, or configuration management yet.** The
  `/api/v1/health` route is a fixed scaffolding literal, not device health.
- **The activation response returns no operational configuration.** FS-02 §10.4 anticipates
  `deviceId`, shared secret, *and* branch/camera/operational configuration; the delivered response
  carries `deviceId`, `sharedSecret`, and `branchId`. This milestone models no Configuration entity
  (IP-01 §4), so inventing values was out of scope — delivering the configuration payload belongs to
  the later configuration feature, which FS-02 establishes will occur at activation.
- **Prototype scale assumptions.** The model supports many Branches, but the prototype is exercised
  around a single Branch/Device, and BR-001's single-Admin model means no multi-user support, no
  roles, and no password reset.
- **No automatic status updates.** Device activation status changes are seen only on an explicit
  refresh — there is no polling, WebSocket, or live update.
- **Authenticated shell (resolved).** The Stitch visual redesign (branch `feat/ui-stitch-redesign`,
  frontend-only) introduced a shared authenticated shell — a sidebar with Branches navigation and a
  Sign-out action in the top header, reachable from every authenticated view. The Dashboard is
  rebranded **LJMU AI Security Platform**. Only screens backed by an implemented feature are styled;
  deferred Stitch screens (alerts, monitoring, analytics, reports, health, device fleet, settings) are
  not built or linked. See [`design/stitch/`](design/stitch/) and the frontend README.
- **The visual browser walkthrough still requires a human pass.** No browser automation was
  available, so IP-01 §18 was executed at the API level plus the Angular component suite. Left to
  verify visually in a browser: the unauthenticated redirect to `/login`; login landing on
  `/branches`; the single generic message rendered on a failed login; the branch-list empty state;
  the create form's multi-camera flow; the key being displayed exactly once and absent from the URL
  and browser storage (DevTools → Application); the regeneration confirmation; the badge flipping to
  `Activated` on refresh; and the global 401 redirect. Each has automated component coverage; what is
  unverified is only their rendered behavior in a real browser.
- **`.gitignore` does not list `.claude/settings.json` or local database files** (`*.mdf`/`*.ldf`).
  Nothing of the sort is currently tracked, but adding them would make the protection structural
  rather than a matter of care.

---

## Next Milestone

**No further implementation plan exists in this repository yet** — `specs/implementation-plans/`
contains only [IP-01](specs/implementation-plans/IP-01-backend-angular-foundation.md), now complete.

The next step is therefore **to author and approve the Agent/Jetson implementation plan** (FS-02
Increment B and the Feature Specifications that follow it) before any Agent code is written. No task
beyond T-30 is currently approved.

---

## Repository Structure

| Path | Contents |
|------|----------|
| `docs/foundation/` | Project charter, vision, SRS, engineering principles, development workflow |
| `docs/architecture/` | ARCH-001, the software architecture document |
| `specs/features/` | Feature Specifications (FS-01, FS-02, FS-03) |
| `specs/implementation-plans/` | Implementation plans (IP-01 complete; IP-02 Jetson Agent; IP-03 branch edit/delete) |
| `backend/` | ASP.NET Core solution — `src/` (four layers) and `tests/` (unit + integration) |
| `frontend/` | Angular Dashboard workspace — see [`frontend/README.md`](frontend/README.md) |
| `agent/` | Jetson Agent FastAPI project (scaffold — IP-02 T-31) — see [`agent/README.md`](agent/README.md) |

> `specs/features/FS-02-branch-device-onboarding-activation.md` is **superseded** by
> `specs/features/FS-02-branch-device-onboarding.md` and is retained for history only. Do not
> implement from it.

## Development Methodology

Spec-Driven Development: every feature follows Requirements → Specification → Implementation Plan →
Implementation → Review → Testing → Documentation. Features are specified before implementation, and
documentation is the source of truth. See
[`docs/foundation/development-workflow.md`](docs/foundation/development-workflow.md) and
[`docs/foundation/engineering-principles.md`](docs/foundation/engineering-principles.md).

## Objectives (Full Project Scope)

- Detect guns and knives using a fine-tuned YOLO26 model. *(future work)*
- Perform inference locally using NVIDIA DeepStream. *(future work)*
- Provide centralized monitoring through a web dashboard. *(foundation delivered)*
- Support branch registration and remote configuration. *(registration delivered; remote
  configuration is future work)*
- Monitor Jetson device health. *(future work)*
- Generate and manage weapon detection alerts. *(future work)*
- Demonstrate a complete end-to-end prototype. *(in progress)*

## Technology Stack

| Layer | Technology | Status |
|-------|-----------|--------|
| AI | YOLO26, NVIDIA DeepStream, TensorRT | Future work |
| Edge device | NVIDIA Jetson Orin Nano | Future work |
| Backend | ASP.NET Core (net10.0) | Delivered |
| Frontend | Angular 20 | Delivered |
| Database | SQL Server | Delivered |
