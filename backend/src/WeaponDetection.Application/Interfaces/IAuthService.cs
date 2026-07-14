namespace WeaponDetection.Application.Interfaces;

// FS-01 §5.1/§5.2, IP-01 §8: verifies Admin credentials, issues a JWT (via IJwtIssuer), and
// persists the corresponding AdminSession. Does not expose HTTP concerns (status codes,
// request/response DTOs) — that translation belongs to a later API-layer task.
public interface IAuthService
{
    Task<LoginResult> LoginAsync(
        string credentialIdentifier,
        string password,
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
