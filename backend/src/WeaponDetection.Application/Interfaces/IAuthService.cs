namespace WeaponDetection.Application.Interfaces;

// FS-01 §5.1/§5.2/§5.4, IP-01 §8: verifies Admin credentials, issues a JWT (via IJwtIssuer),
// persists the corresponding AdminSession, and revokes that session on logout. Does not expose
// HTTP concerns (status codes, request/response DTOs) — that translation belongs to the API layer.
public interface IAuthService
{
    Task<LoginResult> LoginAsync(
        string credentialIdentifier,
        string password,
        CancellationToken cancellationToken = default);

    // Revokes the session named by the presented token's `jti`, for the user the token was issued
    // to (FS-01 §5.4). Both identifiers are passed in rather than re-read from the token here: the
    // Application layer never parses JWTs — the API layer has already had them validated by the
    // authentication middleware and passes the resulting claims down.
    Task<LogoutResult> LogoutAsync(
        Guid sessionId,
        Guid userId,
        CancellationToken cancellationToken = default);
}

// Application-owned result — no EF entities or Infrastructure JWT-library types are exposed
// across the Application boundary. InvalidCredentials is deliberately a single generic
// outcome: FS-01 §5.2/§11 requires an unknown credential identifier and an incorrect password
// to be indistinguishable to the caller.
public sealed class LoginResult
{
    public static readonly LoginResult InvalidCredentials = new(succeeded: false, null, null, null);

    public bool Succeeded { get; }
    public string? AccessToken { get; }
    public DateTimeOffset? IssuedAt { get; }
    public DateTimeOffset? ExpiresAt { get; }

    private LoginResult(bool succeeded, string? accessToken, DateTimeOffset? issuedAt, DateTimeOffset? expiresAt)
    {
        Succeeded = succeeded;
        AccessToken = accessToken;
        IssuedAt = issuedAt;
        ExpiresAt = expiresAt;
    }

    public static LoginResult Success(string accessToken, DateTimeOffset issuedAt, DateTimeOffset expiresAt)
    {
        if (string.IsNullOrWhiteSpace(accessToken))
        {
            throw new ArgumentException("Access token is required.", nameof(accessToken));
        }

        if (expiresAt <= issuedAt)
        {
            throw new ArgumentException("Expiry must be later than issuance.", nameof(expiresAt));
        }

        return new LoginResult(true, accessToken, issuedAt, expiresAt);
    }
}

// Carries no payload: a successful logout returns nothing but the fact of revocation (FS-01 §9.2),
// so an enum is sufficient. Like LoginResult, the failure case is deliberately single and generic —
// an absent, mismatched, expired, or already-revoked session must be indistinguishable to the
// caller (FS-01 §11), so there is no reason code here that the API layer could accidentally surface.
public enum LogoutResult
{
    // The session was active and is now revoked (FS-01 §5.4 step 3).
    Revoked,

    // No active session matched the presented token. In practice the authentication middleware
    // (T-10) already rejects such a request before it reaches the service — this outcome exists so
    // that a session revoked between authorization and this call is still refused, not silently
    // treated as a successful logout.
    SessionNotActive,
}
