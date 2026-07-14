namespace WeaponDetection.Application.Interfaces;

// Independent of ASP.NET Core Identity and any Infrastructure-specific type. The Domain layer
// does not depend on this interface; only Application-layer services (e.g. the later
// AuthService/Admin-bootstrap tasks) consume it.
public interface IPasswordHasher
{
    // Hashes a plaintext password. The returned string contains every parameter required to
    // later verify it (salt, iteration count) — it is not a bare digest.
    string Hash(string password);

    // Verifies a plaintext password against a previously produced stored hash.
    PasswordVerificationResult Verify(string password, string hashedPassword);
}

public enum PasswordVerificationResult
{
    Failed,
    Success
}
