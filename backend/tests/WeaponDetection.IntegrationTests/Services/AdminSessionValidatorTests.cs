using System;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies the session half of the two-check authentication rule (FS-01 §5.3, §11; IP-01 T-10) at
// the service level, against a real SQL Server database (IP-01 §9) — the AdminSession row's own
// foreign key to AdminUser and its DateTimeOffset expiry comparison are relational behaviour that
// EF Core InMemory would not faithfully reproduce. Each test gets its own freshly migrated,
// empty database.
public class AdminSessionValidatorTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly AdminSessionValidator _validator;
    private readonly Guid _adminUserId;

    public AdminSessionValidatorTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionSessionValidatorTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        var admin = new AdminUser($"admin-{Guid.NewGuid()}", "irrelevant-hash-for-this-test");
        _dbContext.AdminUsers.Add(admin);
        _dbContext.SaveChanges();
        _adminUserId = admin.UserId;

        _validator = new AdminSessionValidator(_dbContext, TimeProvider.System);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private async Task<Guid> SeedSessionAsync(
        Guid? userId = null,
        TimeSpan? issuedOffset = null,
        TimeSpan? expiryOffset = null,
        bool revoked = false)
    {
        var now = DateTimeOffset.UtcNow;
        var session = new AdminSession(
            Guid.NewGuid(),
            userId ?? _adminUserId,
            now.Add(issuedOffset ?? TimeSpan.FromMinutes(-1)),
            now.Add(expiryOffset ?? TimeSpan.FromMinutes(60)));

        if (revoked)
        {
            session.Revoke();
        }

        _dbContext.AdminSessions.Add(session);
        await _dbContext.SaveChangesAsync();

        return session.SessionId;
    }

    [Fact]
    public async Task IsSessionActiveAsync_ActiveSessionForTheSameUser_ReturnsTrue()
    {
        var sessionId = await SeedSessionAsync();

        Assert.True(await _validator.IsSessionActiveAsync(sessionId, _adminUserId));
    }

    [Fact]
    public async Task IsSessionActiveAsync_RevokedSession_ReturnsFalse()
    {
        var sessionId = await SeedSessionAsync(revoked: true);

        Assert.False(await _validator.IsSessionActiveAsync(sessionId, _adminUserId));
    }

    [Fact]
    public async Task IsSessionActiveAsync_ExpiredSession_ReturnsFalse()
    {
        // FS-01 §11 requires the *record* to be unexpired, not only the bearer token — this check
        // is deliberately independent of the JWT's own lifetime validation.
        var sessionId = await SeedSessionAsync(
            issuedOffset: TimeSpan.FromMinutes(-120),
            expiryOffset: TimeSpan.FromMinutes(-60));

        Assert.False(await _validator.IsSessionActiveAsync(sessionId, _adminUserId));
    }

    [Fact]
    public async Task IsSessionActiveAsync_NoSuchSession_ReturnsFalse()
    {
        await SeedSessionAsync();

        Assert.False(await _validator.IsSessionActiveAsync(Guid.NewGuid(), _adminUserId));
    }

    [Fact]
    public async Task IsSessionActiveAsync_SessionBelongingToAnotherUser_ReturnsFalse()
    {
        var sessionId = await SeedSessionAsync();

        Assert.False(await _validator.IsSessionActiveAsync(sessionId, Guid.NewGuid()));
    }

    [Fact]
    public async Task IsSessionActiveAsync_EmptyIdentifiers_ReturnFalseWithoutQuerying()
    {
        var sessionId = await SeedSessionAsync();

        Assert.False(await _validator.IsSessionActiveAsync(Guid.Empty, _adminUserId));
        Assert.False(await _validator.IsSessionActiveAsync(sessionId, Guid.Empty));
    }

    [Fact]
    public async Task IsSessionActiveAsync_DoesNotTrackTheSessionItValidates()
    {
        // The authorization check shares the request's scoped DbContext with the business logic
        // that runs afterwards (e.g. logout, T-11); it must not leave an AdminSession attached to
        // the change tracker as a side effect.
        var sessionId = await SeedSessionAsync();
        _dbContext.ChangeTracker.Clear();

        await _validator.IsSessionActiveAsync(sessionId, _adminUserId);

        Assert.Empty(_dbContext.ChangeTracker.Entries<AdminSession>());
    }
}
