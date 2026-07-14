using System;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Api;

// A real, in-process ASP.NET Core test host (TestServer) for full HTTP-endpoint integration
// tests (IP-01 §9/T-09) — not a mocked pipeline. Each fixture instance gets its own freshly
// migrated, empty SQL Server database, pre-migrated *before* the host is first created so the
// application's own startup Admin-bootstrap process (T-06) runs against an existing schema and
// provisions exactly one known Admin account for these tests to log in as.
//
// Configuration is supplied via real process environment variables rather than
// ConfigureWebHost's ConfigureAppConfiguration: for this minimal-hosting (top-level statement)
// Program.cs, an in-memory configuration source added that way was observed NOT to reliably
// take precedence over a developer's own local user-secrets (the test picked up the real local
// WeaponDetectionDev connection string). Environment variables are loaded by
// WebApplicationBuilder after user-secrets, so they deterministically override it.
public sealed class AuthLoginApiFactory : WebApplicationFactory<Program>
{
    public const string AdminIdentifier = "api-test-admin";
    public const string AdminPassword = "Correct-Horse-Battery-Staple-1";

    private readonly string _connectionString =
        $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionAuthApiTests_{Guid.NewGuid():N};" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    public AuthLoginApiFactory()
    {
        Environment.SetEnvironmentVariable("ConnectionStrings__DefaultConnection", _connectionString);
        Environment.SetEnvironmentVariable("BootstrapAdmin__CredentialIdentifier", AdminIdentifier);
        Environment.SetEnvironmentVariable("BootstrapAdmin__Password", AdminPassword);
        Environment.SetEnvironmentVariable("Jwt__Issuer", "test-issuer");
        Environment.SetEnvironmentVariable("Jwt__Audience", "test-audience");
        Environment.SetEnvironmentVariable("Jwt__SigningKey", new string('k', 32));
        Environment.SetEnvironmentVariable("Jwt__AccessTokenLifetimeMinutes", "60");

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

    private bool _cleanedUp;

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
