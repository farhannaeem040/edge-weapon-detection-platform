using System.Security.Cryptography;
using System.Text;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// The device-credential validation service (FS-02 §10.5, IP-05 T-51/ADR-017). Given a presented
// DeviceId + shared secret, it answers only "are these the device's current, active credentials?".
//
// It is strictly read-only: no transaction, no change tracking, no writes — validation never rotates
// a secret, issues a key, or updates any last-seen/health/Online-Offline state. Order matters and
// follows IP-05 §5: reject missing inputs, look the device up by its public DeviceId, apply the state
// guard (Device.CanAuthenticate) FIRST, and only then unprotect the stored secret and compare it in
// constant time. A ReactivationRequired/Unactivated device — or one whose secret was revoked — is
// rejected before the comparison ever runs.
//
// No stored hash, plaintext secret, activation key, internal identifier (DeviceRecordId), or
// comparison detail is ever placed in a result, log, or exception — the service logs nothing and the
// typed outcome carries only a category.
public class DeviceCredentialValidator : IDeviceCredentialValidator
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IDeviceSecretProtector _deviceSecretProtector;

    public DeviceCredentialValidator(
        WeaponDetectionDbContext dbContext, IDeviceSecretProtector deviceSecretProtector)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _deviceSecretProtector = deviceSecretProtector
            ?? throw new ArgumentNullException(nameof(deviceSecretProtector));
    }

    public async Task<DeviceCredentialValidationResult> ValidateAsync(
        Guid deviceId, string? presentedSecret, CancellationToken cancellationToken = default)
    {
        // Missing inputs are rejected before any lookup. An all-zero id is never an assigned DeviceId,
        // so it short-circuits without a query (also stopping it from coercing to a NULL DeviceId).
        if (deviceId == Guid.Empty)
        {
            return DeviceCredentialValidationResult.Invalid(
                DeviceCredentialValidationOutcome.MissingDeviceId);
        }

        if (string.IsNullOrEmpty(presentedSecret))
        {
            return DeviceCredentialValidationResult.Invalid(
                DeviceCredentialValidationOutcome.MissingSecret);
        }

        // Read-only lookup by the external, persistent DeviceId (assigned only once a device has
        // activated). Never the internal DeviceRecordId, which is not exposed to any caller.
        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleOrDefaultAsync(d => d.DeviceId == deviceId, cancellationToken);
        if (device is null)
        {
            return DeviceCredentialValidationResult.Invalid(
                DeviceCredentialValidationOutcome.UnknownDevice);
        }

        // State guard FIRST (IP-05 §2.1/§5): authentication requires Activated AND a present protected
        // secret. A device that fails this — ReactivationRequired (revoked), Unactivated, or an
        // inconsistent Activated-with-no-secret row — is rejected here, before the secret is ever
        // unprotected or compared, so a stray secret on a non-Activated device can never authenticate.
        if (!device.CanAuthenticate())
        {
            return DeviceCredentialValidationResult.Invalid(ClassifyUnauthenticatableState(device));
        }

        // The state guard passed, so the stored secret is present. Recover it (it is stored
        // recoverable-but-protected, not hashed — ARCH-001 §13.3) and compare in constant time.
        string storedSecret;
        try
        {
            storedSecret = _deviceSecretProtector.Unprotect(device.ProtectedSharedSecret!);
        }
        catch (CryptographicException)
        {
            // FS-07: the server's Data Protection key ring cannot decrypt this stored secret right
            // now (e.g. lost across a container recreation) — a server-side storage failure, not a
            // wrong credential. The caught exception (whose message names the missing key ID) is
            // deliberately discarded here, never rethrown or logged, so the key ID can never reach a
            // log sink or the wire.
            return DeviceCredentialValidationResult.Invalid(
                DeviceCredentialValidationOutcome.CredentialStorageUnavailable);
        }

        return SecretsMatch(storedSecret, presentedSecret)
            // BranchId/DeviceRecordId (FS-06 §6.2) come from this same, already-loaded Device row —
            // never a second lookup — so the resolved device can never diverge from the one just
            // authenticated. DeviceId is assigned by the time CanAuthenticate() can be true, so the
            // guard above guarantees device.DeviceId == deviceId here.
            ? DeviceCredentialValidationResult.Valid(device.BranchId, device.DeviceRecordId)
            : DeviceCredentialValidationResult.Invalid(DeviceCredentialValidationOutcome.SecretMismatch);
    }

    // A diagnostic reason for a device that fails the CanAuthenticate guard. Internal/testable only —
    // the API collapses every one of these to the same uniform rejection.
    private static DeviceCredentialValidationOutcome ClassifyUnauthenticatableState(Device device) =>
        device.ActivationStatus switch
        {
            DeviceActivationStatus.ReactivationRequired =>
                DeviceCredentialValidationOutcome.ReactivationRequired,
            DeviceActivationStatus.Unactivated =>
                DeviceCredentialValidationOutcome.DeviceNotActivated,
            // Activated but CanAuthenticate() was false ⇒ the stored secret is absent (inconsistent).
            _ => DeviceCredentialValidationOutcome.MissingStoredSecret,
        };

    // Constant-time comparison of the two plaintext secrets over their UTF-8 bytes (IP-05 §5), so the
    // time taken does not reveal how much of a wrong secret matched. FixedTimeEquals compares in time
    // independent of content; a length difference returns false without leaking content.
    private static bool SecretsMatch(string storedSecret, string presentedSecret) =>
        CryptographicOperations.FixedTimeEquals(
            Encoding.UTF8.GetBytes(storedSecret), Encoding.UTF8.GetBytes(presentedSecret));
}
