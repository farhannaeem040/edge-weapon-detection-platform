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

    public AuthService(WeaponDetectionDbContext dbContext, IPasswordHasher passwordHasher, IJwtIssuer jwtIssuer)
    {
        _dbContext = dbContext;
        _passwordHasher = passwordHasher;
        _jwtIssuer = jwtIssuer;
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
}
