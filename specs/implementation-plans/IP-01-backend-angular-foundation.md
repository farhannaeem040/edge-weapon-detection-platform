# Implementation Plan: Backend and Angular Foundation

| Field | Value |
|-------|-------|
| Plan ID | IP-01 |
| Title | Backend and Angular Foundation |
| Status | Approved |
| Milestone | Workstream A — Backend & Angular Foundation (Work Items A1–A3: Authentication, Branch/Camera Management, Device Activation API) |
| Realizes | FS-01; FS-02 Increment A |
| Feature Specification Links | [`specs/features/FS-01-authentication-session-management.md`](../features/FS-01-authentication-session-management.md), [`specs/features/FS-02-branch-device-onboarding.md`](../features/FS-02-branch-device-onboarding.md) |
| Owner | Farhan Naeem |
| Explicitly Excluded From This Plan | Real Jetson Agent, simulated Jetson client, heartbeat ingestion, alerts, offline synchronization, DeepStream, WebRTC, siren, reporting |

This plan does not modify SRS-001 or ARCH-001. It breaks already-approved Feature Specification behavior into implementable, independently committable tasks. Where a task references a decision not fixed by FS-01/FS-02 (e.g., exact folder names, exact package choices), that decision is an implementation detail within the bounds already left open by those specs' "Open Implementation Details" sections.

---

## 1. Repository Structure

```
edge-weapon-detection-platform/
├── docs/                                  (existing — untouched)
├── specs/                                  (existing — untouched)
├── backend/
│   ├── WeaponDetection.sln
│   ├── src/
│   │   ├── WeaponDetection.Api/            (ASP.NET Core Web API host — Presentation layer)
│   │   ├── WeaponDetection.Application/    (services, DTOs, interfaces — Application layer)
│   │   ├── WeaponDetection.Domain/         (entities, domain rules — Domain layer)
│   │   └── WeaponDetection.Infrastructure/ (EF Core, SQL Server, hashing, JWT, Data Protection — Infrastructure layer)
│   └── tests/
│       ├── WeaponDetection.UnitTests/
│       └── WeaponDetection.IntegrationTests/
└── frontend/
    ├── angular.json
    ├── src/app/
    │   ├── core/                           (interceptors, guards, core services — singleton)
    │   ├── auth/                           (login view, AuthService)
    │   ├── branches/                       (branch list/create/detail, camera config, device status)
    │   └── shared/                         (shared UI components, models)
    └── src/app/... (tests colocated with each component/service, per Angular CLI convention)
```

This mirrors ARCH-001 §10.1 (Clean/layered Backend) and §10.5 (Angular feature-module structure) directly — no new architecture is introduced.

## 2. Backend Projects/Layers

| Project | Layer (ARCH-001 §10.1) | Responsibility in This Milestone |
|---------|--------------------------|--------------------------------------|
| `WeaponDetection.Domain` | Domain | `AdminUser`, `AdminSession`, `Branch`, `Camera`, `Device`, `ActivationKey` entities (FS-02 §1.3/§1.4 identity/key model) |
| `WeaponDetection.Application` | Application | `AuthService`, `BranchService`, `DeviceService` (Activation Key generation/lookup/validation), `AdminBootstrapService`, DTOs, request/response contracts, validation |
| `WeaponDetection.Infrastructure` | Infrastructure | EF Core `DbContext`, SQL Server migrations, password hasher, Activation Key hasher, JWT issuer, `IDeviceSecretProtector` (Data Protection) |
| `WeaponDetection.Api` | API/Presentation | `AuthController`, `BranchController`, `DeviceController`, `ActivateController`, JWT/session middleware, response-envelope middleware, DI composition root, Admin bootstrap hosted service |

## 3. Angular Modules/Components/Services

| Module | Components | Services | Responsibility |
|--------|------------|----------|--------------------|
| `core` | — | `AuthInterceptor` (attaches JWT Bearer header), `AuthGuard` (route guard, FS-01 §7/§10 UX-only) | Cross-cutting concerns |
| `auth` | `LoginComponent` | `AuthService` (login/logout calls, token storage) | FS-01 |
| `branches` | `BranchListComponent`, `BranchCreateComponent`, `BranchDetailComponent`, `CameraConfigFormComponent`, `ActivationKeyDisplayComponent`, `DeviceStatusBadgeComponent` | `BranchService`, `DeviceService` | FS-02 Increment A |
| `shared` | `EnvelopeErrorComponent` (generic API-error display) | `ApiEnvelopeService` (unwraps standard `{success, message, data}` envelope) | Cross-cutting |

## 4. Database Entities and Relationships

```
AdminUser (1) ── (many) AdminSession
Branch (1) ── (many) Camera
Branch (1) ── (1) Device
Device (1) ── (many, historically) ActivationKey     [only one Unconsumed record active at a time]
```

| Entity | Key Fields | Constraints |
|--------|------------|-------------|
| `AdminUser` | `UserId` (PK), `CredentialIdentifier`, `PasswordHash` | Unique index on `CredentialIdentifier` |
| `AdminSession` | `SessionId` (PK, `jti`), `UserId` (FK), `IssuedAt`, `ExpiresAt`, `Revoked` | FK → `AdminUser.UserId` |
| `Branch` | `BranchId` (PK), `Name`, `Address`, `ContactDetails` | — |
| `Camera` | `CameraId` (PK), `BranchId` (FK), `Name`, `RtspUrl`, `Enabled` | FK → `Branch.BranchId` |
| `Device` | `DeviceRecordId` (PK, internal), `DeviceId` (nullable, external), `BranchId` (FK), `ActivationStatus`, `ProtectedSharedSecret` (nullable), `LastKnownAddress` (nullable) | **Unique constraint** on `BranchId` (exactly one Device per Branch, BR-002/CON-007). **Filtered unique index** on `DeviceId` `WHERE DeviceId IS NOT NULL` (enforces external-identity uniqueness once assigned, without rejecting the many `NULL` rows that exist before activation). |
| `ActivationKey` | `ActivationKeyId` (PK — this is the externally-used, non-secret `keyId`), `DeviceRecordId` (FK), `SecretHash`, `Status` | FK → `Device.DeviceRecordId`. Non-clustered index on `(DeviceRecordId, Status)` to support "find the current Unconsumed key for this Device" during regeneration. `ActivationKeyId` itself is the primary/lookup key — no separate secondary index needed for the activation lookup path (FS-02 §1.4, AC-14). |

This directly implements FS-02 §1.3 (DeviceRecordId/DeviceId split) and §1.4 (two-part Activation Key with a separate, explicit `ActivationKey` entity rather than fields bolted onto `Device`).

## 5. Migration Sequence

Migrations are created only once their corresponding entities are fully mapped — no empty or placeholder migrations are authored.

| # | Migration | Adds |
|---|-----------|------|
| M1 | `InitialAdminSchema` | `AdminUser`, `AdminSession` (authored once both entities and their EF configurations exist — task T-03) |
| M2 | `BranchCameraSchema` | `Branch`, `Camera` (task T-11) |
| M3 | `DeviceAndActivationKeySchema` | `Device` (with the filtered unique index on `DeviceId` and the unique constraint on `BranchId`) and `ActivationKey` (task T-12) |

No data-seed migration is used for the Admin account (see §6 — Admin bootstrap is an application-startup process, not a migration).

## 6. Admin Bootstrap (Replaces Migration-Based Seeding)

The single `AdminUser` account is provisioned by an idempotent process that runs at application startup, not by a data migration:

1. On startup, a hosted service (`AdminBootstrapService`) checks whether any `AdminUser` row exists.
2. If one already exists, the service does nothing further — it never overwrites or modifies an existing Admin account automatically.
3. If none exists, the service reads a credential identifier and a bootstrap password from environment variables (e.g., `ADMIN_BOOTSTRAP_IDENTIFIER`, `ADMIN_BOOTSTRAP_PASSWORD`) or .NET user-secrets in development.
4. If provisioning is required (no Admin exists) but either value is absent, the service fails startup with a clear, actionable error — the application does not start in a state with no usable Admin account and no way to create one.
5. If both values are present, the service hashes the password using the same password-hashing utility as login (§10, T-04) and persists a single `AdminUser` row.
6. The plaintext bootstrap password is never logged and never persisted anywhere except as its hash.

This keeps EF migrations deterministic (schema-only) and keeps the Admin credential out of source control and out of migration history.

## 7. Device Shared-Secret Protection

An abstraction, `IDeviceSecretProtector`, decouples the Application layer from the specific protection mechanism:

```
IDeviceSecretProtector
    string Protect(string plaintextSecret)
    string Unprotect(string protectedSecret)
```

- Implemented in `Infrastructure` using ASP.NET Core Data Protection (`IDataProtector`) for this prototype (ARCH-001 §13.3 — recoverable-but-protected storage, not one-way hashing, because the Backend must present the plaintext secret again when authenticating outbound commands to the Agent).
- SQL Server's `Device.ProtectedSharedSecret` column stores only the protected (encrypted) form; the plaintext secret exists only transiently in memory during issuance and during outbound command authentication.
- The device shared secret is never logged, at any layer.
- Data Protection keys are persisted locally on the Server Host (file-system key ring, the ASP.NET Core default), consistent with the prototype's single-host deployment (ARCH-001 §12.1, ARCH-ASM-001). Enterprise-grade key management/rotation/HSM integration is explicitly future work (ARCH-001 §28.2), not part of this plan.

This is distinct from the Activation Key's `secret` portion and the Admin password, both of which use one-way hashing (§8) because they are never re-presented in plaintext by the Backend.

## 8. Activation Key Implementation

Directly implements FS-02 §1.4 and §5.5–§5.7:

1. Generation: produce a random `keyId` (used as `ActivationKey.ActivationKeyId`) and a separately random `secret`. Concatenate as `keyId.secret` for the single plaintext response.
2. Storage: persist only `ActivationKeyId` and a salted adaptive hash of `secret` (via the same hashing utility family as passwords, §10 T-04, parameterized separately). The complete plaintext key and the plaintext `secret` are never persisted.
3. Lookup: on `POST /api/v1/activate`, split the presented value into `keyId`/`secret`; reject malformed input immediately (no lookup attempted). Resolve the `ActivationKey` row by its primary key `ActivationKeyId = keyId` — a direct indexed lookup, never a scan or comparison across all stored hashes (AC-14).
4. Verification: use the hashing service's verify function to check `secret` against `SecretHash`.
5. Status check: confirm `Status == Unconsumed`.
6. Consumption: within a single database transaction — set `Status = Consumed`; assign/retain `Device.DeviceId`; set `Device.ActivationStatus = Activated`; generate a new shared secret, protect it via `IDeviceSecretProtector`, and store it as `Device.ProtectedSharedSecret`. This transaction is the same one referenced in §10/T-18 and is what guarantees AC-16 (concurrent-activation safety).

## 9. SQL Server Integration Testing

Integration tests that claim to verify relational/transactional behavior run against a real SQL Server-compatible test database (a disposable container or a dedicated local test database) — not against EF Core InMemory or SQLite, which do not enforce SQL Server's locking, isolation, and constraint behavior faithfully. This applies specifically to:

- Migrations actually applying cleanly (M1–M3).
- The filtered unique index on `Device.DeviceId` and the unique constraint on `Device.BranchId`.
- Transactional atomicity of Activation Key consumption (§8, step 6).
- Concurrent Activation Key consumption (AC-16) — two simultaneous requests against the same key, verified against real SQL Server locking behavior, not an in-memory provider's simplified semantics.
- Reactivation (Device ID retention across a second transaction).
- Shared-secret rotation (old protected secret replaced, verified via `IDeviceSecretProtector.Unprotect`).

EF Core InMemory or SQLite may be used only for fast tests that do not claim to verify SQL Server-specific relational behavior (e.g., a service-layer test that mocks the repository/`DbContext` entirely, or a pure validation-logic test with no real persistence claim).

## 10. API Endpoints

| Endpoint | Method | Auth | Feature Spec Reference |
|----------|--------|------|---------------------------|
| `/api/v1/auth/login` | POST | None | FS-01 §9.1 |
| `/api/v1/auth/logout` | POST | JWT + session | FS-01 §9.2 |
| `/api/v1/branches` | POST | JWT + session | FS-02 §10.1 |
| `/api/v1/branches` | GET | JWT + session | FS-02 §10.3 |
| `/api/v1/branches/{id}` | GET | JWT + session | FS-02 §10.3 |
| `/api/v1/devices/{id}` | GET | JWT + session | FS-02 §10.3 |
| `/api/v1/devices/{id}/activation-key/regenerate` | POST | JWT + session | FS-02 §10.2 (exact route decided here, per FS-02 §19 open item) |
| `/api/v1/activate` | POST | None for Admin-JWT purposes only — the endpoint still performs full request validation, Activation Key validation, standard error handling, and uses the standard response envelope (FS-02 §10.4) | FS-02 §10.4 |

## 11. DTOs and Validation Boundaries

| DTO | Direction | Validation Boundary |
|-----|-----------|------------------------|
| `LoginRequestDto { credentialIdentifier, password }` | Inbound | Both fields required; no format validation on `credentialIdentifier` beyond non-empty (FS-01 §11) |
| `LoginResponseDto { token }` | Outbound | — |
| `CreateBranchRequestDto { name, address, contactDetails, cameras: CameraConfigDto[] }` | Inbound | Name/address/contactDetails required; at least one camera; each camera requires name + valid RTSP URL (FS-02 §12) |
| `CameraConfigDto { name, rtspUrl }` | Inbound (nested) | RTSP URL format-checked at Application layer, not Domain |
| `BranchResponseDto { branchId, name, address, contactDetails, cameras[], device: DeviceSummaryDto, activationKey? }` | Outbound | `activationKey` present only in the create/regenerate response, never in list/detail (FS-02 §7); `DeviceRecordId` never present in any DTO (FS-02 §1.3) |
| `DeviceSummaryDto { deviceId?, activationStatus, lastKnownAddress? }` | Outbound | `deviceId` populated only once activated; internal `DeviceRecordId` never mapped into this DTO |
| `ActivateRequestDto { activationKey }` | Inbound | Non-empty required, and must parse into `keyId`/`secret`, before any hash lookup (FS-02 §12) |
| `ActivateResponseDto { deviceId, sharedSecret, configuration }` | Outbound | Returned only on successful validation |

All controller responses are wrapped in the uniform envelope (`{success, message, data}` / `{success, message, errorCode}`) by a shared API middleware/filter, not hand-built per endpoint — this keeps ADR-009 compliance centralized and consistent, including for `POST /api/v1/activate`, which is exempt only from JWT authentication, not from the envelope or from standard validation/error handling (FS-02 §10.4).

## 12. Authentication Middleware Flow

1. Request arrives at the Backend.
2. If the route is `/api/v1/auth/login` or `/api/v1/activate`, skip JWT middleware entirely (FS-01 §6, FS-02 §11). Both routes still pass through the standard request-validation and response-envelope pipeline.
3. Otherwise, ASP.NET Core JWT Bearer middleware validates signature and expiry. Failure → 401, short-circuit before any controller code runs.
4. A custom authorization requirement/handler resolves the JWT's `jti` claim against `AdminSession`; missing `jti`, no matching session, or `Revoked == true` → 401, short-circuit (FS-01 §5.3, §11).
5. Only on passing both checks does the request reach the target controller action.
6. Logout endpoint: requires the same two checks to pass (it is itself a protected endpoint, per FS-01 §9.2), then marks the session `Revoked = true`.

This is implemented as ASP.NET Core middleware/authorization-handler composition — no custom reinvention of JWT validation itself.

## 13. Unit Tests

| Target | Cases |
|--------|-------|
| `AuthService` | Valid login issues token + session; invalid credentials rejected; logout revokes session; already-revoked session rejected on reuse |
| JWT/session validation logic | Missing `jti` rejected; `jti` with no session rejected; mismatched user/session rejected; expired token rejected independent of revocation |
| `BranchService` | Valid branch-creation payload creates Branch+Cameras+Device; missing required field rejected before persistence |
| `DeviceService` (Activation Key) | Key generation produces a unique `keyId` and a hashed-at-rest `secret`; regeneration invalidates the previous Activation Key record; activation with valid unconsumed key succeeds and assigns `DeviceId`; activation with malformed/unknown-`keyId`/wrong-secret/consumed/invalidated key rejected identically where FS-02 requires it; reactivation retains `DeviceId` and rotates the shared secret |
| `IDeviceSecretProtector` (Data Protection implementation) | Protect/Unprotect round-trips correctly; protected value is not the plaintext |
| Password/Activation-Key hashing utility | Hash is non-reversible in test (no plaintext round-trip); verify function correctly accepts/rejects |
| `AdminBootstrapService` | Creates Admin when none exists and both env values present; does nothing when an Admin already exists; fails startup clearly when provisioning is required but values are missing |

## 14. Integration Tests (Against Real SQL Server, Per §9)

| Scenario | Verifies |
|----------|----------|
| POST `/api/v1/auth/login` with valid/invalid credentials | FS-01 AC-1, AC-2 |
| Protected endpoint call with no token / expired token / revoked session | FS-01 AC-3, AC-7 |
| POST `/api/v1/auth/logout` then reuse of the same token | FS-01 AC-5, AC-6 |
| Second logout attempt on an already-revoked token | FS-01 §14 T-12 |
| POST `/api/v1/branches` full payload → 201 with Branch/Camera/Device created, `DeviceId = null`, Activation Key present once | FS-02 AC-1, AC-2 |
| POST `/api/v1/branches` incomplete payload → 400 | FS-02 §13 |
| Attempt to create a second Device for the same Branch directly at the persistence layer → unique constraint violation | FS-02 §1.3 (schema-level enforcement) |
| POST `/api/v1/devices/{id}/activation-key/regenerate` → old Activation Key record invalidated, new `keyId`/`secret` returned | FS-02 AC-5 |
| GET branch/device list/detail → Activation Key and `DeviceRecordId` never present | FS-02 §7, §1.3 |
| POST `/api/v1/activate` with valid, malformed, unknown-`keyId`, wrong-secret, consumed, and invalidated keys | FS-02 AC-3, AC-4, AC-9, AC-15 |
| Reactivation flow: regenerate → activate → `DeviceId` retained, shared secret rotated | FS-02 AC-7, AC-12 |
| Two concurrent activation requests against the same valid, unconsumed key, run against real SQL Server | FS-02 AC-16 |

## 15. Angular Tests

| Target | Cases |
|--------|-------|
| `AuthService` | Login success stores token; login failure surfaces error; logout clears token regardless of backend response outcome (FS-01 §7) |
| `AuthInterceptor` | Attaches Bearer header when a token is present; does not attach on login request |
| `AuthGuard` | Blocks navigation to a protected route with no token; allows navigation with a token (explicitly noted in tests as a UX check only, not a security boundary — FS-01 §10) |
| `LoginComponent` | Renders form; submits credentials; displays error on failure |
| `BranchCreateComponent` / `CameraConfigFormComponent` | Form validation (required fields, at least one camera); submits correct payload shape |
| `ActivationKeyDisplayComponent` | Displays the key exactly once per response; does not persist it beyond the current view state |
| `BranchListComponent` / `BranchDetailComponent` | Renders branch/camera/device data; renders device activation status badge correctly for both states; never attempts to render a `deviceId` for an unactivated device |

## 16. Implementation Tasks (Dependency Order)

Each task is sized to be implemented and committed independently, consistent with Engineering Principle 3. Each task lists the FS acceptance criteria it serves.

---

**T-01 — Backend solution and project scaffolding**
- Objective: Create the `.sln` and four backend projects (Api, Application, Domain, Infrastructure) with project references wired per §2, plus UnitTests/IntegrationTests projects.
- Files/Components: `backend/WeaponDetection.sln`, `backend/src/*/*.csproj`, `backend/tests/*/*.csproj`
- Dependencies: None
- Serves FS Acceptance Criteria: None directly (infrastructure only)
- Expected Output: Solution builds; `Api` runs and serves a placeholder health-check route
- Tests: None (scaffolding only)
- Documentation Update: `README.md` — how to build/run the Backend
- Completion Evidence: `dotnet build` succeeds; `dotnet run --project Api` starts and responds to the placeholder route

**T-02 — SQL Server + EF Core integration**
- Objective: Wire `DbContext`, connection string configuration, and EF Core tooling into `Infrastructure`/`Api`. No entities yet — no migration is created in this task.
- Files/Components: `Infrastructure/Persistence/AppDbContext.cs`, `Api/appsettings.json` (connection string placeholder, no secrets committed)
- Dependencies: T-01
- Serves FS Acceptance Criteria: None directly (infrastructure only)
- Expected Output: `DbContext` resolves via DI; connects to a real SQL Server instance
- Tests: None yet (no entities)
- Documentation Update: README — local SQL Server setup instructions
- Completion Evidence: Application starts and `DbContext` can open a connection (verified via a trivial connectivity check, not a migration)

**T-03 — AdminUser and AdminSession entities + M1 migration**
- Objective: Implement `AdminUser`/`AdminSession` Domain entities and EF configuration; author migration M1 now that both entities are fully mapped.
- Files/Components: `Domain/AdminUser.cs`, `Domain/AdminSession.cs`, `Infrastructure/Persistence/Configurations/*`, migration M1
- Dependencies: T-02
- Serves FS Acceptance Criteria: FS-01 (schema prerequisite for AC-1–AC-7)
- Expected Output: Tables created in the test database, including the unique index on `CredentialIdentifier`
- Tests: None yet (no business logic)
- Documentation Update: none (schema documented in FS-01 §8)
- Completion Evidence: `dotnet ef database update` creates both tables against a real SQL Server test database

**T-04 — Password hashing utility**
- Objective: Implement salted adaptive password hashing (hash + verify functions) in `Infrastructure`, reusable for both Admin passwords and Activation Key secrets (parameterized separately per §8).
- Files/Components: `Infrastructure/Security/AdaptiveHasher.cs`
- Dependencies: T-01
- Serves FS Acceptance Criteria: FS-01 (§10, security rules); FS-02 AC-14
- Expected Output: Reusable hash/verify utility
- Tests: Unit — hash is non-reversible via equality; verify succeeds for correct input, fails for incorrect input
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-05 — IDeviceSecretProtector (Data Protection)**
- Objective: Implement the `IDeviceSecretProtector` abstraction (§7) using ASP.NET Core Data Protection.
- Files/Components: `Application/Interfaces/IDeviceSecretProtector.cs`, `Infrastructure/Security/DataProtectionDeviceSecretProtector.cs`
- Dependencies: T-01
- Serves FS Acceptance Criteria: FS-02 (§11 security rules — protected shared-secret storage)
- Expected Output: `Protect`/`Unprotect` round-trip correctly; protected value is not the plaintext
- Tests: Unit — round-trip correctness; protected output differs from plaintext input
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-06 — Admin bootstrap service**
- Objective: Implement the idempotent startup bootstrap process described in §6.
- Files/Components: `Application/Services/AdminBootstrapService.cs`, `Api/Program.cs` (hosted-service registration)
- Dependencies: T-03, T-04
- Serves FS Acceptance Criteria: FS-01 (precondition — provisions the single AdminUser, §3)
- Expected Output: On first run with env values set, one `AdminUser` row is created; on a second run, no duplicate/overwrite occurs; on a first run with values missing, startup fails with a clear error
- Tests: Unit — all three branches (create / no-op / fail-fast)
- Documentation Update: README — required environment variables for first run
- Completion Evidence: Unit tests pass; manual run against a fresh database demonstrates all three behaviors

**T-07 — JWT issuance service**
- Objective: Implement JWT creation with `jti` and expiry claims.
- Files/Components: `Infrastructure/Security/JwtIssuer.cs`, `Application/Interfaces/IJwtIssuer.cs`
- Dependencies: T-01
- Serves FS Acceptance Criteria: FS-01 AC-1
- Expected Output: Given a UserId, produces a signed JWT string with a fresh `jti`
- Tests: Unit — token contains expected claims; expiry set correctly
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-08 — AuthService (login)**
- Objective: Implement `AuthService.Login(credentialIdentifier, password)`: verify against `AdminUser`, issue JWT via T-07, create `AdminSession` row.
- Files/Components: `Application/Services/AuthService.cs`
- Dependencies: T-04, T-06, T-07
- Serves FS Acceptance Criteria: FS-01 AC-1, AC-2
- Expected Output: Successful login returns a token; failed login returns a domain-level failure result
- Tests: Unit — success path creates session + returns token; failure path creates no session
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-09 — AuthController: POST /api/v1/auth/login**
- Objective: Expose `AuthService.Login` over HTTP with the standard response envelope; route explicitly excluded from JWT middleware.
- Files/Components: `Api/Controllers/AuthController.cs`, envelope middleware/filter (introduced here, reused by all subsequent controllers)
- Dependencies: T-08
- Serves FS Acceptance Criteria: FS-01 AC-1, AC-2
- Expected Output: `POST /api/v1/auth/login` returns 200 + token on success, 401 on failure, both enveloped
- Tests: Integration (real SQL Server) — FS-01 AC-1, AC-2
- Documentation Update: none (API contract already in FS-01 §9.1)
- Completion Evidence: Integration tests pass; manual `curl`/Postman call succeeds

**T-10 — JWT + session-revocation authentication middleware**
- Objective: Wire ASP.NET Core JWT Bearer authentication plus a custom authorization requirement checking `AdminSession.Revoked`/existence, applied to all controllers except login and activate.
- Files/Components: `Api/Program.cs`, `Api/Security/SessionAuthorizationHandler.cs`
- Dependencies: T-03, T-09
- Serves FS Acceptance Criteria: FS-01 AC-3, AC-7
- Expected Output: Any endpoint marked `[Authorize]` rejects missing/expired/revoked tokens before reaching controller logic
- Tests: Integration (real SQL Server) — FS-01 AC-3, AC-7, and the missing-`jti`/mismatched-session cases from FS-01 §11/§14
- Documentation Update: none
- Completion Evidence: Integration tests pass, including a placeholder `[Authorize]` test endpoint

**T-11 — AuthService/Controller: logout**
- Objective: Implement session revocation on `POST /api/v1/auth/logout`, including idempotent second-logout rejection.
- Files/Components: `Application/Services/AuthService.cs` (extend), `Api/Controllers/AuthController.cs` (extend)
- Dependencies: T-10
- Serves FS Acceptance Criteria: FS-01 AC-5, AC-6
- Expected Output: First logout revokes and returns success; reuse of the revoked token is rejected (401); second logout attempt is rejected (401), no new session created
- Tests: Integration (real SQL Server) — FS-01 AC-5, AC-6, §14 T-06/T-10/T-11/T-12
- Documentation Update: none
- Completion Evidence: Integration tests pass

**T-12 — Branch and Camera entities + M2 migration**
- Objective: Implement `Branch`/`Camera` Domain entities and EF configuration; author migration M2.
- Files/Components: `Domain/Branch.cs`, `Domain/Camera.cs`, EF configurations, migration M2
- Dependencies: T-02
- Serves FS Acceptance Criteria: FS-02 (schema prerequisite for AC-1)
- Expected Output: Tables created, one-to-many relationship enforced at schema level
- Tests: None yet
- Documentation Update: none (schema already documented in FS-02 §9)
- Completion Evidence: `dotnet ef database update` succeeds against a real SQL Server test database

**T-13 — Device and ActivationKey entities + M3 migration**
- Objective: Implement `Device` (with `DeviceRecordId`/`DeviceId` split, per FS-02 §1.3) and `ActivationKey` (per FS-02 §1.4) Domain entities and EF configuration; author migration M3, including the unique constraint on `Device.BranchId` and the filtered unique index on `Device.DeviceId WHERE DeviceId IS NOT NULL`.
- Files/Components: `Domain/Device.cs`, `Domain/ActivationKey.cs`, EF configurations, migration M3
- Dependencies: T-12
- Serves FS Acceptance Criteria: FS-02 (schema prerequisite for AC-1, AC-7, AC-14)
- Expected Output: Tables created; one Device per Branch enforced by a unique index; `DeviceId` uniqueness enforced only among non-null values
- Tests: None yet
- Documentation Update: none
- Completion Evidence: `dotnet ef database update` succeeds against a real SQL Server test database; attempting a second Device row for the same `BranchId` fails at the database level; inserting two Device rows with `DeviceId = NULL` succeeds (proving the filtered index, not a naive unique index, is in place)

**T-14 — Activation Key generation and hashing utility**
- Objective: Implement Activation Key generation (`keyId` + `secret`) and hashing/verification of the `secret` portion, per §8 steps 1–2 and 4.
- Files/Components: `Infrastructure/Security/ActivationKeyGenerator.cs`
- Dependencies: T-04
- Serves FS Acceptance Criteria: FS-02 AC-2, AC-14
- Expected Output: Generates a unique `keyId` and `secret`; hashes the `secret`; verifies a presented `secret` against a stored hash
- Tests: Unit — generated `keyId`/`secret` pairs are unique across many invocations; verify succeeds/fails correctly
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-15 — BranchService/DeviceService: branch creation with Device + Activation Key (transactional)**
- Objective: Implement `BranchService.CreateBranch(...)`: persist Branch+Cameras+unactivated Device+Activation Key record as a single database transaction; return the plaintext `keyId.secret` once.
- Files/Components: `Application/Services/BranchService.cs`, `Application/Services/DeviceService.cs`
- Dependencies: T-13, T-14
- Serves FS Acceptance Criteria: FS-02 AC-1, AC-2
- Expected Output: One call produces Branch, Camera(s), Device (`DeviceId = null`, unactivated), and an Activation Key record, all within one transaction — a failure partway through leaves no partial rows
- Tests: Unit — FS-02 AC-1, AC-2; missing-field rejection before persistence; transactional rollback on a simulated failure
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-16 — BranchController: POST/GET /api/v1/branches, GET /api/v1/branches/{id}, GET /api/v1/devices/{id}**
- Objective: Expose branch creation and branch/device read endpoints over HTTP, protected by T-10's middleware.
- Files/Components: `Api/Controllers/BranchController.cs`, `Api/Controllers/DeviceController.cs`, DTOs per §11
- Dependencies: T-10, T-15
- Serves FS Acceptance Criteria: FS-02 AC-1, AC-2
- Expected Output: Full branch-creation and read flow reachable over HTTP with the standard envelope; `DeviceRecordId` never serialized
- Tests: Integration (real SQL Server) — FS-02 AC-1, AC-2, §13 error cases, §7 (Activation Key and `DeviceRecordId` never present in read responses)
- Documentation Update: none
- Completion Evidence: Integration tests pass; manual Postman walkthrough succeeds

**T-17 — DeviceService: Activation Key regeneration (transactional)**
- Objective: Implement regeneration logic: invalidate the previous Activation Key record regardless of consumption state, generate and store a new `keyId`/`secret`, within a single transaction.
- Files/Components: `Application/Services/DeviceService.cs` (extend)
- Dependencies: T-15
- Serves FS Acceptance Criteria: FS-02 AC-5
- Expected Output: Regeneration call invalidates old key, returns new plaintext key; both the invalidation and the new-record creation succeed or fail together
- Tests: Unit — FS-02 AC-5
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-18 — DeviceController: POST /api/v1/devices/{id}/activation-key/regenerate**
- Objective: Expose regeneration over HTTP, protected by T-10's middleware.
- Files/Components: `Api/Controllers/DeviceController.cs` (extend)
- Dependencies: T-10, T-17
- Serves FS Acceptance Criteria: FS-02 AC-5
- Expected Output: Endpoint reachable, returns new plaintext key once
- Tests: Integration (real SQL Server) — FS-02 AC-5, §15 T-03
- Documentation Update: none
- Completion Evidence: Integration tests pass

**T-19 — DeviceService: activation validation and consumption (transactional)**
- Objective: Implement the core activation logic per §8 steps 3–6 — parse, look up by indexed `keyId`, verify secret, confirm status, then atomically (single transaction) consume the key, assign/retain `DeviceId`, mark activated, and issue a protected shared secret via `IDeviceSecretProtector` (T-05).
- Files/Components: `Application/Services/DeviceService.cs` (extend)
- Dependencies: T-05, T-14, T-15
- Serves FS Acceptance Criteria: FS-02 AC-3, AC-4, AC-7, AC-9, AC-12, AC-14, AC-15, AC-16
- Expected Output: Given a plaintext key, returns success (`DeviceId`, secret, config) or a typed rejection reason (malformed / unknown-`keyId` / wrong-secret / consumed / invalidated), with the last three of those five failure types collapsed to an identical externally observable outcome per FS-02 §5.6
- Tests: Unit — all rejection paths from FS-02 §13; `DeviceId` retention on reactivation; concurrency behavior simulated at the unit level (full verification deferred to T-22's SQL Server integration test)
- Documentation Update: none
- Completion Evidence: Unit tests pass

**T-20 — ActivateController: POST /api/v1/activate**
- Objective: Expose T-19's logic over HTTP; route explicitly excluded from Admin JWT middleware (authenticated by the Activation Key itself) but still passes through standard request validation, error handling, and the response envelope (FS-02 §10.4).
- Files/Components: `Api/Controllers/ActivateController.cs`
- Dependencies: T-10 (to confirm exclusion), T-19
- Serves FS Acceptance Criteria: FS-02 AC-3, AC-4, AC-9, AC-12, AC-15
- Expected Output: Endpoint reachable without a JWT, behaves per FS-02 §10.4, using the same envelope/error pipeline as every other endpoint
- Tests: Integration (real SQL Server) — FS-02 AC-3, AC-4, AC-9, AC-12, AC-15, §15 T-07–T-13 (activation and reactivation sequences, run against this endpoint directly with a test HTTP client standing in for an Agent)
- Documentation Update: none
- Completion Evidence: Integration tests pass, including a full activate → regenerate → reactivate sequence

**T-21 — Concurrent activation integration test (real SQL Server)**
- Objective: Author a dedicated integration test issuing two simultaneous `POST /api/v1/activate` requests against the same valid, unconsumed key, run against a real SQL Server test database, to verify the transactional guarantee from T-19/§8 step 6 under genuine concurrency (not simulated in-process locking).
- Files/Components: `IntegrationTests/Activation/ConcurrentActivationTests.cs`
- Dependencies: T-20
- Serves FS Acceptance Criteria: FS-02 AC-16
- Expected Output: Exactly one of the two concurrent requests succeeds; the other receives the same rejection as a consumed-key reuse; no two `DeviceId`s are issued from the one key
- Tests: This task is itself the test
- Documentation Update: none
- Completion Evidence: Test passes reliably across repeated runs (not flaky) against the real SQL Server test database

**T-22 — Angular workspace scaffold and core module**
- Objective: Scaffold the Angular application with `core`, `auth`, `branches`, `shared` module boundaries per §3.
- Files/Components: `frontend/` (new Angular workspace)
- Dependencies: None (parallel to Backend tasks, but functionally needs T-09 onward to integrate)
- Serves FS Acceptance Criteria: None directly (scaffolding)
- Expected Output: Application builds and serves a placeholder shell
- Tests: None (scaffolding)
- Documentation Update: README — how to build/run the Angular app
- Completion Evidence: `ng build` succeeds; `ng serve` renders a placeholder page

**T-23 — AuthService (Angular) and login view**
- Objective: Implement `AuthService.login()/logout()` and `LoginComponent`, calling the real Backend endpoints from T-09/T-11.
- Files/Components: `frontend/src/app/auth/*`
- Dependencies: T-09, T-22
- Serves FS Acceptance Criteria: FS-01 AC-1, AC-2
- Expected Output: A user can log in through the UI and receive/store a token
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Manual login against a running Backend succeeds; unit tests pass

**T-24 — HTTP interceptor and route guard**
- Objective: Implement `AuthInterceptor` (attach Bearer token) and `AuthGuard` (UX-only redirect), explicitly documented as non-authoritative per FS-01 §10.
- Files/Components: `frontend/src/app/core/*`
- Dependencies: T-23
- Serves FS Acceptance Criteria: FS-01 AC-3, AC-7
- Expected Output: Protected API calls carry the token automatically; direct navigation to a protected route without a token redirects to login
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual route-guard-bypass-then-API-call reproduces FS-01 T-13 (Backend still rejects)

**T-25 — Logout wiring and session-expiry handling**
- Objective: Wire the logout action and a global HTTP-error handler that treats any 401 as a logged-out state (FS-01 §7).
- Files/Components: `frontend/src/app/core/*`, `frontend/src/app/auth/*`
- Dependencies: T-24
- Serves FS Acceptance Criteria: FS-01 AC-5, AC-6
- Expected Output: Logout clears local state regardless of Backend response outcome; a 401 from any call redirects to login
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual logout-then-reuse-token walkthrough matches FS-01 behavior

**T-26 — Branch list and detail views**
- Objective: Implement `BranchListComponent`/`BranchDetailComponent` and `BranchService` (Angular), consuming T-16's endpoints.
- Files/Components: `frontend/src/app/branches/*`
- Dependencies: T-16, T-25
- Serves FS Acceptance Criteria: FS-02 AC-1, AC-7 (display of `deviceId` only once activated)
- Expected Output: Admin can view branches and drill into a branch's detail, including camera list and device activation status
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual walkthrough against a running Backend

**T-27 — Branch create form + camera configuration UI + Activation Key display**
- Objective: Implement `BranchCreateComponent`, `CameraConfigFormComponent`, `ActivationKeyDisplayComponent`, consuming T-16's create endpoint.
- Files/Components: `frontend/src/app/branches/*`
- Dependencies: T-26
- Serves FS Acceptance Criteria: FS-02 AC-1, AC-2
- Expected Output: Admin can create a branch with one or more cameras and sees the plaintext Activation Key exactly once
- Tests: Angular unit — per §15, including a test asserting the key is not retrievable again after navigating away
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual walkthrough matches FS-02 §15 T-01/T-05

**T-28 — Activation Key regeneration UI**
- Objective: Add a regenerate action to `BranchDetailComponent`, consuming T-18's endpoint, reusing `ActivationKeyDisplayComponent`.
- Files/Components: `frontend/src/app/branches/*`
- Dependencies: T-27, T-18
- Serves FS Acceptance Criteria: FS-02 AC-5
- Expected Output: Admin can regenerate a branch's key and sees the new plaintext key once
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual walkthrough matches FS-02 §15 T-03

**T-29 — Device activation-status badge**
- Objective: Add `DeviceStatusBadgeComponent` reflecting Unactivated/Activated state, and `deviceId` display only when activated, on list and detail views.
- Files/Components: `frontend/src/app/branches/*`
- Dependencies: T-26
- Serves FS Acceptance Criteria: FS-02 AC-7
- Expected Output: Status badge renders correctly for both states; `deviceId` never rendered for an unactivated Device
- Tests: Angular unit — per §15
- Documentation Update: none
- Completion Evidence: Unit tests pass; manual verification by directly activating a Device via T-20's endpoint (using a test HTTP client, not yet a real/simulated Agent) and observing the badge and `deviceId` appear on refresh

**T-30 — Full integration test suite pass (real SQL Server) and milestone documentation update**
- Objective: Run the complete Backend integration suite (§14, against real SQL Server per §9) and Angular unit suite (§15) together; update `README.md`/relevant docs to reflect the delivered milestone.
- Files/Components: `backend/tests/*`, `frontend/src/app/**/*.spec.ts`, `README.md`
- Dependencies: All prior tasks
- Serves FS Acceptance Criteria: All FS-01 and FS-02-Increment-A acceptance criteria (full-suite confirmation)
- Expected Output: Green test run across both stacks
- Tests: Full suite execution (no new tests authored)
- Documentation Update: README updated per Engineering Principle 10 ("every completed feature updates its documentation")
- Completion Evidence: CI or local full-suite run report; updated README committed

---

## 17. Definition of Done for the Milestone

The "Backend and Angular Foundation" milestone is done when:

1. All tasks T-01 through T-30 are complete, each independently committed.
2. Every acceptance criterion in FS-01 (AC-1–AC-7) and the Increment-A-applicable acceptance criteria in FS-02 (AC-1, AC-2, AC-5, AC-6, AC-8, AC-14, and the Increment-A-testable portions of AC-3/AC-4/AC-7/AC-9/AC-12/AC-15/AC-16 per FS-02 §14) is demonstrated by a passing automated test.
3. The manual demonstration script (§18) can be executed end-to-end against a locally running Backend + Angular app without manual data manipulation (e.g., no direct SQL edits required).
4. No plaintext Activation Key, Activation Key secret, device shared secret, Admin password, or JWT appears in any committed file, log output, or source-controlled configuration.
5. `POST /api/v1/activate` is fully functional and independently callable by any HTTP client — proving Increment B (Agent/simulator) will need no further Backend changes to begin.
6. The concurrent-activation guarantee (AC-16) is demonstrated against a real SQL Server test database, not an in-memory provider.
7. The Admin account is provisioned only via the startup bootstrap process (§6); no migration seeds it.
8. No SRS, ARCH-001, FS-01, or FS-02 content has been modified as a byproduct of implementation; any discovered ambiguity is raised for clarification rather than silently resolved in code.

## 18. Manual Demonstration Script

1. Set `ADMIN_BOOTSTRAP_IDENTIFIER` and `ADMIN_BOOTSTRAP_PASSWORD` environment variables. Start the Backend (`dotnet run --project Api`) against a fresh database — confirm the Admin account is created on this first run.
2. Restart the Backend — confirm no duplicate Admin is created and no error occurs.
3. Start the Angular app (`ng serve`). Navigate to it; confirm it redirects to the login view (no stored session).
4. Log in with the bootstrapped Admin credential → confirm redirect to the branch list (empty state).
5. Attempt to open a protected route by directly editing the URL before logging in (in a fresh/incognito session) → confirm redirect to login.
6. Log in again. Create a branch with one camera → confirm the complete plaintext Activation Key (`keyId.secret`) is displayed exactly once, and the branch appears in the list with its Device shown as "Unactivated" (no Device ID shown).
7. Navigate away from the branch and back → confirm the Activation Key is no longer displayed (only the branch/camera/device summary).
8. Using an HTTP client (e.g., Postman/curl, standing in for a future Agent), call `POST /api/v1/activate` with the displayed key → confirm a 200 response containing a `DeviceId`, shared secret, and configuration.
9. Refresh the branch detail view in the Dashboard → confirm the Device now shows "Activated" along with its `DeviceId`.
10. Repeat the same `POST /api/v1/activate` call with the same (now-consumed) key → confirm a 401 rejection.
11. Call `POST /api/v1/activate` with a deliberately malformed key (no delimiter) and with a correct `keyId` but wrong `secret` → confirm both are rejected (401) with no observable difference from each other.
12. In the Dashboard, regenerate the branch's Activation Key → confirm a new plaintext key is displayed once, and confirm (via the HTTP client) that the old key now fails activation while the new key succeeds and retains the same `DeviceId`, with a different shared secret than step 8 returned.
13. Issue two near-simultaneous `POST /api/v1/activate` calls against the same valid key (a scripted concurrent request, not a manual double-click) → confirm exactly one succeeds.
14. Log out from the Dashboard → confirm redirect to login, and confirm (via the HTTP client, reusing the captured JWT) that the previously valid token is now rejected (401) on any protected endpoint.
15. Attempt to log out a second time using the same (already-revoked) token via the HTTP client → confirm 401, and confirm no new session was created.

A successful run of steps 1–15 constitutes a live demonstration of the milestone, using only real, production API contracts — with no simulator- or mock-only paths — directly satisfying the Backend/Angular-first delivery strategy's stated purpose of validating the Agent-facing contract before any Agent (real or simulated) exists.
