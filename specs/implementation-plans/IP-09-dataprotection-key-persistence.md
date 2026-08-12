# Implementation Plan: Persistent ASP.NET Core Data Protection Key Storage

| Field | Value |
|-------|-------|
| Plan ID | IP-09 |
| Title | Data Protection key persistence — options, volume, controlled failure handling, isolated recreation acceptance |
| Status | Complete. T-116–T-127 as before; T-128 (production deploy) executed and verified across two forced recreations; T-129 (Device secret rotation via existing reactivation workflow) executed and verified — DeviceId/BranchId/Camera preserved, `credential_validation_valid` confirmed; T-130 (resume IP-08) is a decision for a separate task/session. |
| Realizes | FS-07 |
| Task ID Range | **T-116 – T-130** |
| Owner | Farhan Naeem |
| Explicitly Excluded | The IP-08 rollout itself (paused); production credential rotation (gated separately, Phase 11/12 below); any Agent/DeepStream change. |

---

## 1. Task Breakdown

| Task | Description |
|---|---|
| T-116 | `DataProtectionOptions`/`DataProtectionOptionsValidator` (mirrors `JwtOptions`/`JwtOptionsValidator`) — section `"DataProtection"`, field `KeyPath`, shape-only validation (non-blank, absolute), no I/O. |
| T-117 | `DataProtectionKeyPathValidator` startup step (mirrors `AdminBootstrapper`'s "runs before `app.Run()`, unhandled failure stops startup" pattern) — directory exists-or-created, read/write check, redacted `ConfigurationException` on failure. |
| T-118 | Wire `AddDataProtection().SetApplicationName("WeaponDetection").PersistKeysToFileSystem(...)` into `DependencyInjection.AddInfrastructure`, reading the raw `DataProtection:KeyPath` config value (default unset — no behavior change for non-Docker/local `dotnet run`). |
| T-119 | `compose.yaml`: named volume `dataprotection-keys`, mounted only into `backend`; `DataProtection__KeyPath=/var/lib/weapon-detection/dataprotection-keys` env var on `backend` only. |
| T-120 | `DeviceCredentialValidationOutcome.CredentialStorageUnavailable` + catch `CryptographicException` around the sole `Unprotect` call site in `DeviceCredentialValidator.ValidateAsync`. |
| T-121 | `DeviceAuthenticationUnavailable` API contract (mirrors `DeviceCredentialFailure`) — `503`, `DEVICE_AUTHENTICATION_UNAVAILABLE`. Wire into `DeviceCredentialValidationController` and `SyncEventsController` (both check the new outcome before falling through to the existing uniform 401). |
| T-122 | `.gitignore`: exclude any local Data Protection key-directory path used for dev/testing; confirm no `key-*.xml` is ever tracked. |
| T-123 | Unit tests: options validation, controlled-failure branch (both controllers), purpose-string/application-name regression pins, incorrect-secret-still-401, valid-secret-still-authenticates. |
| T-124 | Integration tests: protect under instance A → new `IServiceProvider`/instance B sharing the same key directory decrypts successfully (simulates process restart); a **different, empty** key directory cannot decrypt the same payload (simulates unrecoverable key loss) and surfaces the typed failure, not a 500. |
| T-125 | Full verification: `dotnet build`/`test`/`vulnerable`, `dotnet ef migrations has-pending-model-changes`, `docker compose config`. |
| T-126 | Isolated container-recreation acceptance (non-production compose project + test SQL Server + test Device) — prove real `docker compose up -d --force-recreate backend` (not merely process restart) preserves authentication across two consecutive recreations. |
| T-127 | Production remediation Plan A/B report (this document §3) — no execution without approval. |
| T-128 | (Conditional on approval) Deploy persistence-enabled Backend to production, recreate once, verify volume stability across a second recreation. |
| T-129 | (Conditional on separate approval) One Device secret rotation/reactivation via the existing approved workflow, if Outcome B stands (it does, per FS-07 §4). |
| T-130 | Resume IP-08 (Phase 12 of the original rollout) only after all of this plan's acceptance criteria pass. |

## 2. Acceptance Criteria

- A protected Device secret remains decryptable after a real `docker compose up -d --force-recreate backend` (not just an in-process restart) — proven twice consecutively in the isolated environment.
- A missing/undecryptable key produces `503 DEVICE_AUTHENTICATION_UNAVAILABLE`, never an unhandled exception, never a key ID or secret in any response/log.
- An incorrect presented secret still produces the unchanged uniform `401 INVALID_DEVICE_CREDENTIALS`.
- Application name (`WeaponDetection`) and purpose string (`WeaponDetection.Device.SharedSecret.v1`) are pinned by regression tests.
- No key XML file is ever committed to git.
- All existing activation/reactivation/IP-08-sync-auth tests remain green.

## 3. Production Remediation Plans (report only until approved — see FS-07 §4)

**Plan A (original key recovered):** not applicable — FS-07 §4 confirms Outcome B (unrecoverable) from the exhaustive read-only search already performed.

**Plan B (rotation required, applies here):**
1. Fresh backup of production SQL Server + compose files (already exist from the IP-08 rollout backup, §7 of that task — reusable; a second backup taken immediately before Phase 128/129 execution).
2. Deploy persistence-enabled Backend image (T-116–T-121) to production.
3. Recreate Backend once; verify the new empty key ring is generated and persisted to the volume.
4. Recreate Backend a second time; verify the *same* key ring (not a third fresh one) is loaded — proves the volume, not the container, is now the source of truth.
5. **Request explicit approval** for one Device secret rotation/reactivation (separate gate from this plan's own approval — rotation is a production credential action, FS-07 explicitly excludes performing it as part of this feature's own scope).
6. Execute via the existing approved reactivation workflow only (Activation Key regeneration → Agent presents key at `/api/v1/activate` → new secret issued and protected under the now-persistent key ring). No manually invented or manually inserted secret.
7. Preserve existing `DeviceId`, `BranchId`, `CameraId` — reactivation does not create a new Device (per `Device.RequireReactivation`/`Activate`'s existing invariant: `DeviceId` assigned once, retained across reactivation).
8. Verify `/api/v1/device/credentials/validate` succeeds with the new secret.
9. Recreate Backend again; verify the rotated credential remains valid.
10. Resume IP-08 rollout only after all of the above pass.

## 4. Verification Commands

```
dotnet build ./backend/WeaponDetection.slnx
dotnet test ./backend/WeaponDetection.slnx
dotnet list ./backend/WeaponDetection.slnx package --vulnerable --include-transitive
dotnet ef migrations has-pending-model-changes --project backend/src/WeaponDetection.Infrastructure --startup-project backend/src/WeaponDetection.Api
docker compose config
```
