namespace WeaponDetection.Application.Interfaces;

// FS-01 §5.1/§8, IP-01 §7: issues a signed Admin access token carrying a fresh, unique session
// identifier (jti) and expiry. This is the token-issuance primitive only — it does not
// authenticate credentials and does not persist an AdminSession; a later AuthService is
// responsible for verifying the password and creating the AdminSession record from the
// SessionId/IssuedAt/ExpiresAt returned here (FS-01 §5.1 steps 3-4).
public interface IJwtIssuer
{
    // Given an Admin's UserId, produces a signed JWT with a fresh jti. Throws if userId is
    // empty.
    JwtIssuanceResult Issue(Guid userId);
}

// Application-owned result — no Infrastructure or JWT-library types (e.g. JwtSecurityToken)
// are exposed across the Application boundary.
public sealed class JwtIssuanceResult
{
    public string AccessToken { get; }
    public Guid SessionId { get; }
    public DateTimeOffset IssuedAt { get; }
    public DateTimeOffset ExpiresAt { get; }

    public JwtIssuanceResult(string accessToken, Guid sessionId, DateTimeOffset issuedAt, DateTimeOffset expiresAt)
    {
        if (string.IsNullOrWhiteSpace(accessToken))
        {
            throw new ArgumentException("Access token is required.", nameof(accessToken));
        }

        if (sessionId == Guid.Empty)
        {
            throw new ArgumentException("Session id is required.", nameof(sessionId));
        }

        if (expiresAt <= issuedAt)
        {
            throw new ArgumentException("Expiry must be later than issuance.", nameof(expiresAt));
        }

        AccessToken = accessToken;
        SessionId = sessionId;
        IssuedAt = issuedAt;
        ExpiresAt = expiresAt;
    }
}
