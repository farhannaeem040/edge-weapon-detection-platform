using System;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Api;

// A real, in-process ASP.NET Core test host (TestServer) for full HTTP-endpoint integration
// tests (IP-01 §9) — not a mocked pipeline. Each fixture instance gets its own freshly migrated,
// empty SQL Server database, pre-migrated *before* the host is first created so the application's
// own startup Admin-bootstrap process (T-06) runs against an existing schema and provisions
// exactly one known Admin account for these tests to log in as.
//
// Configuration is supplied via real process environment variables rather than
// ConfigureWebHost's ConfigureAppConfiguration: for this minimal-hosting (top-level statement)
// Program.cs, an in-memory configuration source added that way was observed NOT to reliably
// take precedence over a developer's own local user-secrets (the test picked up the real local
// WeaponDetectionDev connection string). Environment variables are loaded by
// WebApplicationBuilder after user-secrets, so they deterministically override it.
//
// Because those environment variables are process-wide, no two hosts derived from this factory
// may be alive at once. Every test class that uses one is therefore placed in the single
// ApiHostCollection, whose classes xUnit runs sequentially.
public abstract class SqlServerApiHostFactory : WebApplicationFactory<Program>
{
    public const string AdminIdentifier = "api-test-admin";
    public const string AdminPassword = "Correct-Horse-Battery-Staple-1";

    // Mirrored by the environment variables below, and reused by tests that need to mint a token
    // the running host will accept (or deliberately reject).
    public const string JwtIssuer = "test-issuer";
    public const string JwtAudience = "test-audience";
    public const int AccessTokenLifetimeMinutes = 60;
    public static readonly string JwtSigningKey = new('k', 32);

    private readonly string _connectionString;
    private bool _cleanedUp;

    protected SqlServerApiHostFactory(string databaseNamePrefix)
    {
        _connectionString =
            $"Server=localhost\\SQLEXPRESS;Database={databaseNamePrefix}_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        Environment.SetEnvironmentVariable("ConnectionStrings__DefaultConnection", _connectionString);
        Environment.SetEnvironmentVariable("BootstrapAdmin__CredentialIdentifier", AdminIdentifier);
        Environment.SetEnvironmentVariable("BootstrapAdmin__Password", AdminPassword);
        Environment.SetEnvironmentVariable("Jwt__Issuer", JwtIssuer);
        Environment.SetEnvironmentVariable("Jwt__Audience", JwtAudience);
        Environment.SetEnvironmentVariable("Jwt__SigningKey", JwtSigningKey);
        Environment.SetEnvironmentVariable(
            "Jwt__AccessTokenLifetimeMinutes", AccessTokenLifetimeMinutes.ToString());

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_connectionString)
            .Options;

        using var dbContext = new WeaponDetectionDbContext(options);
        dbContext.Database.Migrate();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Development");
    }

    public WeaponDetectionDbContext CreateDbContext()
    {
        var scope = Services.CreateScope();
        return scope.ServiceProvider.GetRequiredService<WeaponDetectionDbContext>();
    }

    // WebApplicationFactory's own Dispose()/DisposeAsync() bridging invokes this override more
    // than once; the guard makes cleanup idempotent so the second call doesn't hit an already
    // disposed IServiceProvider.
    protected override void Dispose(bool disposing)
    {
        if (!_cleanedUp)
        {
            _cleanedUp = true;

            using (var scope = Services.CreateScope())
            {
                var dbContext = scope.ServiceProvider.GetRequiredService<WeaponDetectionDbContext>();
                dbContext.Database.EnsureDeleted();
            }

            Environment.SetEnvironmentVariable("ConnectionStrings__DefaultConnection", null);
            Environment.SetEnvironmentVariable("BootstrapAdmin__CredentialIdentifier", null);
            Environment.SetEnvironmentVariable("BootstrapAdmin__Password", null);
            Environment.SetEnvironmentVariable("Jwt__Issuer", null);
            Environment.SetEnvironmentVariable("Jwt__Audience", null);
            Environment.SetEnvironmentVariable("Jwt__SigningKey", null);
            Environment.SetEnvironmentVariable("Jwt__AccessTokenLifetimeMinutes", null);
        }

        base.Dispose(disposing);
    }
}

// Groups every test class that spins up a SqlServerApiHostFactory. xUnit runs the classes of a
// single collection sequentially, which is what keeps the process-wide environment variables
// above from being clobbered by a concurrently constructed second host.
[CollectionDefinition(ApiHostCollection.Name)]
public sealed class ApiHostCollection
{
    public const string Name = "In-process API host";
}
