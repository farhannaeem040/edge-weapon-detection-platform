using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// IP-01 §8 lists AuthService under Application/Services, but the concrete implementation
// depends on WeaponDetectionDbContext (EF Core) — placing it here preserves the same
// Application-does-not-reference-Infrastructure boundary established for AdminBootstrapper
// (T-06). IAuthService (Application) is the only type the Application layer exposes; callers
// never see EF entities or JWT-library types.
public class AuthService : IAuthService
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IPasswordHasher _passwordHasher;
    private readonly IJwtIssuer _jwtIssuer;
    private readonly TimeProvider _timeProvider;

    public AuthService(
        WeaponDetectionDbContext dbContext,
        IPasswordHasher passwordHasher,
        IJwtIssuer jwtIssuer,
        TimeProvider timeProvider)
    {
        _dbContext = dbContext;
        _passwordHasher = passwordHasher;
        _jwtIssuer = jwtIssuer;
        _timeProvider = timeProvider;
    }

    public async Task<LoginResult> LoginAsync(
        string credentialIdentifier,
        string password,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(credentialIdentifier))
        {
            throw new ArgumentException("Credential identifier is required.", nameof(credentialIdentifier));
        }

        if (string.IsNullOrWhiteSpace(password))
        {
            throw new ArgumentException("Password is required.", nameof(password));
        }

        var normalizedIdentifier = credentialIdentifier.Trim();

        // AdminUserConfiguration's unique index relies on SQL Server's default
        // case-insensitive collation, so this equality comparison is already case-insensitive
        // at the database level — no additional normalization is needed here.
        var admin = await _dbContext.AdminUsers
            .SingleOrDefaultAsync(u => u.CredentialIdentifier == normalizedIdentifier, cancellationToken);

        // Known limitation (documented, not mitigated): an unknown identifier returns here
        // without performing a PBKDF2 verification, while a known identifier with the wrong
        // password proceeds to Verify() below — the two branches are not constant-time, so a
        // network-timing side channel could in principle distinguish "unknown identifier" from
        // "known identifier, wrong password" despite both returning the identical public
        // LoginResult.InvalidCredentials. Neither FS-01 nor IP-01 requires timing-hardening for
        // this single-Admin, trusted-LAN prototype, so no dummy-verification path is added.
        if (admin is null)
        {
            return LoginResult.InvalidCredentials;
        }

        var verification = _passwordHasher.Verify(password, admin.PasswordHash);
        if (verification != PasswordVerificationResult.Success)
        {
            return LoginResult.InvalidCredentials;
        }

        var issuance = _jwtIssuer.Issue(admin.UserId);
        var session = new AdminSession(issuance.SessionId, admin.UserId, issuance.IssuedAt, issuance.ExpiresAt);

        await using var transaction = await _dbContext.Database.BeginTransactionAsync(cancellationToken);
        try
        {
            _dbContext.AdminSessions.Add(session);
            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);
            throw new InvalidOperationException(
                "Login failed while persisting the new Admin session.", ex);
        }

        return LoginResult.Success(issuance.AccessToken, issuance.IssuedAt, issuance.ExpiresAt);
    }

    public async Task<LogoutResult> LogoutAsync(
        Guid sessionId,
        Guid userId,
        CancellationToken cancellationToken = default)
    {
        if (sessionId == Guid.Empty)
        {
            throw new ArgumentException("Session id is required.", nameof(sessionId));
        }

        if (userId == Guid.Empty)
        {
            throw new ArgumentException("User id is required.", nameof(userId));
        }

        var now = _timeProvider.GetUtcNow();

        // Tracked (no AsNoTracking) — unlike AdminSessionValidator's read-only authorization check,
        // this query loads the session precisely in order to mutate it.
        //
        // The four conditions below are the same ones AdminSessionValidator applies, re-applied
        // here rather than assumed: by FS-01 §9.2 logout is itself a protected endpoint, so the
        // authentication middleware has already refused any request whose session is absent,
        // mismatched, expired, or revoked — but re-checking closes the window in which a session
        // is revoked between that authorization pass and this call, and it keeps the service
        // correct for any future caller that is not behind the same middleware. A session failing
        // any condition is never revoked "again": the revoked record is left exactly as it is
        // (FS-01 §5.4 step 6 — not reactivated, not re-timestamped).
        var session = await _dbContext.AdminSessions
            .SingleOrDefaultAsync(
                s => s.SessionId == sessionId
                    && s.UserId == userId
                    && !s.Revoked
                    && s.ExpiresAt > now,
                cancellationToken);

        if (session is null)
        {
            return LogoutResult.SessionNotActive;
        }

        session.Revoke();

        // A single-row UPDATE — SaveChangesAsync already runs it in an implicit transaction, so no
        // explicit BeginTransaction is warranted here (unlike LoginAsync, which had a persistence
        // failure path of its own to unwind). Revocation is idempotent at the Domain level
        // (AdminSession.Revoke), so even two concurrent logouts for one session converge on the
        // same revoked state — neither can resurrect it.
        await _dbContext.SaveChangesAsync(cancellationToken);

        return LogoutResult.Revoked;
    }
}
