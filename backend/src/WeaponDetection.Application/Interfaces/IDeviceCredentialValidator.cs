namespace WeaponDetection.Application.Interfaces;

// Validates a running device's current credentials — its permanent public DeviceId and its private
// shared secret — for the device-authenticated credential-validation endpoint (FS-02 §10.5, IP-05
// T-51/ADR-017). It is detect-only: it reads credential state, verifies the secret, and returns a
// verdict; it never issues, rotates, or returns a key or secret, and it writes nothing.
//
// The result carries a typed outcome ONLY so the service is testable — exactly like
// DeviceActivationResult's failure reason. The API layer (T-52) collapses every non-Valid outcome to
// one uniform 401/INVALID_DEVICE_CREDENTIALS that never reveals which check failed (FS-02 §10.5); the
// outcome is never placed on the wire, in a log, or in an exception.
public interface IDeviceCredentialValidator
{
    // Validates the presented credentials. Reads only (no transaction, no tracking). The state guard
    // (Device.CanAuthenticate: Activated AND a stored secret present) is applied BEFORE the stored
    // secret is unprotected and compared, so a ReactivationRequired/Unactivated device — or one whose
    // secret was revoked — is rejected without the secret comparison ever running.
    Task<DeviceCredentialValidationResult> ValidateAsync(
        Guid deviceId, string? presentedSecret, CancellationToken cancellationToken = default);
}

// The outcome of a credential-validation attempt. Only Valid authorizes the device; every other value
// is an internal, testable distinction that the API maps to the same uniform rejection. No value
// carries a secret, hash, internal identifier, or comparison detail.
public enum DeviceCredentialValidationOutcome
{
    Valid,
    MissingDeviceId,
    MissingSecret,
    UnknownDevice,
    ReactivationRequired,   // credentials were revoked by a key regeneration; device awaits reactivation
    DeviceNotActivated,     // Unactivated (defensive: an unactivated device has no external DeviceId)
    MissingStoredSecret,    // inconsistent state: Activated but no stored protected secret
    SecretMismatch,

    // FS-07: the stored secret exists but cannot currently be decrypted by the server's Data
    // Protection key ring (e.g. the key ring was lost across a container recreation). Distinct
    // from every outcome above: this means the server cannot currently answer the question, not
    // that the presented credential is wrong. The API layer maps this to 503, never the uniform
    // 401 a confirmed-revocation signal — conflating the two would let a transient server-side
    // failure be mistaken for (or mask) an actual credential revocation.
    CredentialStorageUnavailable,
}

// The immutable result. It exposes the boolean verdict and the typed outcome (for tests); it holds no
// credential material. ToString renders only the outcome name — there is nothing sensitive to redact.
//
// BranchId/DeviceRecordId (FS-06 §6.2, additive) are populated only on a Valid result, from the same
// Device row the validator already loaded for the credential check — never a second, independent
// lookup, which could theoretically resolve a different device if a row changed between two queries.
// They are null on every Invalid result and on any Valid result minted before this feature existed
// (there are none; the only factory is this type's own Valid()). Existing callers that read only
// IsValid (DeviceCredentialValidationController) are unaffected by these additive fields.
public sealed class DeviceCredentialValidationResult
{
    private DeviceCredentialValidationResult(
        DeviceCredentialValidationOutcome outcome, Guid? branchId, Guid? deviceRecordId)
    {
        Outcome = outcome;
        BranchId = branchId;
        DeviceRecordId = deviceRecordId;
    }

    public DeviceCredentialValidationOutcome Outcome { get; }

    public bool IsValid => Outcome == DeviceCredentialValidationOutcome.Valid;

    // The authenticated device's Branch — non-null only when IsValid. Callers other than the
    // credential-validation endpoint (e.g. AlertSyncService, FS-06 §6.3) use this to scope
    // Branch-owned lookups without a second Device query.
    public Guid? BranchId { get; }

    // The authenticated device's internal primary key. Never serialized to any wire response
    // (FS-02 §1.3) — carried here only for callers that need to address the same Device row.
    public Guid? DeviceRecordId { get; }

    public static DeviceCredentialValidationResult Valid(Guid branchId, Guid deviceRecordId) =>
        new(DeviceCredentialValidationOutcome.Valid, branchId, deviceRecordId);

    public static DeviceCredentialValidationResult Invalid(DeviceCredentialValidationOutcome outcome) =>
        outcome == DeviceCredentialValidationOutcome.Valid
            ? throw new ArgumentException("Valid is not a failure outcome.", nameof(outcome))
            : new DeviceCredentialValidationResult(outcome, null, null);

    public override string ToString() => $"{nameof(DeviceCredentialValidationResult)} {{ Outcome = {Outcome} }}";
}
