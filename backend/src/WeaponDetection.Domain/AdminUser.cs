namespace WeaponDetection.Domain;

// Single Admin account (BR-001, ASM-005). No registration, roles, or profile fields —
// provisioning is an Application-layer concern (later task), not a Domain responsibility.
public class AdminUser
{
    public Guid UserId { get; private set; }
    public string CredentialIdentifier { get; private set; }
    public string PasswordHash { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private AdminUser()
    {
        CredentialIdentifier = null!;
        PasswordHash = null!;
    }

    public AdminUser(string credentialIdentifier, string passwordHash)
    {
        if (string.IsNullOrWhiteSpace(credentialIdentifier))
        {
            throw new ArgumentException("Credential identifier is required.", nameof(credentialIdentifier));
        }

        if (string.IsNullOrWhiteSpace(passwordHash))
        {
            throw new ArgumentException("Password hash is required.", nameof(passwordHash));
        }

        UserId = Guid.NewGuid();
        CredentialIdentifier = credentialIdentifier.Trim();
        PasswordHash = passwordHash;
    }
}
