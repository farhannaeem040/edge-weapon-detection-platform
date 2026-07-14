# Edge-Based Weapon Detection and Centralized Monitoring System

## Overview

This repository contains the source code and documentation for a Software Engineering dissertation focused on designing and implementing an edge-based weapon detection platform.

The platform combines AI-powered weapon detection running on NVIDIA Jetson devices with a centralized web application for monitoring, configuration, and alert management.

The project follows a Spec-Driven Development workflow and is developed using Claude Code as an AI engineering assistant.

---

## Objectives

- Detect guns and knives using a fine-tuned YOLO26 model.
- Perform inference locally using NVIDIA DeepStream.
- Provide centralized monitoring through a web dashboard.
- Support branch registration and remote configuration.
- Monitor Jetson device health.
- Generate and manage weapon detection alerts.
- Demonstrate a complete end-to-end prototype.

---

## Technology Stack

### AI

- YOLO26
- NVIDIA DeepStream
- TensorRT

### Edge Device

- NVIDIA Jetson Orin Nano

### Backend

- ASP.NET Core

### Frontend

- Angular

### Database

- SQL Server

---

## Repository Structure

Documentation and specifications are located in the `docs` and `specs` directories.

Implementation source code is organized into independent modules for the server, dashboard, Jetson agent, AI pipeline, and supporting tools.

---

## Development Methodology

This project follows Spec-Driven Development.

Features are specified before implementation.

Claude Code is used to assist with planning, implementation, testing, and code review.

---

## Backend

### Prerequisites

- .NET SDK 10.0 or later

### Build

```
cd backend
dotnet build
```

### Local SQL Server Setup

The Backend requires a SQL Server-compatible database. Any of the following work:

**Option A — Docker (recommended for local development):**

```
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=<your-strong-password>" \
  -p 1433:1433 --name weapondetection-sql --hostname weapondetection-sql \
  -d mcr.microsoft.com/mssql/server:2022-latest
```

**Option B — a local SQL Server / SQL Server Express instance** already installed on the development machine.

Once a server is reachable, set the connection string via **user-secrets** (never commit it):

```
cd backend
dotnet user-secrets set "ConnectionStrings:DefaultConnection" "Server=localhost;Database=WeaponDetectionDev;Trusted_Connection=True;TrustServerCertificate=True;" --project src/WeaponDetection.Api
```

Or, for a SQL-authenticated instance (e.g., the Docker container above):

```
dotnet user-secrets set "ConnectionStrings:DefaultConnection" "Server=localhost,1433;Database=WeaponDetectionDev;User Id=sa;Password=<your-strong-password>;TrustServerCertificate=True;" --project src/WeaponDetection.Api
```

Alternatively, set the `ConnectionStrings__DefaultConnection` environment variable (double underscore), which ASP.NET Core's configuration system maps to the same key — this is the mechanism used in CI/non-developer environments. If neither is set, the API fails to start with a clear error identifying the missing configuration, rather than starting in a broken state.

No connection string, password, or secret is ever committed to source control (see `.gitignore`).

### Admin Bootstrap (Local Development)

On startup, the Backend provisions the single Admin account if none exists yet (`AdminBootstrapper`, `specs/implementation-plans/IP-01-backend-angular-foundation.md` task T-06) — no registration flow exists, and no migration seeds it. Set the bootstrap credential and password via **user-secrets** (never commit them):

```
cd backend
dotnet user-secrets set "BootstrapAdmin:CredentialIdentifier" "<admin-identifier>" --project src/WeaponDetection.Api
dotnet user-secrets set "BootstrapAdmin:Password" "<strong-password>" --project src/WeaponDetection.Api
```

Or, in non-development environments, set the equivalent double-underscore environment variables (ASP.NET Core's configuration system maps these to the same keys):

```
BootstrapAdmin__CredentialIdentifier
BootstrapAdmin__Password
```

Notes:

- These values are read **only** when no Admin account exists yet. Once the initial Admin account has been created, changing or removing these values has no effect — it does not reset the existing account's credential identifier or password.
- If no Admin exists and either value is missing or blank, the API fails to start with a clear error identifying the missing configuration key, rather than starting in a broken state.
- The plaintext bootstrap password is never logged, persisted, or committed — only its hash is stored (via the same password-hashing utility used at login).

### JWT Configuration (Local Development)

The Backend issues signed JWT access tokens (`JwtIssuer`, `specs/implementation-plans/IP-01-backend-angular-foundation.md` task T-07). Only the signing key is secret — the issuer, audience, and access-token lifetime have non-secret defaults committed in `appsettings.json`. Set the signing key via **user-secrets** (never commit it):

```
cd backend
dotnet user-secrets set "Jwt:SigningKey" "<strong-random-signing-key>" --project src/WeaponDetection.Api
```

The signing key must be at least 32 bytes (UTF-8 encoded) — a random 32+ character string is sufficient for this prototype's HMAC-SHA256 signing.

Or, in non-development environments, set the equivalent double-underscore environment variables (ASP.NET Core's configuration system maps these to the same keys):

```
Jwt__Issuer
Jwt__Audience
Jwt__SigningKey
Jwt__AccessTokenLifetimeMinutes
```

Notes:

- All four values (issuer, audience, signing key, lifetime) are validated at application startup — a missing, blank, or insufficiently strong value fails startup immediately with a clear error, rather than remaining dormant until the first token is issued.
- The same validated values are used both to sign tokens (`JwtIssuer`) and to verify them (the authentication middleware, task T-10), so the two can never drift apart.
- Revoking a session (logout) is implemented in a later task.
- No refresh-token flow exists in this prototype — a new login is required once a token expires.

### Login Endpoint

`POST /api/v1/auth/login` (`specs/implementation-plans/IP-01-backend-angular-foundation.md` task T-09) accepts the Admin credential identifier and password, and returns the standard response envelope with the signed access token on success:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:5230/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{
    "credentialIdentifier": "<admin-identifier>",
    "password": "<admin-password>"
  }'
```

Invalid credentials (unknown identifier or wrong password) return `401 Unauthorized` with the same generic error for both cases — the response never reveals which one was wrong.

### Protected Endpoints

Every endpoint is protected by Admin JWT authentication **by default** (`specs/implementation-plans/IP-01-backend-angular-foundation.md` task T-10). A protected request must present the access token from login as a Bearer token:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:5230/api/v1/<protected-route>" `
  -Headers @{ Authorization = "Bearer <access-token>" }
```

A request passes only if **both** checks succeed: the JWT's signature, issuer, audience, and expiry are valid, **and** the `AdminSession` record named by its `jti` claim exists, belongs to the same Admin, is unexpired, and has not been revoked. Discarding a token in the browser therefore does not invalidate it — only the server-side session record does (FS-01 §10).

Every failure — no `Authorization` header, a malformed or wrongly-signed token, an expired token, a token with no `jti`, an unknown or mismatched session, or a revoked session — returns `401 Unauthorized` with the same generic error envelope. The response never reveals which check failed.

Two routes are exempt, and each opts out explicitly with `[AllowAnonymous]`:

- `POST /api/v1/auth/login` — it is what issues a session, so it cannot require one (FS-01 §9.1).
- `GET /api/v1/health` — the temporary T-01 scaffolding probe, which exposes no data beyond a fixed `"Healthy"` literal.

`POST /api/v1/activate` will be added as a third exemption in a later task; it is authenticated by the Activation Key itself rather than by an Admin JWT (FS-02 §10.4). Any endpoint added without an authorization attribute is protected by the fallback policy, so a forgotten `[Authorize]` cannot silently expose a route.

### Run

```
cd backend
dotnet run --project src/WeaponDetection.Api
```

The API listens on the URL shown in the console output (see `src/WeaponDetection.Api/Properties/launchSettings.json` for the configured profile/port). A temporary health-check endpoint is available at:

```
GET /api/v1/health
```

This endpoint is scaffolding-verification only (`specs/implementation-plans/IP-01-backend-angular-foundation.md`, task T-01) and will be superseded by real health/status reporting in a later feature.

### Test

```
cd backend
dotnet test
```

Unit tests live in `tests/WeaponDetection.UnitTests`; integration tests live in `tests/WeaponDetection.IntegrationTests` (per IP-01 §9, integration tests that verify relational/transactional SQL Server behavior require a real SQL Server-compatible test database).

### EF Core Migrations and Design-Time Tooling

`dotnet-ef` is installed as a repo-local tool (see `backend/dotnet-tools.json`). Restore it once per clone:

```
cd backend
dotnet tool restore
```

Common commands (the `DbContext` lives in `Infrastructure`; `Api` is the startup project):

```
dotnet ef dbcontext info --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api
dotnet ef migrations add <Name> --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api
dotnet ef migrations list --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api
dotnet ef database update --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api
dotnet ef database update <PreviousMigrationName-or-0> --project src/WeaponDetection.Infrastructure --startup-project src/WeaponDetection.Api  # rollback
```

CLI discovery does not require a real database connection or the runtime `ConnectionStrings:DefaultConnection` value — `WeaponDetectionDbContextFactory` (an `IDesignTimeDbContextFactory`) supplies a design-time-only placeholder connection string (integrated/Windows authentication, no password) used solely to construct the `DbContext` for tooling purposes.

To apply migrations against your **real** local development database (rather than the design-time placeholder), set `EFCORE_DESIGNTIME_CONNECTION` to the same value as your `ConnectionStrings:DefaultConnection` user-secret before running `dotnet ef database update` — otherwise the design-time factory's placeholder connection is used, which is intentional for `migrations add`/`dbcontext info` but must be overridden for `database update` to target your real database.

## Frontend

Not yet scaffolded — added in a later Implementation Plan task.