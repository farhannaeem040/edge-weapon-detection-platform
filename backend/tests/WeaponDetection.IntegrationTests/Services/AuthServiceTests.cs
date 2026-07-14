using System;
using System.IdentityModel.Tokens.Jwt;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies AuthService's login workflow — password verification, JWT issuance, and AdminSession
// persistence — against a real SQL Server database (IP-01 §9): case-insensitive credential
// matching relies on SQL Server's default collation (AdminUserConfiguration), which EF Core
// InMemory/SQLite would not faithfully reproduce. Each test gets its own freshly migrated,
// empty database (not a shared IClassFixture).
//
// No test in this file prints a plaintext password, password hash, or complete JWT to
// console/log output.
public class AuthServiceTests : IDisposable
{
    private const string AdminPassword = "Correct-Horse-Battery-Staple-1";
    private const string WrongPassword = "not-the-right-password";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly Pbkdf2PasswordHasher _passwordHasher = new();
    private readonly AuthService _authService;
    private Guid _adminUserId;
    private string _adminIdentifier = null!;

    public AuthServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionAuthServiceTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        var jwtIssuer = new JwtIssuer(
            Options.Create(new JwtOptions
            {
                Issuer = "test-issuer",
                Audience = "test-audience",
                SigningKey = new string('k', 32),
                AccessTokenLifetimeMinutes = 60,
            }),
            TimeProvider.System);

        _authService = new AuthService(_dbContext, _passwordHasher, jwtIssuer, TimeProvider.System);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private async Task<AdminUser> SeedAdminAsync(string? identifier = null, string? password = null)
    {
        identifier ??= $"admin-{Guid.NewGuid()}";
        password ??= AdminPassword;

        var admin = new AdminUser(identifier, _passwordHasher.Hash(password));
        _dbContext.AdminUsers.Add(admin);
        await _dbContext.SaveChangesAsync();

        _adminUserId = admin.UserId;
        _adminIdentifier = identifier;

        return admin;
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_Succeeds()
    {
        await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        Assert.True(result.Succeeded);
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_ReturnsNonEmptyAccessToken()
    {
        await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        Assert.False(string.IsNullOrWhiteSpace(result.AccessToken));
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_PersistsExactlyOneAdminSession()
    {
        await SeedAdminAsync();

        await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        var sessions = await _dbContext.AdminSessions.ToListAsync();
        Assert.Single(sessions);
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_PersistedSessionIdMatchesJtiInToken()
    {
        await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);
        var session = await _dbContext.AdminSessions.SingleAsync();

        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken!);
        var jti = token.Claims.Single(c => c.Type == JwtRegisteredClaimNames.Jti).Value;

        Assert.Equal(jti, session.SessionId.ToString());
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_PersistedUserIdMatchesAdmin()
    {
        await SeedAdminAsync();

        await _authService.LoginAsync(_adminIdentifier, AdminPassword);
        var session = await _dbContext.AdminSessions.SingleAsync();

        Assert.Equal(_adminUserId, session.UserId);
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_PersistedTimestampsMatchIssuanceResult()
    {
        await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);
        var session = await _dbContext.AdminSessions.SingleAsync();

        Assert.Equal(result.IssuedAt, session.IssuedAt);
        Assert.Equal(result.ExpiresAt, session.ExpiresAt);
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_SessionStartsNonRevoked()
    {
        await SeedAdminAsync();

        await _authService.LoginAsync(_adminIdentifier, AdminPassword);
        var session = await _dbContext.AdminSessions.SingleAsync();

        Assert.False(session.Revoked);
    }

    [Fact]
    public async Task LoginAsync_UnknownIdentifier_ReturnsGenericInvalidCredentials()
    {
        var result = await _authService.LoginAsync($"no-such-admin-{Guid.NewGuid()}", AdminPassword);

        Assert.False(result.Succeeded);
        Assert.Same(LoginResult.InvalidCredentials, result);
    }

    [Fact]
    public async Task LoginAsync_WrongPassword_ReturnsSameGenericInvalidCredentialsOutcome()
    {
        await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, WrongPassword);

        Assert.False(result.Succeeded);
        Assert.Same(LoginResult.InvalidCredentials, result);
    }

    [Fact]
    public async Task LoginAsync_UnknownIdentifier_CreatesNoSession()
    {
        await _authService.LoginAsync($"no-such-admin-{Guid.NewGuid()}", AdminPassword);

        Assert.Empty(await _dbContext.AdminSessions.ToListAsync());
    }

    [Fact]
    public async Task LoginAsync_WrongPassword_CreatesNoSession()
    {
        await SeedAdminAsync();

        await _authService.LoginAsync(_adminIdentifier, WrongPassword);

        Assert.Empty(await _dbContext.AdminSessions.ToListAsync());
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task LoginAsync_BlankCredentialIdentifier_Throws(string identifier)
    {
        await Assert.ThrowsAsync<ArgumentException>(() => _authService.LoginAsync(identifier, AdminPassword));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task LoginAsync_BlankPassword_Throws(string password)
    {
        await SeedAdminAsync();

        await Assert.ThrowsAsync<ArgumentException>(() => _authService.LoginAsync(_adminIdentifier, password));
    }

    [Fact]
    public async Task LoginAsync_SessionPersistenceFailure_ThrowsAndDoesNotExposeSecrets()
    {
        var admin = await SeedAdminAsync();

        // Forces the AdminSession insert to fail after password verification and token
        // issuance have already succeeded, exercising the failure path without a successful
        // LoginResult ever being returned.
        await _dbContext.Database.ExecuteSqlRawAsync("DROP TABLE AdminSessions");

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            _authService.LoginAsync(_adminIdentifier, AdminPassword));

        Assert.DoesNotContain(AdminPassword, exception.ToString());
        Assert.DoesNotContain(admin.PasswordHash, exception.ToString());
    }

    [Fact]
    public async Task LoginAsync_CalledTwiceWithCorrectCredentials_CreatesSeparateSessionsWithDifferentIds()
    {
        await SeedAdminAsync();

        var first = await _authService.LoginAsync(_adminIdentifier, AdminPassword);
        var second = await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        var sessions = await _dbContext.AdminSessions.ToListAsync();
        Assert.Equal(2, sessions.Count);
        Assert.NotEqual(first.AccessToken, second.AccessToken);
        Assert.NotEqual(sessions[0].SessionId, sessions[1].SessionId);
    }

    [Fact]
    public async Task LoginAsync_CredentialIdentifierDifferentCase_StillMatches()
    {
        await SeedAdminAsync(identifier: $"CaseTest-{Guid.NewGuid()}");

        var result = await _authService.LoginAsync(_adminIdentifier.ToUpperInvariant(), AdminPassword);

        Assert.True(result.Succeeded);
    }

    [Fact]
    public async Task LoginAsync_CorrectCredentials_TokenDoesNotContainPlaintextPasswordOrHash()
    {
        var admin = await SeedAdminAsync();

        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        Assert.DoesNotContain(AdminPassword, result.AccessToken!);
        Assert.DoesNotContain(admin.PasswordHash, result.AccessToken!);
    }

    // --- Logout / session revocation (IP-01 T-11; FS-01 §5.4, AC-5, AC-6) ---

    // Logs in for real and returns the id of the session the issued token actually names, so the
    // logout tests below revoke exactly the session a client would be presenting — not a
    // hand-seeded stand-in.
    private async Task<Guid> LoginAndGetSessionIdAsync()
    {
        var result = await _authService.LoginAsync(_adminIdentifier, AdminPassword);

        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken!);
        var jti = token.Claims.Single(c => c.Type == JwtRegisteredClaimNames.Jti).Value;

        return Guid.Parse(jti);
    }

    private async Task<AdminSession> SeedSessionAsync(
        Guid userId, DateTimeOffset issuedAt, DateTimeOffset expiresAt, bool revoked = false)
    {
        var session = new AdminSession(Guid.NewGuid(), userId, issuedAt, expiresAt);

        if (revoked)
        {
            session.Revoke();
        }

        _dbContext.AdminSessions.Add(session);
        await _dbContext.SaveChangesAsync();

        return session;
    }

    [Fact]
    public async Task LogoutAsync_ActiveSession_ReturnsRevoked()
    {
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        var result = await _authService.LogoutAsync(sessionId, _adminUserId);

        Assert.Equal(LogoutResult.Revoked, result);
    }

    [Fact]
    public async Task LogoutAsync_ActiveSession_MarksTheSessionRevokedInTheDatabase()
    {
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        await _authService.LogoutAsync(sessionId, _adminUserId);

        var session = await _dbContext.AdminSessions
            .AsNoTracking()
            .SingleAsync(s => s.SessionId == sessionId);

        Assert.True(session.Revoked);
    }

    [Fact]
    public async Task LogoutAsync_ActiveSession_CreatesNoNewSession()
    {
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        await _authService.LogoutAsync(sessionId, _adminUserId);

        Assert.Single(await _dbContext.AdminSessions.AsNoTracking().ToListAsync());
    }

    [Fact]
    public async Task LogoutAsync_SecondCallWithTheSameSession_ReturnsSessionNotActive()
    {
        // FS-01 §5.4 step 6 / §14 T-12: an already-revoked session is not a valid session to log
        // out of. (Over HTTP the middleware rejects it before the service is even reached — this
        // asserts the service itself does not treat it as a fresh, successful logout.)
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        var first = await _authService.LogoutAsync(sessionId, _adminUserId);
        var second = await _authService.LogoutAsync(sessionId, _adminUserId);

        Assert.Equal(LogoutResult.Revoked, first);
        Assert.Equal(LogoutResult.SessionNotActive, second);
    }

    [Fact]
    public async Task LogoutAsync_SecondCall_NeitherReactivatesTheRecordNorCreatesANewOne()
    {
        // FS-01 §5.4 step 6: the revoked record is not reactivated, and no new session appears.
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        await _authService.LogoutAsync(sessionId, _adminUserId);
        await _authService.LogoutAsync(sessionId, _adminUserId);

        var sessions = await _dbContext.AdminSessions.AsNoTracking().ToListAsync();

        Assert.Single(sessions);
        Assert.True(sessions[0].Revoked);
    }

    [Fact]
    public async Task LogoutAsync_AlreadyRevokedSession_LeavesTheRecordByteForByteUnchanged()
    {
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        await _authService.LogoutAsync(sessionId, _adminUserId);
        var afterFirst = await _dbContext.AdminSessions.AsNoTracking().SingleAsync();

        await _authService.LogoutAsync(sessionId, _adminUserId);
        var afterSecond = await _dbContext.AdminSessions.AsNoTracking().SingleAsync();

        Assert.Equal(afterFirst.SessionId, afterSecond.SessionId);
        Assert.Equal(afterFirst.UserId, afterSecond.UserId);
        Assert.Equal(afterFirst.IssuedAt, afterSecond.IssuedAt);
        Assert.Equal(afterFirst.ExpiresAt, afterSecond.ExpiresAt);
        Assert.True(afterSecond.Revoked);
    }

    [Fact]
    public async Task LogoutAsync_UnknownSessionId_ReturnsSessionNotActive()
    {
        await SeedAdminAsync();
        await LoginAndGetSessionIdAsync();

        var result = await _authService.LogoutAsync(Guid.NewGuid(), _adminUserId);

        Assert.Equal(LogoutResult.SessionNotActive, result);
    }

    [Fact]
    public async Task LogoutAsync_SessionBelongingToAnotherUser_ReturnsSessionNotActiveAndLeavesItActive()
    {
        // A session that exists and is active, but was issued to a different user than the one
        // named — the mismatched user/session association of FS-01 §11. It must not be revoked:
        // one Admin's token can never revoke another's session.
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        var result = await _authService.LogoutAsync(sessionId, Guid.NewGuid());

        var session = await _dbContext.AdminSessions
            .AsNoTracking()
            .SingleAsync(s => s.SessionId == sessionId);

        Assert.Equal(LogoutResult.SessionNotActive, result);
        Assert.False(session.Revoked);
    }

    [Fact]
    public async Task LogoutAsync_ExpiredSession_ReturnsSessionNotActive()
    {
        // FS-01 §11 requires the session record itself to be non-expired, not merely the token.
        await SeedAdminAsync();
        var now = DateTimeOffset.UtcNow;
        var expired = await SeedSessionAsync(_adminUserId, now.AddMinutes(-120), now.AddMinutes(-60));

        var result = await _authService.LogoutAsync(expired.SessionId, _adminUserId);

        Assert.Equal(LogoutResult.SessionNotActive, result);
    }

    [Fact]
    public async Task LogoutAsync_RevokesOnlyTheNamedSession()
    {
        // Two concurrent sessions for the one Admin (e.g. two browsers). Logging out of one must
        // not log out the other — revocation is per-session, keyed by the token's own `jti`.
        await SeedAdminAsync();
        var firstSessionId = await LoginAndGetSessionIdAsync();
        var secondSessionId = await LoginAndGetSessionIdAsync();

        await _authService.LogoutAsync(firstSessionId, _adminUserId);

        var sessions = await _dbContext.AdminSessions.AsNoTracking().ToListAsync();

        Assert.True(sessions.Single(s => s.SessionId == firstSessionId).Revoked);
        Assert.False(sessions.Single(s => s.SessionId == secondSessionId).Revoked);
    }

    [Fact]
    public async Task LogoutAsync_EmptySessionId_Throws()
    {
        await SeedAdminAsync();

        await Assert.ThrowsAsync<ArgumentException>(() =>
            _authService.LogoutAsync(Guid.Empty, _adminUserId));
    }

    [Fact]
    public async Task LogoutAsync_EmptyUserId_Throws()
    {
        await SeedAdminAsync();
        var sessionId = await LoginAndGetSessionIdAsync();

        await Assert.ThrowsAsync<ArgumentException>(() =>
            _authService.LogoutAsync(sessionId, Guid.Empty));
    }
}
