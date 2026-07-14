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

        _authService = new AuthService(_dbContext, _passwordHasher, jwtIssuer);
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
}
