using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Startup;
using Xunit;

namespace WeaponDetection.IntegrationTests.Startup;

// Verifies AdminBootstrapper's persistence, uniqueness, and idempotency behavior against a real
// SQL Server database (IP-01 §9) — each test gets its own freshly migrated, empty database (not
// a shared IClassFixture) because these scenarios specifically depend on an empty AdminUsers
// table at the start of the test.
//
// No test in this file prints a plaintext password, generated hash, or logged message
// containing either to console/log output.
public class AdminBootstrapperTests : IDisposable
{
    private const string TestPassword = "Correct-Horse-Battery-Staple-1";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IPasswordHasher _passwordHasher = new Pbkdf2PasswordHasher();

    public AdminBootstrapperTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionBootstrapTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private AdminBootstrapper CreateBootstrapper(
        string? credentialIdentifier,
        string? password,
        ILogger<AdminBootstrapper>? logger = null,
        bool includeCredentialIdentifierKey = true,
        bool includePasswordKey = true)
    {
        var configData = new Dictionary<string, string?>();
        if (includeCredentialIdentifierKey)
        {
            configData["BootstrapAdmin:CredentialIdentifier"] = credentialIdentifier;
        }

        if (includePasswordKey)
        {
            configData["BootstrapAdmin:Password"] = password;
        }

        var configuration = new ConfigurationBuilder().AddInMemoryCollection(configData).Build();

        return new AdminBootstrapper(
            _dbContext,
            _passwordHasher,
            configuration,
            logger ?? NullLogger<AdminBootstrapper>.Instance);
    }

    [Fact]
    public async Task BootstrapAsync_EmptyDatabaseValidConfig_CreatesExactlyOneAdmin()
    {
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";
        var bootstrapper = CreateBootstrapper(credentialIdentifier, TestPassword);

        await bootstrapper.BootstrapAsync();

        var admins = await _dbContext.AdminUsers.ToListAsync();
        Assert.Single(admins);
        Assert.Equal(credentialIdentifier, admins[0].CredentialIdentifier);
    }

    [Fact]
    public async Task BootstrapAsync_EmptyDatabaseValidConfig_StoredPasswordIsHashedNotPlaintext()
    {
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";
        var bootstrapper = CreateBootstrapper(credentialIdentifier, TestPassword);

        await bootstrapper.BootstrapAsync();

        var admin = await _dbContext.AdminUsers.SingleAsync();
        Assert.NotEqual(TestPassword, admin.PasswordHash);
    }

    [Fact]
    public async Task BootstrapAsync_EmptyDatabaseValidConfig_StoredHashVerifiesWithPasswordHasher()
    {
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";
        var bootstrapper = CreateBootstrapper(credentialIdentifier, TestPassword);

        await bootstrapper.BootstrapAsync();

        var admin = await _dbContext.AdminUsers.SingleAsync();
        var result = _passwordHasher.Verify(TestPassword, admin.PasswordHash);
        Assert.Equal(PasswordVerificationResult.Success, result);
    }

    [Fact]
    public async Task BootstrapAsync_CalledTwice_DoesNotCreateSecondAdmin()
    {
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";
        var bootstrapper = CreateBootstrapper(credentialIdentifier, TestPassword);

        await bootstrapper.BootstrapAsync();
        await bootstrapper.BootstrapAsync();

        var admins = await _dbContext.AdminUsers.ToListAsync();
        Assert.Single(admins);
    }

    [Fact]
    public async Task BootstrapAsync_ExistingAdmin_IsNotModified()
    {
        var existingIdentifier = $"admin-{Guid.NewGuid()}";
        var existingHash = _passwordHasher.Hash("original-password");
        var existingAdmin = new AdminUser(existingIdentifier, existingHash);
        _dbContext.AdminUsers.Add(existingAdmin);
        await _dbContext.SaveChangesAsync();

        var bootstrapper = CreateBootstrapper($"different-{Guid.NewGuid()}", "a-different-password");
        await bootstrapper.BootstrapAsync();

        var admins = await _dbContext.AdminUsers.ToListAsync();
        var admin = Assert.Single(admins);
        Assert.Equal(existingIdentifier, admin.CredentialIdentifier);
        Assert.Equal(existingHash, admin.PasswordHash);
    }

    [Fact]
    public async Task BootstrapAsync_ExistingAdmin_AllowsStartupWithoutBootstrapPassword()
    {
        var existingAdmin = new AdminUser($"admin-{Guid.NewGuid()}", _passwordHasher.Hash("original-password"));
        _dbContext.AdminUsers.Add(existingAdmin);
        await _dbContext.SaveChangesAsync();

        var bootstrapper = CreateBootstrapper(
            credentialIdentifier: null,
            password: null,
            includeCredentialIdentifierKey: false,
            includePasswordKey: false);

        var exception = await Record.ExceptionAsync(() => bootstrapper.BootstrapAsync());

        Assert.Null(exception);
    }

    [Fact]
    public async Task BootstrapAsync_EmptyDatabaseMissingCredentialIdentifier_ThrowsAndCreatesNoAdmin()
    {
        var bootstrapper = CreateBootstrapper(
            credentialIdentifier: null,
            password: TestPassword,
            includeCredentialIdentifierKey: false);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());

        Assert.Contains("BootstrapAdmin:CredentialIdentifier", exception.Message);
        Assert.Empty(await _dbContext.AdminUsers.ToListAsync());
    }

    [Fact]
    public async Task BootstrapAsync_EmptyDatabaseMissingPassword_ThrowsAndCreatesNoAdmin()
    {
        var bootstrapper = CreateBootstrapper(
            credentialIdentifier: $"admin-{Guid.NewGuid()}",
            password: null,
            includePasswordKey: false);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());

        Assert.Contains("BootstrapAdmin:Password", exception.Message);
        Assert.Empty(await _dbContext.AdminUsers.ToListAsync());
    }

    [Theory]
    [InlineData("", "some-password")]
    [InlineData("   ", "some-password")]
    public async Task BootstrapAsync_BlankCredentialIdentifier_ThrowsAndCreatesNoAdmin(string identifier, string password)
    {
        var bootstrapper = CreateBootstrapper(identifier, password);

        await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());
        Assert.Empty(await _dbContext.AdminUsers.ToListAsync());
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task BootstrapAsync_BlankPassword_ThrowsAndCreatesNoAdmin(string password)
    {
        var bootstrapper = CreateBootstrapper($"admin-{Guid.NewGuid()}", password);

        await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());
        Assert.Empty(await _dbContext.AdminUsers.ToListAsync());
    }

    [Fact]
    public async Task BootstrapAsync_InvalidCredentialIdentifierExceedingSchemaLength_LeavesNoPartialAdminRecord()
    {
        // AdminUserConfiguration caps CredentialIdentifier at 256 characters (nvarchar(256));
        // a longer value is rejected by SQL Server at insert time, exercising the failure path.
        var tooLongIdentifier = new string('a', 300);
        var bootstrapper = CreateBootstrapper(tooLongIdentifier, TestPassword);

        await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());

        Assert.Empty(await _dbContext.AdminUsers.ToListAsync());
    }

    [Fact]
    public async Task BootstrapAsync_MissingConfiguration_ExceptionMessageDoesNotContainPlaintextPassword()
    {
        var bootstrapper = CreateBootstrapper(
            credentialIdentifier: $"admin-{Guid.NewGuid()}",
            password: null,
            includePasswordKey: false);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => bootstrapper.BootstrapAsync());

        Assert.DoesNotContain(TestPassword, exception.Message);
        Assert.DoesNotContain(TestPassword, exception.ToString());
    }

    [Fact]
    public async Task BootstrapAsync_SuccessfulCreation_LoggedMessagesDoNotContainPlaintextPasswordOrHash()
    {
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";
        var capturingLogger = new CapturingLogger<AdminBootstrapper>();
        var bootstrapper = CreateBootstrapper(credentialIdentifier, TestPassword, capturingLogger);

        await bootstrapper.BootstrapAsync();

        var admin = await _dbContext.AdminUsers.SingleAsync();
        Assert.DoesNotContain(capturingLogger.Messages, m => m.Contains(TestPassword));
        Assert.DoesNotContain(capturingLogger.Messages, m => m.Contains(admin.PasswordHash));
    }

    // Minimal test double capturing formatted log messages, to assert secrets never reach a
    // logger — deliberately not a mocking-library dependency for a single-purpose check.
    private class CapturingLogger<T> : ILogger<T>
    {
        public List<string> Messages { get; } = new();

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(
            LogLevel logLevel,
            EventId eventId,
            TState state,
            Exception? exception,
            Func<TState, Exception?, string> formatter)
        {
            Messages.Add(formatter(state, exception));
        }
    }
}
