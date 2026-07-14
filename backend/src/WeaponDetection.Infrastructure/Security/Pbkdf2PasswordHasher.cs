using System.Security.Cryptography;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Security;

// Self-contained PBKDF2 (RFC 2898) implementation using the .NET BCL's
// System.Security.Cryptography primitives directly — no additional NuGet package is required.
// The stored hash string embeds a format version, the iteration count, and the salt, so every
// parameter needed to verify a password travels with the hash itself; nothing is reversible.
public class Pbkdf2PasswordHasher : IPasswordHasher
{
    private const byte FormatVersion = 0x01;
    private const int SaltSizeBytes = 16;
    private const int SubkeySizeBytes = 32;

    // OWASP's current minimum guidance for PBKDF2-HMAC-SHA256.
    private const int Iterations = 210_000;

    private static readonly HashAlgorithmName Algorithm = HashAlgorithmName.SHA256;
    private const int PayloadLength = 1 + sizeof(int) + SaltSizeBytes + SubkeySizeBytes;

    public string Hash(string password)
    {
        if (string.IsNullOrWhiteSpace(password))
        {
            throw new ArgumentException("Password is required.", nameof(password));
        }

        var salt = RandomNumberGenerator.GetBytes(SaltSizeBytes);
        var subkey = Rfc2898DeriveBytes.Pbkdf2(password, salt, Iterations, Algorithm, SubkeySizeBytes);

        var payload = new byte[PayloadLength];
        payload[0] = FormatVersion;
        BitConverter.GetBytes(Iterations).CopyTo(payload, 1);
        salt.CopyTo(payload, 1 + sizeof(int));
        subkey.CopyTo(payload, 1 + sizeof(int) + SaltSizeBytes);

        return Convert.ToBase64String(payload);
    }

    public PasswordVerificationResult Verify(string password, string hashedPassword)
    {
        if (string.IsNullOrWhiteSpace(password))
        {
            throw new ArgumentException("Password is required.", nameof(password));
        }

        // A malformed/empty/unsupported stored hash fails safely (no throw): it represents
        // untrusted, persisted data, not a caller programming error.
        if (string.IsNullOrWhiteSpace(hashedPassword))
        {
            return PasswordVerificationResult.Failed;
        }

        byte[] payload;
        try
        {
            payload = Convert.FromBase64String(hashedPassword);
        }
        catch (FormatException)
        {
            return PasswordVerificationResult.Failed;
        }

        if (payload.Length != PayloadLength || payload[0] != FormatVersion)
        {
            return PasswordVerificationResult.Failed;
        }

        var iterations = BitConverter.ToInt32(payload, 1);
        if (iterations <= 0)
        {
            return PasswordVerificationResult.Failed;
        }

        var salt = new byte[SaltSizeBytes];
        Array.Copy(payload, 1 + sizeof(int), salt, 0, SaltSizeBytes);

        var expectedSubkey = new byte[SubkeySizeBytes];
        Array.Copy(payload, 1 + sizeof(int) + SaltSizeBytes, expectedSubkey, 0, SubkeySizeBytes);

        var actualSubkey = Rfc2898DeriveBytes.Pbkdf2(password, salt, iterations, Algorithm, SubkeySizeBytes);

        // Constant-time comparison to avoid leaking match-length information via timing.
        return CryptographicOperations.FixedTimeEquals(actualSubkey, expectedSubkey)
            ? PasswordVerificationResult.Success
            : PasswordVerificationResult.Failed;
    }
}
