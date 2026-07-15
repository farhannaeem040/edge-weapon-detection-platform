namespace WeaponDetection.Application.Interfaces;

// Produces and verifies the two-part Activation Key defined in FS-02 §1.4. The key the Admin sees
// is a single string, `keyId.secret`; the two halves are stored very differently, and that
// asymmetry is the security design (FS-02 §11, ARCH-001 §15.5):
//
//  - `keyId` is a non-secret lookup identifier, persisted verbatim as ActivationKey.ActivationKeyId
//    so an activation resolves by a direct indexed lookup rather than by hashing the presented
//    value against every stored row (AC-14).
//  - `secret` is the credential material. Only a salted adaptive hash of it is ever stored; the
//    plaintext secret, and the complete plaintext key, exist only transiently at generation time
//    and in the single API response that discloses them (FS-02 §5.1/§5.3, §11).
//
// This is Infrastructure-facing crypto, so it lives behind an Application interface like the other
// security utilities (IPasswordHasher, IJwtIssuer, IDeviceSecretProtector). The T-15/T-17 Branch
// and Device services depend on this abstraction, never on the concrete generator.
public interface IActivationKeyGenerator
{
    // Generates a fresh Activation Key: a random `keyId`, a random `secret`, the salted hash of the
    // secret to persist, and the complete `keyId.secret` plaintext to disclose exactly once.
    GeneratedActivationKey Generate();

    // Verifies a presented plaintext `secret` (already split out of `keyId.secret` by the caller)
    // against a previously produced stored hash. Fails safely — returns false rather than throwing
    // — for empty or malformed input, since both arguments originate from untrusted request/stored
    // data at the activation call site (FS-02 §5.5–§5.6).
    bool VerifySecret(string secret, string secretHash);
}

// The result of generating one Activation Key. `PlaintextKey` is the only representation of the
// secret material outside the returned hash, and is never persisted; `KeyId` and `SecretHash` are
// what the caller stores on the ActivationKey record. The raw secret is deliberately not surfaced
// on its own — a caller only ever needs the complete plaintext to disclose and the hash to store.
public sealed record GeneratedActivationKey(string KeyId, string PlaintextKey, string SecretHash);
