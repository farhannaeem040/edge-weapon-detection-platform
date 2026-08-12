# Feature Specification: Persistent ASP.NET Core Data Protection Key Storage

| Field | Value |
|-------|-------|
| Feature ID | FS-07 |
| Title | Persistent Data Protection key storage — survive Backend container recreation without losing Device secret decryption |
| Status | Complete — deployed to production, verified across two real container recreations, and the production Device secret rotated via the existing reactivation workflow. Credential validation confirmed working end-to-end (`credential_validation_valid, status=200`). |
| Related Architecture Sections | ARCH-001 §12.1/ARCH-ASM-001 ("single physical/virtual host... deployment convenience"), §13.3 (recoverable-but-protected Device secret storage via Data Protection) |
| Owner | Farhan Naeem |
| Triggering incident | 2026-07-28: `docker compose up -d backend` (IP-08 rollout, Phase 11) recreated the Backend container. `services.AddDataProtection()` had no persistent key repository configured, so the key ring generated inside the previous container's writable layer was destroyed. The single existing production `Device`'s `ProtectedSharedSecret` — encrypted under the destroyed key `{33c85197-983a-4552-a49e-5e70ec5c5233}` — became permanently undecryptable, and every credential-validation/sync-auth call began raising an unhandled `CryptographicException` (HTTP 500). Full incident record: see the IP-08 rollout conversation, 2026-07-28 06:24–06:52 UTC. |
| Dependencies | None — this is an infrastructure correction to code that has existed since IP-01 (`DataProtectionDeviceSecretProtector`, `AddInfrastructure`). Independent of IP-08. |
| Explicitly excluded | The IP-08 DetectionEvent sync rollout itself (paused, resumes only after this feature's Phase 12 gate passes); any change to `deepstream-rtsp-route.service`, tracker, inference, RTSP, or cooldown settings; any Agent code change; a second production Device; changing `DeviceId`/`BranchId`/`CameraId`; changing the Data Protection purpose string (`WeaponDetection.Device.SharedSecret.v1`) or the protector's public contract in a way that breaks compatibility with data protected under an intact key. |

---

## 1. Root Cause

`DataProtectionDeviceSecretProtector`'s own comment (since IP-01) claims: *"Keys are persisted using the ASP.NET Core default (local file-system key ring), consistent with the prototype's single-host deployment (ARCH-001 §12.1, ARCH-ASM-001)."* This was true only in the sense that the ASP.NET Core default writes keys to `%LOCALAPPDATA%`/`~/.aspnet/DataProtection-Keys` on whatever filesystem the process sees — but once the Backend moved into a Docker container (IP-04, `compose.yaml`) with no volume mounted at that path, "the local filesystem" became the container's ephemeral writable layer, which `docker compose up`/`--force-recreate`/an image rebuild all discard. ARCH-ASM-001's "single host" assumption was never actually violated (it is still one host) — the assumption that quietly broke was that the *process's local filesystem persists across restarts*, which containerization does not guarantee for a plain `docker compose up -d <service>` recreate (as opposed to a bare `docker restart`, which reuses the same container and would have preserved it).

## 2. Scope

Fix the Backend's Data Protection key persistence so that:
- keys survive full container recreation (not merely process restart);
- an existing protected secret remains decryptable after any future redeploy;
- a missing/undecryptable key is a controlled, typed, non-crashing outcome rather than an unhandled exception;
- the one currently-broken production Device secret is recovered if possible, or explicitly and safely rotated through the existing approved workflow if not, under a separate approval gate (this feature does not perform that rotation itself — see IP-09 Phase 11/12).

## 3. Design

### 3.1 Persistent key directory

```csharp
services
    .AddDataProtection()
    .SetApplicationName("WeaponDetection")
    .PersistKeysToFileSystem(new DirectoryInfo(dataProtectionKeyPath));
```

`dataProtectionKeyPath` comes from a new bound-and-validated options type, `DataProtectionOptions` (section `"DataProtection"`, env var `DataProtection__KeyPath`), mirroring `JwtOptions`/`JwtOptionsValidator`'s existing pattern exactly (`ValidateOnStart()`). The path is never hardcoded to a Windows host path in application code — it is supplied entirely through configuration, defaulting to a documented container-internal path (`/var/lib/weapon-detection/dataprotection-keys`) set in `compose.yaml`, not in `appsettings.json` (so a non-Docker/local-dev run still uses the ASP.NET Core default unless the variable is set — no behavior change for `dotnet run` outside Compose).

**Application name is fixed as `"WeaponDetection"` and must never change between deployments** — `SetApplicationName` is itself an input to key derivation isolation; changing it would make every existing protected payload unreadable, which is the exact failure this feature exists to prevent recurring.

### 3.2 Docker volume

A new named volume, `dataprotection-keys`, mounted **only** into the `backend` service at `/var/lib/weapon-detection/dataprotection-keys` — not into `frontend`, `sqlserver`, `mediamtx`, or `migrations` (the migrations job never authenticates a device and has no need to unprotect a secret). This follows `compose.yaml`'s existing `sqlserver-data` named-volume convention exactly (survives `down`, removed only by explicit `down -v`).

### 3.3 Startup validation (no I/O inside the options validator)

Mirroring the separation `AdminBootstrapper` already establishes (a dedicated startup step, not inline in `Program.cs`, that fails the host before it serves any request): a new `DataProtectionKeyPathValidator` step checks, before `app.Run()`:
- the configured path is absolute;
- the directory exists or can be created;
- the Backend process can read and write it.

Failure raises a clear, redacted `ConfigurationException` naming only the configuration key (`DataProtection:KeyPath`) — never a file path's contents, never key material. `IValidateOptions<DataProtectionOptions>` (the `JwtOptionsValidator`-style pure check) validates only the string shape (non-blank, absolute) with no filesystem access, consistent with that pattern's existing no-I/O discipline; the directory-creation/writability check is the separate, explicit startup step Phase 6 requires.

### 3.4 Controlled behavior for an undecryptable stored secret (Phase 7)

`DeviceCredentialValidator.ValidateAsync` is the **only** caller of `IDeviceSecretProtector.Unprotect` in the codebase (confirmed by repository-wide search). It now catches `System.Security.Cryptography.CryptographicException` around that one call and returns a new outcome:

```csharp
public enum DeviceCredentialValidationOutcome
{
    Valid,
    MissingDeviceId,
    MissingSecret,
    UnknownDevice,
    ReactivationRequired,
    DeviceNotActivated,
    MissingStoredSecret,
    SecretMismatch,
    CredentialStorageUnavailable,   // new: the stored secret cannot be decrypted by the current key ring
}
```

This is deliberately distinct from every existing outcome: it means *the server cannot currently answer the question*, not *the presented credential is wrong*. Both `DeviceCredentialValidationController` and `SyncEventsController` check for this specific outcome and return **`503 Service Unavailable`** with a new, generic `DEVICE_AUTHENTICATION_UNAVAILABLE` error code — never the `401 INVALID_DEVICE_CREDENTIALS` envelope (a 503 here must not be usable by an attacker to distinguish "wrong secret" from "key ring broken," but conflating it with the confirmed-revocation 401 would be actively harmful: per ADR-017 only a 401 with `INVALID_DEVICE_CREDENTIALS` is the confirmed-revocation signal the Agent may act on, and a transient server-side storage failure must never be mistaken for that). No cryptographic key ID, stack trace, or protected-secret content is ever placed in the response, a log, or an exception message; only a bounded, redacted log event (e.g. `device_credential_storage_unavailable`) is emitted, without the key ID.

An incorrect *presented* secret continues to return the existing uniform 401 unchanged; a valid secret continues to authenticate unchanged. This feature changes behavior in exactly one new branch: the key-ring-missing case.

### 3.5 Purpose strings are a compatibility contract

`DataProtectionDeviceSecretProtector`'s purpose string, `"WeaponDetection.Device.SharedSecret.v1"`, is unchanged by this feature and is documented here as load-bearing: any future change to it would make every already-protected secret unreadable under the *new* protector instance even with an intact key ring, for the same class of reason the application name is fixed. A regression test pins this string.

## 4. Recovery of the Existing Broken Secret

Per the incident investigation (read-only, exhausted): no orphaned container, no existing volume, no `DataProtectionKeys` table, and no key material in the tagged rollback image or the local backups directory contain the destroyed key ring. **The original key is confirmed unrecoverable.** Restoring persistence going forward cannot retroactively decrypt ciphertext that was protected under a key that no longer exists anywhere. The only path to restore the one production Device's authentication is one explicitly approved secret rotation/reactivation through the *existing* approved activation/reactivation workflow (IP-05) — not a new mechanism, not a manually invented or manually inserted secret. This feature's implementation (Phases 5–10) proceeds independently of that decision; the rotation itself is gated separately (IP-09 Phase 11/12) and is out of this document's implementation scope.

## 5. Backward Compatibility / Rollback

Enabling `PersistKeysToFileSystem` with an **empty** directory (as will be the case on first deploy, since the previous key ring is unrecoverable) causes the Data Protection system to generate a brand-new key ring on first startup and persist it going forward — this is the same "start fresh" state the system is already in, just made durable from this point on. No existing *decryptable* data is put at risk by this change; the only data affected (the single broken Device secret) is already unrecoverable regardless of this feature. Rollback of this feature (reverting the volume mount/options) simply returns to the previously-broken-on-every-recreate posture — there is no data-loss risk either direction beyond what the incident already caused.
