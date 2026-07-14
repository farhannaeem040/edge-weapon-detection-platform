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
}
