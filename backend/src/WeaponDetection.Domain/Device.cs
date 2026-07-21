namespace WeaponDetection.Domain;

// The single Jetson device reserved for a Branch (FS-02 §1.3, ARCH-001 §13.1). Created together
// with its Branch and left unactivated until an Agent presents a valid Activation Key.
//
// Two identifiers, and the difference between them is the whole point of this entity (FS-02 §1.3):
//
//  - DeviceRecordId — the internal primary key. It exists from branch creation, because the
//    Activation Key record needs something to point at before any device has activated. It is
//    never returned by any API, never logged, and never rendered.
//  - DeviceId — the external, persistent identity an Agent uses in its `X-Device-Id` header. It is
//    NULL until the first successful activation, assigned exactly once at that moment, and then
//    retained unchanged forever — including across a reactivation (AC-7, §5.8). This is what keeps
//    historical alerts and health records correlated when a Jetson unit is replaced.
//
// ProtectedSharedSecret holds the *protected* form only. The plaintext shared secret never enters
// this entity: the Application layer protects it (IDeviceSecretProtector, IP-01 §7) before calling
// Activate. Nothing here is ever interpolated into an exception message (FS-02 §11 — secrets are
// never written to logs, and an exception is a log entry waiting to happen).
public class Device
{
    public const int ProtectedSharedSecretMaxLength = 1024;
    public const int LastKnownAddressMaxLength = 256;

    public Guid DeviceRecordId { get; private set; }
    public Guid? DeviceId { get; private set; }
    public Guid BranchId { get; private set; }
    public DeviceActivationStatus ActivationStatus { get; private set; }
    public string? ProtectedSharedSecret { get; private set; }
    public string? LastKnownAddress { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private Device()
    {
    }

    public Device(Guid branchId)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        DeviceRecordId = Guid.NewGuid();
        BranchId = branchId;

        // The reserved, pre-activation state (FS-02 §5.1 step 4). Every one of these is what
        // "unactivated" means, and no caller may choose otherwise at construction.
        DeviceId = null;
        ActivationStatus = DeviceActivationStatus.Unactivated;
        ProtectedSharedSecret = null;
        LastKnownAddress = null;
    }

    // Called on first activation and on every reactivation alike (FS-02 §5.5 step 7, §5.8 steps
    // 5–6). The caller does not get to supply the DeviceId: assigning it here, and only when it is
    // still NULL, is what makes "assigned exactly once, never reassigned" (AC-7) an invariant of
    // the entity rather than a rule each caller has to remember.
    //
    // The shared secret, by contrast, is replaced on *every* activation — that rotation is the
    // security purpose of a reactivation (NFR-SEC-002, ADR-015).
    public void Activate(string protectedSharedSecret)
    {
        if (string.IsNullOrWhiteSpace(protectedSharedSecret))
        {
            throw new ArgumentException(
                "Protected shared secret is required.", nameof(protectedSharedSecret));
        }

        if (protectedSharedSecret.Length > ProtectedSharedSecretMaxLength)
        {
            // States only the limit, never the value.
            throw new ArgumentException(
                $"Protected shared secret must not exceed {ProtectedSharedSecretMaxLength} characters.",
                nameof(protectedSharedSecret));
        }

        DeviceId ??= Guid.NewGuid();

        ActivationStatus = DeviceActivationStatus.Activated;
        ProtectedSharedSecret = protectedSharedSecret;
    }

    // The security-first credential reset (IP-05, FS-02 §5.3 amended, ADR-015 amended). When the
    // Admin regenerates the Activation Key of a device that has already activated, the device's
    // current shared secret is revoked *immediately* — not left valid until a later reactivation.
    //
    // This is the only transition into ReactivationRequired, and it does two things atomically at the
    // entity level: it moves the status to ReactivationRequired and clears ProtectedSharedSecret, so
    // the old secret can never authenticate again (the credential-state form of revocation, §11 —
    // there is no live device-auth endpoint yet). The permanent DeviceId is deliberately untouched
    // (FR-BRN-007, AC-2/AC-12): a credential reset is not a new identity.
    //
    // Only valid for a device that has completed a first activation (DeviceId assigned). An
    // Unactivated device has no DeviceId and no secret to revoke, so calling this on one is a caller
    // bug (the regeneration service must branch on status, §5.3) — a thrown invariant violation, not
    // a silent no-op. Calling it again while already ReactivationRequired is safe and idempotent:
    // the status stays ReactivationRequired, the DeviceId is preserved, and the secret stays null.
    public void RequireReactivation()
    {
        if (DeviceId is null)
        {
            // States only the invariant, never any credential value.
            throw new InvalidOperationException(
                "A device that has never activated cannot be moved to ReactivationRequired.");
        }

        ActivationStatus = DeviceActivationStatus.ReactivationRequired;
        ProtectedSharedSecret = null;
    }

    // The credential-state prerequisite for device authentication (IP-05 §2.1, FS-02 §11). Every
    // current or future device-authentication path must gate on this *in addition to* cryptographic
    // secret verification — it does not replace that verification, it precedes it.
    //
    // Authentication is permitted only when the device is Activated AND a protected shared secret is
    // present. Status is checked first, so a device in ReactivationRequired (or Unactivated) is
    // rejected even if inconsistent/legacy data left a secret value present: a regenerated-away
    // credential can never authenticate. This makes revocation robust against a stray secret rather
    // than relying solely on ProtectedSharedSecret having been nulled.
    public bool CanAuthenticate() =>
        ActivationStatus == DeviceActivationStatus.Activated
        && !string.IsNullOrEmpty(ProtectedSharedSecret);
}
