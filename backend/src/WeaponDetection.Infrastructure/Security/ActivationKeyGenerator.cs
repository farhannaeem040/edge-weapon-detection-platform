using System.Buffers.Text;
using System.Security.Cryptography;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Security;

// Generates and verifies the two-part Activation Key (FS-02 §1.4, IP-01 §8 steps 1-2 and 4).
//
// Both halves are cryptographically random bytes rendered as Base64Url text. Base64Url's alphabet
// is `A-Z a-z 0-9 - _` — crucially it never contains the '.' delimiter, so `keyId.secret` always
// splits back into exactly two parts and no generated value can smuggle a second delimiter into the
// string (the split safety FS-02 §5.5 step 3 relies on).
//
// The secret half is hashed through the shared IPasswordHasher (T-04) rather than a bespoke digest:
// FS-02 §11 requires a *salted adaptive* hash, and IP-01 §8 step 2 explicitly reuses "the same
// hashing utility family as passwords". The keyId half is not a secret and is not hashed — it is
// the indexed lookup value (AC-14). Neither the plaintext key nor the secret is ever logged.
public sealed class ActivationKeyGenerator : IActivationKeyGenerator
{
    // Conceptually '.' in FS-02 §1.4; confirmed here per FS-02 §19 (deferred implementation detail).
    public const char Delimiter = '.';

    // 128 bits of lookup entropy and 256 bits of secret entropy. Base64Url-encoded these are 22 and
    // 43 characters, so the keyId comfortably fits ActivationKey.ActivationKeyIdMaxLength (64).
    private const int KeyIdSizeBytes = 16;
    private const int SecretSizeBytes = 32;

    private readonly IPasswordHasher _secretHasher;

    public ActivationKeyGenerator(IPasswordHasher secretHasher)
    {
        _secretHasher = secretHasher ?? throw new ArgumentNullException(nameof(secretHasher));
    }

    public GeneratedActivationKey Generate()
    {
        var keyId = GenerateToken(KeyIdSizeBytes);
        var secret = GenerateToken(SecretSizeBytes);
        var secretHash = _secretHasher.Hash(secret);
        var plaintextKey = string.Concat(keyId, Delimiter, secret);

        return new GeneratedActivationKey(keyId, plaintextKey, secretHash);
    }

    public bool VerifySecret(string secret, string secretHash)
    {
        // Untrusted inputs at the activation call site: an empty presented secret or a
        // missing/blank stored hash must fail safely, not throw (IPasswordHasher.Hash/Verify throw
        // on a blank secret, which we deliberately avoid triggering here).
        if (string.IsNullOrWhiteSpace(secret) || string.IsNullOrWhiteSpace(secretHash))
        {
            return false;
        }

        return _secretHasher.Verify(secret, secretHash) == PasswordVerificationResult.Success;
    }

    private static string GenerateToken(int sizeBytes)
    {
        var bytes = RandomNumberGenerator.GetBytes(sizeBytes);
        return Base64Url.EncodeToString(bytes);
    }
}
