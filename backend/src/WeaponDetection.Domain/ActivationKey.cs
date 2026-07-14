namespace WeaponDetection.Domain;

// One issued Activation Key for a Device (FS-02 §1.4). A separate entity, deliberately, rather
// than a pair of columns on Device: a Device accumulates a *history* of keys as the Admin
// regenerates them (§5.3), and only the newest is Unconsumed. ARCH-001 §13.1 sketches
// ActivationKeyHash/ActivationKeyStatus as Device fields; FS-02 (Final, which supersedes the
// earlier draft) refines that into this entity, and IP-01 §4 adopts the refinement — no ARCH-001
// requirement changes, only the model that satisfies it.
//
// The key the Admin sees is `keyId.secret` (§1.4). This entity stores the two halves very
// differently, and the asymmetry is the security design:
//
//  - ActivationKeyId *is* the keyId — non-secret, and the primary key, so an activation resolves
//    by a direct indexed lookup instead of hashing the presented value against every stored row
//    (AC-14).
//  - SecretHash is a salted adaptive hash of the secret half. The plaintext secret, and the
//    complete plaintext key, are never stored in any recoverable form (§11, ARCH-001 §15.5) —
//    they exist only in the single API response that discloses them.
//
// The Domain neither generates the key nor hashes it; T-14's generator does both and passes the
// results in, mirroring how AdminSession receives an already-minted `jti`. Neither half is ever
// interpolated into an exception message.
public class ActivationKey
{
    public const int ActivationKeyIdMaxLength = 64;
    public const int SecretHashMaxLength = 512;

    public string ActivationKeyId { get; private set; }
    public Guid DeviceRecordId { get; private set; }
    public string SecretHash { get; private set; }
    public ActivationKeyStatus Status { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private ActivationKey()
    {
        ActivationKeyId = null!;
        SecretHash = null!;
    }

    public ActivationKey(string activationKeyId, Guid deviceRecordId, string secretHash)
    {
        if (string.IsNullOrWhiteSpace(activationKeyId))
        {
            throw new ArgumentException("Activation key id is required.", nameof(activationKeyId));
        }

        var trimmedActivationKeyId = activationKeyId.Trim();

        if (trimmedActivationKeyId.Length > ActivationKeyIdMaxLength)
        {
            throw new ArgumentException(
                $"Activation key id must not exceed {ActivationKeyIdMaxLength} characters.",
                nameof(activationKeyId));
        }

        if (deviceRecordId == Guid.Empty)
        {
            throw new ArgumentException("Device record id is required.", nameof(deviceRecordId));
        }

        if (string.IsNullOrWhiteSpace(secretHash))
        {
            throw new ArgumentException("Secret hash is required.", nameof(secretHash));
        }

        if (secretHash.Length > SecretHashMaxLength)
        {
            throw new ArgumentException(
                $"Secret hash must not exceed {SecretHashMaxLength} characters.", nameof(secretHash));
        }

        ActivationKeyId = trimmedActivationKeyId;
        DeviceRecordId = deviceRecordId;
        SecretHash = secretHash;

        // A newly issued key is always usable; no caller may construct one pre-consumed.
        Status = ActivationKeyStatus.Unconsumed;
    }

    // A one-time credential (BR-003): the transition out of Unconsumed happens once and never
    // reverses. T-19 checks the status before calling this and returns a typed rejection, so a
    // caller reaching here with a spent key is a bug in that orchestration, not a user error —
    // hence a thrown invariant violation rather than a returned failure.
    public void Consume()
    {
        if (Status != ActivationKeyStatus.Unconsumed)
        {
            throw new InvalidOperationException(
                $"An activation key in the {Status} state cannot be consumed.");
        }

        Status = ActivationKeyStatus.Consumed;
    }

    // Unconditional by specification: regeneration invalidates the current key "regardless of its
    // prior consumption state" (FS-02 §5.3 step 3), so an already-Consumed key is invalidated just
    // the same. Idempotent — invalidating twice leaves it invalidated.
    public void Invalidate()
    {
        Status = ActivationKeyStatus.Invalidated;
    }
}
