namespace WeaponDetection.Domain;

// SessionId corresponds to the JWT `jti` claim (FS-01 §8, ADR-013). The session identifier and
// its expiry are minted by the Application layer (later task) and passed in here; the Domain
// only enforces the invariants below, it does not generate identifiers or expiry policy.
public class AdminSession
{
    public Guid SessionId { get; private set; }
    public Guid UserId { get; private set; }
    public DateTimeOffset IssuedAt { get; private set; }
    public DateTimeOffset ExpiresAt { get; private set; }
    public bool Revoked { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private AdminSession()
    {
    }

    public AdminSession(Guid sessionId, Guid userId, DateTimeOffset issuedAt, DateTimeOffset expiresAt)
    {
        if (sessionId == Guid.Empty)
        {
            throw new ArgumentException("Session id is required.", nameof(sessionId));
        }

        if (userId == Guid.Empty)
        {
            throw new ArgumentException("User id is required.", nameof(userId));
        }

        if (expiresAt <= issuedAt)
        {
            throw new ArgumentException("Expiry must be later than issuance.", nameof(expiresAt));
        }

        SessionId = sessionId;
        UserId = userId;
        IssuedAt = issuedAt;
        ExpiresAt = expiresAt;
        Revoked = false;
    }

    // Idempotent: revoking an already-revoked session leaves it revoked, it does not
    // reactivate or otherwise mutate the session (FS-01 §5.4 step 6).
    public void Revoke()
    {
        Revoked = true;
    }
}
